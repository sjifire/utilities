"""Base Aladtec HTTP client with authentication."""

import logging
from typing import Self

import httpx

from sjifire.core.config import get_aladtec_credentials

logger = logging.getLogger(__name__)


class AladtecClient:
    """Base HTTP client for Aladtec with login functionality.

    This class handles session management and authentication for all
    Aladtec scrapers. Use as a context manager to ensure proper cleanup.

    Example:
        with AladtecClient() as client:
            if client.login():
                # Make authenticated requests via client.http
                response = client.http.get(...)
    """

    def __init__(self, timeout: float = 60.0) -> None:
        """Initialize the client with credentials from environment.

        Args:
            timeout: HTTP request timeout in seconds (default 60)
        """
        self.base_url, self.username, self.password = get_aladtec_credentials()
        self._timeout = timeout
        self.http: httpx.Client | None = None

    def __enter__(self) -> Self:
        """Enter context manager - create HTTP client."""
        self.http = httpx.Client(
            follow_redirects=True,
            timeout=self._timeout,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager - close HTTP client."""
        if self.http:
            self.http.close()
            self.http = None

    def _require_client(self) -> httpx.Client:
        """Get HTTP client or raise if not in context manager.

        Returns:
            The HTTP client

        Raises:
            RuntimeError: If not used as context manager
        """
        if not self.http:
            raise RuntimeError("Client must be used as context manager")
        return self.http

    def login(self) -> bool:
        """Log in to Aladtec.

        Returns:
            True if login successful, False otherwise
        """
        client = self._require_client()

        logger.info(f"Logging in to {self.base_url}")

        # Get the login page first to establish session
        response = client.get(f"{self.base_url}/")

        if response.status_code != 200:
            logger.error(f"Failed to load login page: {response.status_code}")
            return False

        form_data = {
            "username": self.username,
            "password": self.password,
        }

        # Submit login
        login_url = f"{self.base_url}/index.php?action=login"
        response = client.post(login_url, data=form_data)

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
