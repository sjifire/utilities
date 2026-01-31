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

    def get_people(self, include_deleted: bool = False) -> list[ISpyFirePerson]:
        """Fetch all people from iSpyFire.

        Args:
            include_deleted: If True, include deleted/inactive people

        Returns:
            List of ISpyFirePerson objects
        """
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        url = f"{self.base_url}/api/ddui/people"
        if include_deleted:
            url += "?includeDeleted=true"

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

    def logout_mobile_devices(self, person_id: str) -> bool:
        """Logout all mobile devices for a person.

        Args:
            person_id: ID of person to logout devices for

        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        # iSpyFire uses GET for this endpoint
        url = f"{self.base_url}/api/mobile/clearalluserdevices/{person_id}"
        response = self.client.get(url)

        if response.status_code != 200:
            logger.warning(f"Failed to logout mobile devices: {response.status_code}")
            return False

        logger.info(f"Logged out mobile devices for person {person_id}")
        return True

    def deactivate_person(self, person_id: str, logout_devices: bool = True) -> bool:
        """Deactivate a person in iSpyFire.

        Sets both isActive and isLoginActive to False.

        Args:
            person_id: ID of person to deactivate
            logout_devices: If True, logout mobile devices first (default: True)

        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            raise RuntimeError("Client must be used as context manager")

        # First logout mobile devices if requested
        if logout_devices:
            self.logout_mobile_devices(person_id)

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
