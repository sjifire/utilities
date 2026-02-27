"""Tests for Entra ID auth module."""

import base64
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.ops.auth import (
    EntraTokenValidator,
    UserContext,
    check_is_editor,
    get_current_user,
    get_easyauth_user,
    set_current_user,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear module-level caches between tests."""
    import sjifire.ops.auth

    sjifire.ops.auth._EDITOR_GROUP_ID = None
    yield
    sjifire.ops.auth._EDITOR_GROUP_ID = None


class TestUserContext:
    def test_basic_properties(self):
        user = UserContext(email="chief@sjifire.org", name="Fire Chief", user_id="abc-123")
        assert user.email == "chief@sjifire.org"
        assert user.name == "Fire Chief"
        assert user.user_id == "abc-123"
        assert user.groups == frozenset()

    def test_is_editor_without_config(self):
        """Without ENTRA_REPORT_EDITORS_GROUP_ID set, no one is an editor."""
        user = UserContext(
            email="chief@sjifire.org",
            name="Fire Chief",
            user_id="abc-123",
            groups=frozenset(["group-1", "group-2"]),
        )
        with patch.dict(os.environ, {}, clear=True):
            assert not user.is_editor

    def test_is_editor_with_matching_group(self):
        user = UserContext(
            email="chief@sjifire.org",
            name="Fire Chief",
            user_id="abc-123",
            groups=frozenset(["editor-group-id", "other-group"]),
        )
        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "editor-group-id"}):
            assert user.is_editor

    def test_is_editor_without_matching_group(self):
        user = UserContext(
            email="ff@sjifire.org",
            name="Firefighter",
            user_id="abc-456",
            groups=frozenset(["some-other-group"]),
        )
        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "editor-group-id"}):
            assert not user.is_editor

    def test_frozen(self):
        user = UserContext(email="a@b.com", name="A", user_id="1")
        with pytest.raises(AttributeError):
            user.email = "new@b.com"


class TestCurrentUserContext:
    def test_get_without_set_raises(self):
        """With no user set, always raises RuntimeError."""
        set_current_user(None)
        with pytest.raises(RuntimeError, match="No authenticated user"):
            get_current_user()

    def test_set_and_get(self):
        user = UserContext(email="test@sjifire.org", name="Test", user_id="xyz")
        set_current_user(user)
        assert get_current_user() is user
        # Clean up
        set_current_user(None)


class TestCheckIsEditor:
    """Tests for the live Graph API group membership check."""

    def setup_method(self):
        """Clear the editor cache between tests."""
        import sjifire.ops.auth

        sjifire.ops.auth._editor_cache.clear()

    async def test_returns_false_when_no_group_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await check_is_editor("user-1", fallback=True)
            assert result is False  # No group ID → False

    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_calls_graph_api(self, mock_check):
        mock_check.return_value = True

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            result = await check_is_editor("user-1")

        assert result is True
        mock_check.assert_called_once_with("user-1", "grp-1")

    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_caches_result_for_same_user(self, mock_check):
        """Result is cached — second call for same user skips Graph API."""
        mock_check.return_value = True

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            await check_is_editor("user-cache-1")
            await check_is_editor("user-cache-1")

        assert mock_check.call_count == 1

    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_falls_back_on_error(self, mock_check):
        mock_check.side_effect = RuntimeError("Graph API down")

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            result = await check_is_editor("user-1", fallback=True)

        assert result is True  # Uses fallback

    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_different_users_checked_independently(self, mock_check):
        mock_check.side_effect = [True, False]

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            r1 = await check_is_editor("user-1")
            r2 = await check_is_editor("user-2")

        assert r1 is True
        assert r2 is False
        assert mock_check.call_count == 2

    @patch("sjifire.ops.auth._resolve_user_id", new_callable=AsyncMock)
    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_resolves_user_id_from_email_when_empty(self, mock_check, mock_resolve):
        """When user_id is empty, resolves it from email before checking groups."""
        mock_resolve.return_value = "resolved-id"
        mock_check.return_value = True

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            result = await check_is_editor("", email="chief@sjifire.org")

        assert result is True
        mock_resolve.assert_called_once_with("chief@sjifire.org")
        mock_check.assert_called_once_with("resolved-id", "grp-1")

    @patch("sjifire.ops.auth._resolve_user_id", new_callable=AsyncMock)
    async def test_fallback_when_user_id_unresolvable(self, mock_resolve):
        """When user_id is empty and email can't be resolved, uses fallback."""
        mock_resolve.return_value = ""

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            result = await check_is_editor("", email="unknown@sjifire.org", fallback=True)

        assert result is True  # fallback

    async def test_fallback_when_no_user_id_and_no_email(self):
        """Empty user_id and no email returns fallback directly."""
        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            assert await check_is_editor("", fallback=False) is False
            assert await check_is_editor("", fallback=True) is True


# ---------------------------------------------------------------------------
# EasyAuth header parsing
# ---------------------------------------------------------------------------


def _make_principal(claims: list[dict]) -> str:
    """Build a Base64-encoded X-MS-CLIENT-PRINCIPAL header value."""
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


def _make_request(headers: dict) -> MagicMock:
    """Build a minimal mock Request with the given headers."""
    req = MagicMock()
    req.headers = headers
    return req


class TestGetEasyAuthUser:
    """Tests for get_easyauth_user header parsing."""

    def test_returns_none_without_header(self):
        req = _make_request({})
        assert get_easyauth_user(req) is None

    def test_returns_none_for_invalid_base64(self):
        req = _make_request({"X-MS-CLIENT-PRINCIPAL": "not-valid-base64!!!"})
        assert get_easyauth_user(req) is None

    def test_returns_none_for_invalid_json(self):
        bad_json = base64.b64encode(b"not json").decode()
        req = _make_request({"X-MS-CLIENT-PRINCIPAL": bad_json})
        assert get_easyauth_user(req) is None

    def test_extracts_user_from_claims(self):
        principal = _make_principal(
            [
                {"typ": "preferred_username", "val": "Chief@SJIFire.org"},
                {"typ": "name", "val": "Fire Chief"},
                {
                    "typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
                    "val": "oid-123",
                },
                {"typ": "groups", "val": "group-a"},
                {"typ": "groups", "val": "group-b"},
            ]
        )
        req = _make_request({"X-MS-CLIENT-PRINCIPAL": principal})
        user = get_easyauth_user(req)

        assert user is not None
        assert user.email == "chief@sjifire.org"  # lowercased
        assert user.name == "Fire Chief"
        assert user.user_id == "oid-123"
        assert user.groups == frozenset({"group-a", "group-b"})

    def test_falls_back_to_email_claim(self):
        """Uses emailaddress claim when preferred_username is absent."""
        principal = _make_principal(
            [
                {
                    "typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                    "val": "FF@sjifire.org",
                },
            ]
        )
        req = _make_request({"X-MS-CLIENT-PRINCIPAL": principal})
        user = get_easyauth_user(req)

        assert user is not None
        assert user.email == "ff@sjifire.org"
        assert user.name == "FF"  # derived from email prefix (before lowercasing)

    def test_empty_claims_returns_user_with_defaults(self):
        principal = _make_principal([])
        req = _make_request({"X-MS-CLIENT-PRINCIPAL": principal})
        user = get_easyauth_user(req)

        assert user is not None
        assert user.email == ""
        assert user.name == "Unknown"
        assert user.user_id == ""
        assert user.groups == frozenset()

    def test_multiple_groups_collected(self):
        principal = _make_principal(
            [
                {"typ": "preferred_username", "val": "user@sjifire.org"},
                {"typ": "groups", "val": "g1"},
                {"typ": "groups", "val": "g2"},
                {"typ": "groups", "val": "g3"},
            ]
        )
        req = _make_request({"X-MS-CLIENT-PRINCIPAL": principal})
        user = get_easyauth_user(req)
        assert user.groups == frozenset({"g1", "g2", "g3"})


# ---------------------------------------------------------------------------
# EntraTokenValidator
# ---------------------------------------------------------------------------


class TestEntraTokenValidator:
    """Tests for JWT token validation."""

    def test_issuer_and_jwks_url_from_tenant(self):
        v = EntraTokenValidator(tenant_id="tenant-abc", api_client_id="client-xyz")
        assert "tenant-abc" in v.issuer
        assert "tenant-abc" in v.jwks_url
        assert v.api_client_id == "client-xyz"

    def _make_validator(self, payload: dict) -> tuple[EntraTokenValidator, MagicMock]:
        """Create a validator with mocked JWKS client and jwt.decode."""
        v = EntraTokenValidator(tenant_id="t", api_client_id="aud")
        mock_jwks = MagicMock()
        fake_signing_key = MagicMock()
        fake_signing_key.key = MagicMock()
        mock_jwks.get_signing_key_from_jwt.return_value = fake_signing_key
        # Set the cached JWKS client directly (bypass property)
        v._jwks_client = mock_jwks
        return v, fake_signing_key.key

    def test_validate_token_extracts_claims(self):
        """validate_token extracts email, name, user_id, and groups from JWT payload."""
        payload = {
            "preferred_username": "Captain@SJIFire.org",
            "name": "Captain Hook",
            "oid": "oid-456",
            "groups": ["g1", "g2"],
        }
        v, fake_key = self._make_validator(payload)

        with patch("sjifire.ops.auth.jwt.decode", return_value=payload) as mock_decode:
            user = v.validate_token("fake.jwt.token")

        assert user.email == "captain@sjifire.org"
        assert user.name == "Captain Hook"
        assert user.user_id == "oid-456"
        assert user.groups == frozenset({"g1", "g2"})
        mock_decode.assert_called_once_with(
            "fake.jwt.token",
            fake_key,
            algorithms=["RS256"],
            audience="aud",
            issuer=v.issuer,
        )

    def test_validate_token_uses_email_fallback(self):
        """Falls back to email claim when preferred_username is absent."""
        payload = {"email": "Lt@SJIFire.org", "oid": "oid-789"}
        v, _ = self._make_validator(payload)

        with patch("sjifire.ops.auth.jwt.decode", return_value=payload):
            user = v.validate_token("token")

        assert user.email == "lt@sjifire.org"

    def test_validate_token_uses_upn_fallback(self):
        """Falls back to upn claim when both preferred_username and email are absent."""
        payload = {"upn": "EMT@SJIFire.org", "oid": "oid-000"}
        v, _ = self._make_validator(payload)

        with patch("sjifire.ops.auth.jwt.decode", return_value=payload):
            user = v.validate_token("token")

        assert user.email == "emt@sjifire.org"

    def test_validate_token_no_groups(self):
        """Token without groups claim results in empty groups set."""
        payload = {"preferred_username": "user@sjifire.org", "oid": "oid-1"}
        v, _ = self._make_validator(payload)

        with patch("sjifire.ops.auth.jwt.decode", return_value=payload):
            user = v.validate_token("token")

        assert user.groups == frozenset()
