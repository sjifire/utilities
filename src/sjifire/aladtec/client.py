"""Base Aladtec HTTP client with authentication."""

import logging
import os
from typing import Self

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def get_aladtec_credentials() -> tuple[str, str, str]:
    """Get Aladtec credentials from environment.

    Returns:
        Tuple of (url, username, password)

    Raises:
        ValueError: If any required credential is not set
    """
    load_dotenv()

    url = os.getenv("ALADTEC_URL")
    username = os.getenv("ALADTEC_USERNAME")
    password = os.getenv("ALADTEC_PASSWORD")

    if not url or not username or not password:
        raise ValueError(
            "Aladtec credentials not set. Required: ALADTEC_URL, ALADTEC_USERNAME, ALADTEC_PASSWORD"
        )

    return url, username, password


class AladtecClient:
    """Base HTTP client for Aladtec with login functionality.

    This class handles session management and authentication for all
    Aladtec scrapers. Use as a context manager to ensure proper cleanup.

    Example:
        with AladtecClient() as client:
            if client.login():
                # Make authenticated requests via client.client
                response = client.client.get(...)
    """

    def __init__(self, timeout: float = 60.0) -> None:
        """Initialize the client with credentials from environment.

        Args:
            timeout: HTTP request timeout in seconds (default 60)
        """
        self.base_url, self.username, self.password = get_aladtec_credentials()
        self._timeout = timeout
        self.client: httpx.Client | None = None
        self._request_count = 0

    def __enter__(self) -> Self:
        """Enter context manager - create HTTP client."""
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=self._timeout,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager - close HTTP client and log request count."""
        if self.client:
            if self._request_count > 0:
                logger.info(f"Aladtec API calls: {self._request_count}")
            self.client.close()
            self.client = None

    @property
    def request_count(self) -> int:
        """Get the number of HTTP requests made to Aladtec."""
        return self._request_count

    def get(self, url: str, **kwargs) -> httpx.Response:
        """Make a GET request and track the call count."""
        client = self._require_client()
        self._request_count += 1
        return client.get(url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        """Make a POST request and track the call count."""
        client = self._require_client()
        self._request_count += 1
        return client.post(url, **kwargs)

    def _require_client(self) -> httpx.Client:
        """Get HTTP client or raise if not in context manager.

        Returns:
            The HTTP client

        Raises:
            RuntimeError: If not used as context manager
        """
        if not self.client:
            raise RuntimeError("Client must be used as context manager")
        return self.client

    def login(self) -> bool:
        """Log in to Aladtec.

        Returns:
            True if login successful, False otherwise
        """
        logger.info(f"Logging in to {self.base_url}")

        # Get the login page first to establish session
        response = self.get(f"{self.base_url}/")

        if response.status_code != 200:
            logger.error(f"Failed to load login page: {response.status_code}")
            return False

        form_data = {
            "username": self.username,
            "password": self.password,
        }

        # Submit login
        login_url = f"{self.base_url}/index.php?action=login"
        response = self.post(login_url, data=form_data)

        # Check if login succeeded - look for dashboard elements or schedule
        if "schedule" in response.text.lower() or "dashboard" in response.text.lower():
            logger.info("Login successful")
            return True

        # Check for error messages
        if "invalid" in response.text.lower() or "incorrect" in response.text.lower():
            logger.error("Login failed - invalid credentials")
            return False

        # Check URL - successful login usually goes to schedule or home
        if "action=login" not in str(response.url):
            logger.info("Login successful")
            return True

        logger.error("Login failed - still on login page")
        return False
