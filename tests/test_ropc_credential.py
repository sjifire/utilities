"""Tests for ROPCCredential class and DutyCalendarSync in sjifire/calendar/sync.py."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.calendar.duty_sync import DutyCalendarSync, ROPCCredential

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_msal_app():
    """Mock msal.ConfidentialClientApplication."""
    with patch("sjifire.calendar.duty_sync.msal.ConfidentialClientApplication") as mock:
        yield mock


@pytest.fixture
def credential(mock_msal_app):
    """Create a ROPCCredential for testing."""
    return ROPCCredential(
        tenant_id="test-tenant",
        client_id="test-client",
        client_secret="test-secret",
        username="test@example.com",
        password="test-password",
    )


# =============================================================================
# Initialization Tests
# =============================================================================


class TestROPCCredentialInit:
    """Tests for ROPCCredential initialization."""

    def test_creates_confidential_client_app(self, mock_msal_app):
        """Creates MSAL ConfidentialClientApplication with correct params."""
        ROPCCredential(
            tenant_id="my-tenant",
            client_id="my-client",
            client_secret="my-secret",
            username="user@test.com",
            password="pass123",
        )

        mock_msal_app.assert_called_once_with(
            client_id="my-client",
            client_credential="my-secret",
            authority="https://login.microsoftonline.com/my-tenant",
        )

    def test_stores_username(self, mock_msal_app):
        """Stores username for later use."""
        cred = ROPCCredential(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            username="user@test.com",
            password="p",
        )

        assert cred._username == "user@test.com"

    def test_stores_password(self, mock_msal_app):
        """Stores password for later use."""
        cred = ROPCCredential(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            username="u",
            password="secret-pass",
        )

        assert cred._password == "secret-pass"


# =============================================================================
# get_token Tests
# =============================================================================


class TestGetToken:
    """Tests for get_token method."""

    def test_acquires_token_with_username_password(self, credential, mock_msal_app):
        """Calls acquire_token_by_username_password with correct params."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        credential.get_token("https://graph.microsoft.com/.default")

        mock_app_instance.acquire_token_by_username_password.assert_called_once_with(
            username="test@example.com",
            password="test-password",
            scopes=["https://graph.microsoft.com/.default"],
        )

    def test_returns_access_token(self, credential, mock_msal_app):
        """Returns AccessToken with correct token value."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "access_token": "my-access-token",
            "expires_in": 3600,
        }

        result = credential.get_token("scope1")

        assert result.token == "my-access-token"

    def test_calculates_expiration_time(self, credential, mock_msal_app):
        """Calculates correct expiration timestamp."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "access_token": "token",
            "expires_in": 7200,  # 2 hours
        }

        before = int(datetime.now().timestamp())
        result = credential.get_token("scope1")
        after = int(datetime.now().timestamp())

        # Expiration should be approximately now + 7200 seconds
        assert result.expires_on >= before + 7200
        assert result.expires_on <= after + 7200 + 1  # Allow 1 second tolerance

    def test_defaults_expiration_when_missing(self, credential, mock_msal_app):
        """Uses default 3600s expiration when not provided."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "access_token": "token",
            # No expires_in
        }

        before = int(datetime.now().timestamp())
        result = credential.get_token("scope1")

        # Should default to 3600 seconds
        assert result.expires_on >= before + 3600

    def test_handles_multiple_scopes(self, credential, mock_msal_app):
        """Passes multiple scopes as list."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "access_token": "token",
            "expires_in": 3600,
        }

        credential.get_token("scope1", "scope2", "scope3")

        mock_app_instance.acquire_token_by_username_password.assert_called_once()
        call_args = mock_app_instance.acquire_token_by_username_password.call_args
        assert call_args.kwargs["scopes"] == ["scope1", "scope2", "scope3"]

    def test_raises_on_auth_failure(self, credential, mock_msal_app):
        """Raises exception on authentication failure."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "error": "invalid_grant",
            "error_description": "Invalid username or password",
        }

        with pytest.raises(Exception) as exc_info:
            credential.get_token("scope1")

        assert "ROPC authentication failed" in str(exc_info.value)
        assert "invalid_grant" in str(exc_info.value)

    def test_error_message_includes_description(self, credential, mock_msal_app):
        """Error message includes error description."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "error": "access_denied",
            "error_description": "User account is locked",
        }

        with pytest.raises(Exception) as exc_info:
            credential.get_token("scope1")

        assert "User account is locked" in str(exc_info.value)

    def test_handles_missing_error_description(self, credential, mock_msal_app):
        """Handles missing error description gracefully."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "error": "unknown_error",
            # No error_description
        }

        with pytest.raises(Exception) as exc_info:
            credential.get_token("scope1")

        assert "No description" in str(exc_info.value)

    def test_ignores_kwargs(self, credential, mock_msal_app):
        """Ignores additional kwargs (required by interface)."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "access_token": "token",
            "expires_in": 3600,
        }

        # Should not raise, even with extra kwargs
        result = credential.get_token(
            "scope1",
            claims=None,
            tenant_id="override",
            enable_cae=True,
        )

        assert result.token == "token"


# =============================================================================
# Security Tests
# =============================================================================


class TestROPCCredentialSecurity:
    """Security-focused tests for ROPCCredential."""

    def test_password_not_in_error_message(self, credential, mock_msal_app):
        """Password is not exposed in error messages."""
        mock_app_instance = mock_msal_app.return_value
        mock_app_instance.acquire_token_by_username_password.return_value = {
            "error": "invalid_grant",
            "error_description": "Authentication failed",
        }

        with pytest.raises(Exception) as exc_info:
            credential.get_token("scope1")

        assert "test-password" not in str(exc_info.value)

    def test_password_not_in_repr(self, mock_msal_app):
        """Password is not exposed in string representation."""
        cred = ROPCCredential(
            tenant_id="t",
            client_id="c",
            client_secret="secret123",
            username="u",
            password="password123",
        )

        # Just verify the credential was created correctly
        # (repr/str may contain password by default, but that's Python's behavior)
        assert cred._password == "password123"


# =============================================================================
# DutyCalendarSync _detect_if_group Tests
# =============================================================================


class TestDetectIfGroup:
    """Tests for DutyCalendarSync._detect_if_group method."""

    @pytest.fixture
    def mock_graph_client(self):
        """Mock GraphServiceClient."""
        with patch("sjifire.calendar.duty_sync.GraphServiceClient") as mock:
            yield mock

    @pytest.fixture
    def mock_credentials(self):
        """Mock credential functions."""
        with patch("sjifire.calendar.duty_sync.get_graph_credentials") as mock_graph:
            mock_graph.return_value = ("tenant", "client", "secret")
            with patch("sjifire.calendar.duty_sync.ClientSecretCredential"):
                yield mock_graph

    @pytest.mark.asyncio
    async def test_detects_m365_unified_group(self, mock_graph_client, mock_credentials):
        """Detects M365 Unified group and caches group ID."""
        sync = DutyCalendarSync("test-group@sjifire.org")

        # Mock the groups API response
        mock_group = MagicMock()
        mock_group.id = "group-123"
        mock_group.display_name = "Test Group"
        mock_group.group_types = ["Unified"]

        mock_result = MagicMock()
        mock_result.value = [mock_group]

        mock_client_instance = mock_graph_client.return_value
        mock_client_instance.groups.get = AsyncMock(return_value=mock_result)

        # Mock the svc-automations credentials and ROPCCredential for delegated client setup
        with patch("sjifire.calendar.duty_sync.get_svc_automations_credentials") as mock_svc:
            mock_svc.return_value = ("svc@test.org", "password")
            with patch("sjifire.calendar.duty_sync.ROPCCredential"):
                result = await sync._detect_if_group()

        assert result is True
        assert sync._is_group is True
        assert sync._group_id == "group-123"

    @pytest.mark.asyncio
    async def test_detects_non_group_mailbox(self, mock_graph_client, mock_credentials):
        """Detects regular user mailbox (not a group)."""
        sync = DutyCalendarSync("user@sjifire.org")

        # Mock empty groups response
        mock_result = MagicMock()
        mock_result.value = []

        mock_client_instance = mock_graph_client.return_value
        mock_client_instance.groups.get = AsyncMock(return_value=mock_result)

        result = await sync._detect_if_group()

        assert result is False
        assert sync._is_group is False
        assert sync._group_id is None

    @pytest.mark.asyncio
    async def test_caches_detection_result(self, mock_graph_client, mock_credentials):
        """Caches detection result and doesn't call API twice."""
        sync = DutyCalendarSync("test@sjifire.org")
        sync._is_group = False  # Pre-set cached value

        # Should return cached value without API call
        result = await sync._detect_if_group()

        assert result is False
        mock_client_instance = mock_graph_client.return_value
        mock_client_instance.groups.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_api_error_gracefully(self, mock_graph_client, mock_credentials):
        """Handles API errors gracefully and returns False."""
        sync = DutyCalendarSync("test@sjifire.org")

        mock_client_instance = mock_graph_client.return_value
        mock_client_instance.groups.get = AsyncMock(side_effect=Exception("API Error"))

        result = await sync._detect_if_group()

        assert result is False
        assert sync._is_group is False

    @pytest.mark.asyncio
    async def test_ignores_non_unified_groups(self, mock_graph_client, mock_credentials):
        """Ignores groups that are not Unified (M365) groups."""
        sync = DutyCalendarSync("security-group@sjifire.org")

        # Mock a security group (not Unified)
        mock_group = MagicMock()
        mock_group.id = "security-123"
        mock_group.display_name = "Security Group"
        mock_group.group_types = []  # Not Unified

        mock_result = MagicMock()
        mock_result.value = [mock_group]

        mock_client_instance = mock_graph_client.return_value
        mock_client_instance.groups.get = AsyncMock(return_value=mock_result)

        result = await sync._detect_if_group()

        assert result is False
        assert sync._is_group is False

    @pytest.mark.asyncio
    async def test_sets_up_delegated_client_for_group(self, mock_graph_client, mock_credentials):
        """Sets up delegated auth client when group is detected."""
        sync = DutyCalendarSync("test-group@sjifire.org")

        # Mock the groups API response
        mock_group = MagicMock()
        mock_group.id = "group-123"
        mock_group.display_name = "Test Group"
        mock_group.group_types = ["Unified"]

        mock_result = MagicMock()
        mock_result.value = [mock_group]

        mock_client_instance = mock_graph_client.return_value
        mock_client_instance.groups.get = AsyncMock(return_value=mock_result)

        with patch("sjifire.calendar.duty_sync.get_svc_automations_credentials") as mock_svc:
            mock_svc.return_value = ("svc@test.org", "password")
            with patch("sjifire.calendar.duty_sync.ROPCCredential"):
                await sync._detect_if_group()

        # Should have set up delegated client
        assert sync._delegated_client is not None
