"""iSpyFire API client."""

import json
import logging
import os
import re
import time
from typing import Self

import httpx
from dotenv import load_dotenv
from tenacity import (
    RetryError,
    retry,
    retry_if_result,
    stop_after_attempt,
    wait_exponential_jitter,
)

from sjifire.ispyfire.models import CallSummary, DispatchCall, ISpyFirePerson


def get_ispyfire_credentials() -> tuple[str, str, str]:
    """Get iSpyFire credentials from environment.

    Returns:
        Tuple of (url, username, password)

    Raises:
        ValueError: If any required credential is not set
    """
    load_dotenv()

    url = os.getenv("ISPYFIRE_URL")
    username = os.getenv("ISPYFIRE_USERNAME")
    password = os.getenv("ISPYFIRE_PASSWORD")

    if not url or not username or not password:
        raise ValueError(
            "iSpyFire credentials not set. "
            "Required: ISPYFIRE_URL, ISPYFIRE_USERNAME, ISPYFIRE_PASSWORD"
        )

    return url, username, password


logger = logging.getLogger(__name__)

# Rate limiting configuration
MAX_RETRIES = 5
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 30
BULK_OPERATION_DELAY = 0.2  # 200ms delay between bulk operations


def _is_rate_limited(response: httpx.Response) -> bool:
    """Check if response indicates rate limiting (429)."""
    return response.status_code == 429


def _log_retry(retry_state) -> None:
    """Log retry attempts."""
    if retry_state.attempt_number > 1:
        logger.warning(f"Retry attempt {retry_state.attempt_number} after rate limiting")


class ISpyFireClient:
    """Client for iSpyFire API."""

    CENTRAL_API_BASE = "https://api.ispyfire.com"

    def __init__(self) -> None:
        """Initialize the client with credentials from environment."""
        self.base_url, self.username, self.password = get_ispyfire_credentials()
        self.client: httpx.Client | None = None
        self.central_client: httpx.Client | None = None
        self.bearer: str | None = None
        self.ispyid: str | None = None
        self.leadispyid: str | None = None
        self.person_id: str | None = None

    def __enter__(self) -> Self:
        """Enter context manager - create HTTP client and login."""
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
        )
        self._login()
        self._login_central_api()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager - close HTTP clients."""
        if self.central_client:
            self.central_client.close()
            self.central_client = None
        if self.client:
            self.client.close()
            self.client = None

    @retry(
        retry=retry_if_result(_is_rate_limited),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential_jitter(initial=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an HTTP request with proactive delay and retry logic.

        All API calls should go through this method to ensure consistent
        rate limiting behavior:
        1. Proactive delay before each request to avoid hitting limits
        2. Exponential backoff retry on 429 responses

        Args:
            method: HTTP method (GET, POST, PUT, etc.)
            url: URL to request
            **kwargs: Additional arguments passed to httpx

        Returns:
            Response object
        """
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        # Proactive delay to avoid rate limiting
        time.sleep(BULK_OPERATION_DELAY)

        response = self.client.request(method, url, **kwargs)

        if response.status_code == 429:
            logger.warning(f"Rate limited (429) on {method} {url}")

        return response

    def _login(self) -> bool:
        """Log in to iSpyFire.

        Returns:
            True if login successful, False otherwise
        """
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        logger.info(f"Logging in to {self.base_url}")

        response = self.client.post(
            f"{self.base_url}/login",
            data={
                "username": self.username,
                "password": self.password,
            },
        )

        if response.status_code != 200:
            logger.error(f"Login failed: {response.status_code}")
            return False

        logger.info("Login successful")
        return True

    def _login_central_api(self) -> bool:
        """Authenticate with the central API (api.ispyfire.com).

        Performs a separate non-redirect login to get the session IDs
        from the HTML response, then authenticates with the central API.

        Returns:
            True if login successful, False otherwise
        """
        # Do a separate non-redirect login to get the HTML with session IDs.
        # The main client follows redirects, so the login HTML is lost.
        try:
            no_redirect = httpx.Client(follow_redirects=False, timeout=30.0)
            response = no_redirect.post(
                f"{self.base_url}/login",
                data={"username": self.username, "password": self.password},
            )
            html = response.text
            no_redirect.close()
        except httpx.HTTPError:
            logger.warning("Failed to perform non-redirect login for central API")
            return False

        # Parse session identifiers from login page JavaScript
        # HTML looks like: window.localStorage.setItem('currentLIPID', 'token...');
        pid_match = re.search(r"setItem\('currentLIPID',\s*'([^']+)'\)", html)
        aid_match = re.search(r"setItem\('currentLIAID',\s*'([^']+)'\)", html)
        uid_match = re.search(r"setItem\('currentLIUserID',\s*'([^']+)'\)", html)

        if not pid_match or not aid_match or not uid_match:
            logger.warning("Could not parse session IDs from login HTML")
            return False

        pid = pid_match.group(1)  # Password/token for central API
        agency = aid_match.group(1)  # e.g. "sjf3"
        user_id = uid_match.group(1)  # e.g. "svc-automations@sjifire.org"

        logger.debug(f"Central API auth: agency={agency}, user={user_id}")

        # Authenticate with central API using PID as password
        self.central_client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
        )

        login_url = f"{self.CENTRAL_API_BASE}/{agency}/session/login/{user_id}"
        try:
            response = self.central_client.put(
                login_url,
                content=json.dumps({"agency": agency, "pass": pid}),
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError:
            logger.warning("Failed to connect to central API")
            return False

        if response.status_code != 200:
            logger.warning(f"Central API login failed: {response.status_code}")
            return False

        data = response.json()
        self.bearer = data.get("bearer")
        if not self.bearer:
            logger.warning("No bearer token in central API response")
            return False

        self.person_id = data.get("personid")

        # Get CAD settings to find ispyid and leadispyid
        self._get_cad_settings(agency)

        logger.info("Central API login successful")
        return True

    def _get_cad_settings(self, agency: str) -> None:
        """Fetch CAD settings to get ispyid and leadispyid.

        Args:
            agency: Agency identifier (e.g. "sjf3")
        """
        if not self.client:
            return

        url = f"{self.base_url}/api/cad/settings/{agency}"
        try:
            response = self.client.get(url)
        except httpx.HTTPError:
            logger.warning("Failed to fetch CAD settings")
            return

        if response.status_code != 200:
            logger.warning(f"CAD settings request failed: {response.status_code}")
            return

        data = response.json()
        results = data.get("results", [])
        if results:
            settings = results[0]
            self.ispyid = settings.get("ispyid")
            self.leadispyid = settings.get("leadispyid")
            logger.debug(f"CAD settings: ispyid={self.ispyid}, leadispyid={self.leadispyid}")

    def _central_request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response | None:
        """Make an HTTP request to the central API with bearer auth.

        Args:
            method: HTTP method
            url: URL to request
            **kwargs: Additional arguments passed to httpx

        Returns:
            Response object, or None if central API is not available
        """
        if not self.central_client or not self.bearer:
            logger.warning("Central API not authenticated")
            return None

        headers = kwargs.pop("headers", {})
        headers["X-ISPY-Bearer"] = self.bearer
        kwargs["headers"] = headers

        time.sleep(BULK_OPERATION_DELAY)

        try:
            return self.central_client.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            logger.error(f"Central API request failed: {e}")
            return None

    def get_people(
        self, include_inactive: bool = False, include_deleted: bool = False
    ) -> list[ISpyFirePerson]:
        """Fetch all people from iSpyFire.

        Args:
            include_inactive: If True, include inactive people
            include_deleted: If True, include deleted people

        Returns:
            List of ISpyFirePerson objects
        """
        url = f"{self.base_url}/api/ddui/people"
        params = []
        if include_inactive:
            params.append("includeInactive=true")
        if include_deleted:
            params.append("includeDeleted=true")
        if params:
            url += "?" + "&".join(params)

        logger.info(f"Fetching people from {url}")
        try:
            response = self._request("GET", url)
        except RetryError:
            logger.error("Failed to fetch people after max retries")
            return []

        if response.status_code != 200:
            logger.error(f"Failed to fetch people: {response.status_code}")
            return []

        data = response.json()
        people = [ISpyFirePerson.from_api(p) for p in data.get("results", [])]
        logger.info(f"Fetched {len(people)} people from iSpyFire")
        return people

    def get_person_by_email(self, email: str) -> ISpyFirePerson | None:
        """Fetch a person by email address.

        Args:
            email: Email address to search for

        Returns:
            ISpyFirePerson if found, None otherwise
        """
        url = f"{self.base_url}/api/ddui/people/email/{email}"
        try:
            response = self._request("GET", url)
        except RetryError:
            logger.error(f"Failed to fetch person by email after max retries: {email}")
            return None

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            logger.error(f"Failed to fetch person by email: {response.status_code}")
            return None

        data = response.json()
        if data.get("results"):
            return ISpyFirePerson.from_api(data["results"][0])
        return None

    def create_person(self, person: ISpyFirePerson) -> ISpyFirePerson | None:
        """Create a new person in iSpyFire.

        Args:
            person: Person data to create

        Returns:
            Created person with ID, or None if failed
        """
        url = f"{self.base_url}/api/ddui/people"
        try:
            response = self._request(
                "PUT",
                url,
                json=person.to_api(),
                headers={"Content-Type": "application/json"},
            )
        except RetryError:
            logger.error("Failed to create person after max retries")
            return None

        if response.status_code not in (200, 201):
            logger.error(f"Failed to create person: {response.status_code}")
            return None

        data = response.json()
        if data.get("results"):
            return ISpyFirePerson.from_api(data["results"][0])
        return None

    def update_person(self, person: ISpyFirePerson) -> ISpyFirePerson | None:
        """Update an existing person in iSpyFire.

        Args:
            person: Person data to update (must have id set)

        Returns:
            Updated person, or None if failed
        """
        if not person.id:
            logger.error("Cannot update person without ID")
            return None

        url = f"{self.base_url}/api/ddui/people/{person.id}"
        try:
            response = self._request(
                "PUT",
                url,
                json=person.to_api(),
                headers={"Content-Type": "application/json"},
            )
        except RetryError:
            logger.error(f"Failed to update person after max retries: {person.id}")
            return None

        if response.status_code != 200:
            logger.error(f"Failed to update person: {response.status_code}")
            return None

        data = response.json()
        if data.get("results"):
            return ISpyFirePerson.from_api(data["results"][0])
        return None

    def _get_ispyid(self) -> str:
        """Extract ispyid from base URL (e.g., sjf3 from https://sjf3.ispyfire.com)."""
        match = re.search(r"https://([^.]+)\.ispyfire\.com", self.base_url)
        if match:
            return match.group(1).lower()
        return ""

    def logout_push_notifications(self, email: str) -> bool:
        """Logout/deactivate push notifications for a person.

        This deactivates iOS and GCM push registrations but doesn't remove
        the device registrations. Call this BEFORE remove_all_devices.

        Args:
            email: Email of person to logout push for

        Returns:
            True if successful, False otherwise
        """
        success = True

        # Deactivate iOS push registrations
        url = f"{self.base_url}/api/ddui/iosregids/user/{email}"
        try:
            response = self._request(
                "PUT",
                url,
                json={"isActive": False},
                headers={"Content-Type": "application/json"},
            )
            if response.status_code != 200:
                logger.warning(f"Failed to deactivate iOS push: {response.status_code}")
                success = False
        except RetryError:
            logger.warning("Failed to deactivate iOS push after max retries")
            success = False

        # Deactivate GCM push registrations
        url = f"{self.base_url}/api/ddui/gcmregids/user/{email}"
        try:
            response = self._request(
                "PUT",
                url,
                json={"isActive": False},
                headers={"Content-Type": "application/json"},
            )
            if response.status_code != 200:
                logger.warning(f"Failed to deactivate GCM push: {response.status_code}")
                success = False
        except RetryError:
            logger.warning("Failed to deactivate GCM push after max retries")
            success = False

        # Clear iSpyFire notifications
        ispyid = self._get_ispyid()
        if ispyid:
            url = f"{self.base_url}/api/mobile/clearallispyidnotifications/{email}/{ispyid}"
            try:
                response = self._request("GET", url)
                if response.status_code != 200:
                    logger.warning(f"Failed to clear notifications: {response.status_code}")
                    success = False
            except RetryError:
                logger.warning("Failed to clear notifications after max retries")
                success = False

        if success:
            logger.info(f"Logged out push notifications for {email}")
        return success

    def remove_all_devices(self, email: str) -> bool:
        """Remove all registered devices for a person.

        This clears all device registrations. Call this AFTER
        logout_push_notifications.

        Args:
            email: Email of person to remove devices for

        Returns:
            True if successful, False otherwise
        """
        url = f"{self.base_url}/api/mobile/clearalluserdevices/{email}"
        try:
            response = self._request("GET", url)
        except RetryError:
            logger.warning("Failed to remove devices after max retries")
            return False

        if response.status_code != 200:
            logger.warning(f"Failed to remove devices: {response.status_code}")
            return False

        logger.info(f"Removed all devices for {email}")
        return True

    def logout_mobile_devices(self, person_id: str) -> bool:
        """Logout all mobile devices for a person.

        DEPRECATED: Use logout_push_notifications + remove_all_devices instead.

        Args:
            person_id: ID of person to logout devices for

        Returns:
            True if successful, False otherwise
        """
        # This endpoint accepts both ID and email
        url = f"{self.base_url}/api/mobile/clearalluserdevices/{person_id}"
        try:
            response = self._request("GET", url)
        except RetryError:
            logger.warning("Failed to logout mobile devices after max retries")
            return False

        if response.status_code != 200:
            logger.warning(f"Failed to logout mobile devices: {response.status_code}")
            return False

        logger.info(f"Logged out mobile devices for person {person_id}")
        return True

    def send_invite_email(self, email: str) -> bool:
        """Send iSpyFire invite email to a person.

        This sends an email with instructions to set up their password.

        Args:
            email: Email address of the person

        Returns:
            True if successful, False otherwise
        """
        url = f"{self.base_url}/api/login/passinvite/{email}"
        try:
            response = self._request(
                "PUT",
                url,
                json={"usernamePF": email},
                headers={"Content-Type": "application/json"},
            )
        except RetryError:
            logger.error(f"Failed to send invite email after max retries: {email}")
            return False

        if response.status_code != 200:
            logger.warning(f"Failed to send invite email: {response.status_code}")
            return False

        logger.info(f"Sent invite email to {email}")
        return True

    def deactivate_person(
        self, person_id: str, email: str | None = None, logout_devices: bool = True
    ) -> bool:
        """Deactivate a person in iSpyFire.

        Sets both isActive and isLoginActive to False. If email is provided
        and logout_devices is True, also logs out push notifications and
        removes all device registrations.

        Args:
            person_id: ID of person to deactivate
            email: Email of person (required for full device logout)
            logout_devices: If True, logout devices first (default: True)

        Returns:
            True if successful, False otherwise
        """
        # First logout devices if requested and email provided
        if logout_devices and email:
            # Step 1: Logout push notifications (deactivate iOS/GCM registrations)
            self.logout_push_notifications(email)
            # Step 2: Remove all device registrations
            self.remove_all_devices(email)

        # Then set both isActive and isLoginActive to False
        url = f"{self.base_url}/api/ddui/people/{person_id}"
        try:
            response = self._request(
                "PUT",
                url,
                json={"isActive": False, "isLoginActive": False},
                headers={"Content-Type": "application/json"},
            )
        except RetryError:
            logger.error(f"Failed to deactivate person after max retries: {person_id}")
            return False

        if response.status_code != 200:
            logger.error(f"Failed to deactivate person: {response.status_code}")
            return False

        logger.info(f"Deactivated person {person_id}")
        return True

    def reactivate_person(self, person_id: str, email: str | None = None) -> bool:
        """Reactivate a person in iSpyFire.

        Sets both isActive and isLoginActive to True, and sends a
        password reset email so they can log in again.

        Args:
            person_id: ID of person to reactivate
            email: Email address for password reset (required for invite)

        Returns:
            True if successful, False otherwise
        """
        # Set both isActive and isLoginActive to True
        url = f"{self.base_url}/api/ddui/people/{person_id}"
        try:
            response = self._request(
                "PUT",
                url,
                json={"isActive": True, "isLoginActive": True},
                headers={"Content-Type": "application/json"},
            )
        except RetryError:
            logger.error(f"Failed to reactivate person after max retries: {person_id}")
            return False

        if response.status_code != 200:
            logger.error(f"Failed to reactivate person: {response.status_code}")
            return False

        logger.info(f"Reactivated person {person_id}")

        # Send password reset email if email provided
        if email:
            if self.send_invite_email(email):
                logger.info(f"Sent password reset email to {email}")
            else:
                logger.warning(f"Failed to send password reset email to {email}")

        return True

    def create_and_invite(self, person: ISpyFirePerson) -> ISpyFirePerson | None:
        """Create a new person and send them an invite email.

        This is the recommended way to add new users. It:
        1. Ensures isActive and isLoginActive are both True
        2. Creates the person in iSpyFire
        3. Sends an invite email so they can set their password

        Args:
            person: Person data to create

        Returns:
            Created person with ID, or None if failed
        """
        # Ensure both active flags are set
        person.set_active(True)

        # Create the person
        result = self.create_person(person)
        if not result:
            return None

        # Send invite email
        if person.email:
            if self.send_invite_email(person.email):
                logger.info(f"Sent invite email to {person.email}")
            else:
                logger.warning(f"Failed to send invite email to {person.email}")

        return result

    # ── Dispatch / Call methods (central API) ──────────────────────────

    def get_calls(self, days: int = 30) -> list[CallSummary]:
        """List recent calls from the central API.

        Args:
            days: Number of days to look back (7 or 30 only)

        Returns:
            List of CallSummary objects
        """
        if not self.ispyid:
            logger.error("ispyid not available - central API not initialized")
            return []

        if days not in (7, 30):
            logger.warning(f"Only 7 or 30 day windows supported, got {days}. Using 30.")
            days = 30

        url = f"{self.CENTRAL_API_BASE}/calls/me/{days}/{self.ispyid}/all"
        response = self._central_request("GET", url)
        if not response or response.status_code != 200:
            logger.error("Failed to fetch calls")
            return []

        data = response.json()
        return [CallSummary.from_api(c) for c in data.get("results", [])]

    def get_call_details(self, call_id: str) -> DispatchCall | None:
        """Get full details for a specific call.

        Args:
            call_id: The call's _id (UUID) or long_term_call_id (dispatch ID)

        Returns:
            DispatchCall if found, None otherwise
        """
        if not self.ispyid:
            logger.error("ispyid not available - central API not initialized")
            return None

        # If it looks like a dispatch ID (e.g. "26-001678"), search by listing
        if re.match(r"\d{2}-\d+", call_id):
            return self._get_call_by_dispatch_id(call_id)

        url = f"{self.CENTRAL_API_BASE}/calls/details/{self.ispyid}/id/{call_id}"
        response = self._central_request("GET", url)
        if not response or response.status_code != 200:
            logger.error(f"Failed to fetch call details: {call_id}")
            return None

        data = response.json()
        results = data.get("results", [])
        if not results:
            return None

        return DispatchCall.from_api(results[0])

    def _get_call_by_dispatch_id(self, dispatch_id: str) -> DispatchCall | None:
        """Find a call by its dispatch ID (e.g. '26-001678').

        Fetches the 30-day call list and looks up details for matching calls.

        Args:
            dispatch_id: The LongTermCallID to search for

        Returns:
            DispatchCall if found, None otherwise
        """
        summaries = self.get_calls(days=30)
        for summary in summaries:
            detail = self.get_call_details(summary.id)
            if detail and detail.long_term_call_id == dispatch_id:
                return detail
        return None

    def get_open_calls(self) -> list[DispatchCall]:
        """Get currently active/open calls.

        Returns:
            List of open DispatchCall objects
        """
        if not self.leadispyid:
            logger.error("leadispyid not available - central API not initialized")
            return []

        url = f"{self.CENTRAL_API_BASE}/calls/headers/{self.leadispyid}/open?skipunit=true"
        response = self._central_request("GET", url)
        if not response or response.status_code != 200:
            logger.error("Failed to fetch open calls")
            return []

        data = response.json()
        results = data.get("results", [])

        # Open call headers have a different shape than full details.
        # Fetch full details for each open call.
        calls = []
        for header in results:
            call_id = header.get("_id")
            if call_id:
                detail = self.get_call_details(call_id)
                if detail:
                    calls.append(detail)
        return calls

    def get_call_log(self, call_id: str) -> list[dict]:
        """Get audit log entries for a call (who viewed it, when).

        Args:
            call_id: The call's _id (UUID)

        Returns:
            List of log entry dicts with email, commenttype, timestamp
        """
        url = f"{self.CENTRAL_API_BASE}/logging/calldetails/callid/{call_id}"
        response = self._central_request("GET", url)
        if not response or response.status_code != 200:
            logger.error(f"Failed to fetch call log: {call_id}")
            return []

        data = response.json()
        return data.get("results", [])
