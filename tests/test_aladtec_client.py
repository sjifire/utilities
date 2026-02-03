"""Tests for sjifire.aladtec.client module."""

import pytest

from sjifire.aladtec.client import AladtecClient


class TestAladtecClient:
    """Tests for AladtecClient base class."""

    def test_context_manager_creates_http_client(self, mock_env_vars):
        """Context manager creates HTTP client."""
        with AladtecClient() as client:
            assert client.http is not None

    def test_context_manager_closes_http_client(self, mock_env_vars):
        """Context manager closes HTTP client."""
        client = AladtecClient()
        with client:
            pass
        assert client.http is None

    def test_require_client_raises_outside_context(self, mock_env_vars):
        """_require_client raises outside context manager."""
        client = AladtecClient()
        with pytest.raises(RuntimeError, match="must be used as context manager"):
            client._require_client()

    def test_require_client_returns_http_inside_context(self, mock_env_vars):
        """_require_client returns HTTP client inside context manager."""
        with AladtecClient() as client:
            http = client._require_client()
            assert http is client.http

    def test_login_requires_context_manager(self, mock_env_vars):
        """login() requires context manager."""
        client = AladtecClient()
        with pytest.raises(RuntimeError, match="must be used as context manager"):
            client.login()

    def test_custom_timeout(self, mock_env_vars):
        """Custom timeout is passed to HTTP client."""
        with AladtecClient(timeout=120.0) as client:
            assert client._timeout == 120.0

    def test_credentials_loaded(self, mock_env_vars):
        """Credentials are loaded from environment."""
        client = AladtecClient()
        assert client.base_url == "https://test.aladtec.com"
        assert client.username == "testuser"
        assert client.password == "testpass"
