"""iSpyFire API client."""

import logging
from typing import Self

import httpx

from sjifire.core.config import get_ispyfire_credentials
from sjifire.ispyfire.models import ISpyFirePerson

logger = logging.getLogger(__name__)


class ISpyFireClient:
    """Client for iSpyFire API."""

    def __init__(self) -> None:
        """Initialize the client with credentials from environment."""
        self.base_url, self.username, self.password = get_ispyfire_credentials()
        self.client: httpx.Client | None = None

    def __enter__(self) -> Self:
        """Enter context manager - create HTTP client and login."""
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
        )
        self._login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager - close HTTP client."""
        if self.client:
            self.client.close()
            self.client = None

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        url = f"{self.base_url}/api/ddui/people"
        params = []
        if include_inactive:
            params.append("includeInactive=true")
        if include_deleted:
            params.append("includeDeleted=true")
        if params:
            url += "?" + "&".join(params)

        logger.info(f"Fetching people from {url}")
        response = self.client.get(url)

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        url = f"{self.base_url}/api/ddui/people/email/{email}"
        response = self.client.get(url)

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        url = f"{self.base_url}/api/ddui/people"
        response = self.client.put(
            url,
            json=person.to_api(),
            headers={"Content-Type": "application/json"},
        )

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        if not person.id:
            logger.error("Cannot update person without ID")
            return None

        url = f"{self.base_url}/api/ddui/people/{person.id}"
        response = self.client.put(
            url,
            json=person.to_api(),
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            logger.error(f"Failed to update person: {response.status_code}")
            return None

        data = response.json()
        if data.get("results"):
            return ISpyFirePerson.from_api(data["results"][0])
        return None

    def _get_ispyid(self) -> str:
        """Extract ispyid from base URL (e.g., sjf3 from https://sjf3.ispyfire.com)."""
        import re

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        success = True

        # Deactivate iOS push registrations
        url = f"{self.base_url}/api/ddui/iosregids/user/{email}"
        response = self.client.put(
            url,
            json={"isActive": False},
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            logger.warning(f"Failed to deactivate iOS push: {response.status_code}")
            success = False

        # Deactivate GCM push registrations
        url = f"{self.base_url}/api/ddui/gcmregids/user/{email}"
        response = self.client.put(
            url,
            json={"isActive": False},
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            logger.warning(f"Failed to deactivate GCM push: {response.status_code}")
            success = False

        # Clear iSpyFire notifications
        ispyid = self._get_ispyid()
        if ispyid:
            url = f"{self.base_url}/api/mobile/clearallispyidnotifications/{email}/{ispyid}"
            response = self.client.get(url)
            if response.status_code != 200:
                logger.warning(f"Failed to clear notifications: {response.status_code}")
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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        url = f"{self.base_url}/api/mobile/clearalluserdevices/{email}"
        response = self.client.get(url)

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        # This endpoint accepts both ID and email
        url = f"{self.base_url}/api/mobile/clearalluserdevices/{person_id}"
        response = self.client.get(url)

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        url = f"{self.base_url}/api/login/passinvite/{email}"
        response = self.client.put(
            url,
            json={"usernamePF": email},
            headers={"Content-Type": "application/json"},
        )

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        # First logout devices if requested and email provided
        if logout_devices and email:
            # Step 1: Logout push notifications (deactivate iOS/GCM registrations)
            self.logout_push_notifications(email)
            # Step 2: Remove all device registrations
            self.remove_all_devices(email)

        # Then set both isActive and isLoginActive to False
        url = f"{self.base_url}/api/ddui/people/{person_id}"
        response = self.client.put(
            url,
            json={"isActive": False, "isLoginActive": False},
            headers={"Content-Type": "application/json"},
        )

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
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        # Set both isActive and isLoginActive to True
        url = f"{self.base_url}/api/ddui/people/{person_id}"
        response = self.client.put(
            url,
            json={"isActive": True, "isLoginActive": True},
            headers={"Content-Type": "application/json"},
        )

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
