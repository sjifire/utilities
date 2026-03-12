"""Comprehensive tests for EntraOAuthProvider — OAuth AS proxy to Entra ID.

Covers serialization, client registration, authorization flow, callback handling,
token exchange, refresh token rotation, access token loading (with UserContext bridge),
and token revocation.

Uses an in-memory TokenStore (no Cosmos DB) by patching get_cosmos_container.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import RedirectResponse

from sjifire.ops.auth import UserContext, get_current_user, set_current_user
from sjifire.ops.oauth_provider import (
    ACCESS_TOKEN_TTL,
    AUTH_CODE_TTL,
    CLIENT_REG_TTL,
    PENDING_AUTH_TTL,
    REFRESH_TOKEN_TTL,
    EntraOAuthProvider,
    _deserialize_auth_params,
    _serialize_auth_params,
)
from sjifire.ops.token_store import TokenStore, _serialize_user, get_token_store

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_TENANT_ID = "test-tenant-id"
_API_CLIENT_ID = "test-api-client-id"
_SERVER_URL = "https://ops.sjifire.org"
_CLIENT_SECRET = "test-client-secret"

_TEST_USER = UserContext(
    email="chief@sjifire.org",
    name="Fire Chief",
    user_id="oid-abc-123",
    groups=frozenset(["officer-group", "admin-group"]),
)


def _make_client_info(client_id: str = "claude-client-1") -> OAuthClientInformationFull:
    """Build a minimal OAuthClientInformationFull for tests."""
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="client-sec-1",
        redirect_uris=["https://claude.ai/callback"],
    )


def _make_auth_params(**overrides) -> AuthorizationParams:
    """Build AuthorizationParams with sensible defaults."""
    defaults = {
        "state": "mcp-state-abc",
        "scopes": ["mcp.access"],
        "code_challenge": "challenge-xyz",
        "redirect_uri": "https://claude.ai/callback",
        "redirect_uri_provided_explicitly": True,
        "resource": None,
    }
    defaults.update(overrides)
    return AuthorizationParams(**defaults)


async def _noop_container(name):
    return None


@pytest.fixture(autouse=True)
def _clear_state(monkeypatch):
    """Reset in-memory store, singleton, ContextVar, and env so tests are isolated."""
    import sjifire.ops.auth as auth_mod
    import sjifire.ops.token_store as mod

    TokenStore._memory.clear()
    mod._instance = None
    # Reset the ContextVar so prior tests don't bleed into ours
    auth_mod._current_user.set(None)
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.token_store.get_cosmos_container", _noop_container)
    yield
    TokenStore._memory.clear()
    mod._instance = None
    auth_mod._current_user.set(None)


@pytest.fixture
def provider() -> EntraOAuthProvider:
    """Create an EntraOAuthProvider configured for testing."""
    return EntraOAuthProvider(
        tenant_id=_TENANT_ID,
        api_client_id=_API_CLIENT_ID,
        server_url=_SERVER_URL,
        client_secret=_CLIENT_SECRET,
    )


def _build_request(query_params: dict[str, str] | None = None) -> Request:
    """Build a minimal Starlette Request with query params."""
    qs = "&".join(f"{k}={v}" for k, v in (query_params or {}).items())
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/callback",
        "query_string": qs.encode(),
        "headers": [],
    }
    return Request(scope)


# ===========================================================================
# 1. Serialization round-trip
# ===========================================================================


class TestSerializeAuthParams:
    def test_round_trip_basic(self):
        params = _make_auth_params()
        data = _serialize_auth_params(params)
        restored = _deserialize_auth_params(data)

        assert restored.state == params.state
        assert restored.scopes == params.scopes
        assert restored.code_challenge == params.code_challenge
        assert str(restored.redirect_uri) == str(params.redirect_uri)
        assert restored.redirect_uri_provided_explicitly == params.redirect_uri_provided_explicitly
        assert restored.resource == params.resource

    def test_round_trip_with_resource(self):
        params = _make_auth_params(resource="https://api.example.com")
        data = _serialize_auth_params(params)
        restored = _deserialize_auth_params(data)
        assert str(restored.resource) == "https://api.example.com"

    def test_round_trip_none_resource(self):
        params = _make_auth_params(resource=None)
        data = _serialize_auth_params(params)
        assert data["resource"] is None
        restored = _deserialize_auth_params(data)
        assert restored.resource is None

    def test_round_trip_none_state(self):
        params = _make_auth_params(state=None)
        data = _serialize_auth_params(params)
        assert data["state"] is None
        restored = _deserialize_auth_params(data)
        assert restored.state is None

    def test_round_trip_multiple_scopes(self):
        params = _make_auth_params(scopes=["mcp.access", "openid", "profile"])
        data = _serialize_auth_params(params)
        restored = _deserialize_auth_params(data)
        assert restored.scopes == ["mcp.access", "openid", "profile"]

    def test_deserialize_missing_scopes_defaults(self):
        """Missing scopes key should default to ['mcp.access']."""
        data = {
            "state": "s",
            "code_challenge": "c",
            "redirect_uri": "https://example.com/cb",
            "redirect_uri_provided_explicitly": True,
        }
        restored = _deserialize_auth_params(data)
        assert restored.scopes == ["mcp.access"]

    def test_deserialize_missing_explicit_flag_defaults_true(self):
        data = {
            "state": "s",
            "scopes": ["mcp.access"],
            "code_challenge": "c",
            "redirect_uri": "https://example.com/cb",
        }
        restored = _deserialize_auth_params(data)
        assert restored.redirect_uri_provided_explicitly is True

    def test_serialized_format_is_json_safe(self):
        """All values should be JSON-serializable (no Pydantic types)."""
        import json

        params = _make_auth_params()
        data = _serialize_auth_params(params)
        roundtripped = json.loads(json.dumps(data))
        assert roundtripped == data


# ===========================================================================
# 2. Client registration
# ===========================================================================


class TestGetClient:
    async def test_unknown_client_returns_none(self, provider):
        result = await provider.get_client("nonexistent-client")
        assert result is None

    async def test_returns_registered_client(self, provider):
        client = _make_client_info("my-client")
        await provider.register_client(client)

        loaded = await provider.get_client("my-client")
        assert loaded is not None
        assert loaded.client_id == "my-client"
        assert loaded.redirect_uris == client.redirect_uris


class TestRegisterClient:
    async def test_stores_and_retrieves(self, provider):
        client = _make_client_info("reg-test")
        await provider.register_client(client)

        loaded = await provider.get_client("reg-test")
        assert loaded is not None
        assert loaded.client_id == "reg-test"

    async def test_no_client_id_is_noop(self, provider):
        """A client with no client_id should be silently skipped."""
        client = OAuthClientInformationFull(
            client_id=None,
            redirect_uris=["https://example.com/cb"],
        )
        # Should not raise
        await provider.register_client(client)

    async def test_register_multiple_clients(self, provider):
        c1 = _make_client_info("client-a")
        c2 = _make_client_info("client-b")
        await provider.register_client(c1)
        await provider.register_client(c2)

        assert (await provider.get_client("client-a")).client_id == "client-a"
        assert (await provider.get_client("client-b")).client_id == "client-b"

    async def test_re_register_overwrites(self, provider):
        """Re-registering the same client_id replaces the old registration."""
        c1 = _make_client_info("dup")
        c1_v2 = OAuthClientInformationFull(
            client_id="dup",
            client_secret="new-secret",
            redirect_uris=["https://new.example.com/cb"],
        )
        await provider.register_client(c1)
        await provider.register_client(c1_v2)

        loaded = await provider.get_client("dup")
        assert str(loaded.redirect_uris[0]) == "https://new.example.com/cb"


# ===========================================================================
# 3. Authorization -- redirect to Entra ID
# ===========================================================================


class TestAuthorize:
    async def test_returns_entra_login_url(self, provider):
        client = _make_client_info()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        parsed = urlparse(url)

        assert parsed.scheme == "https"
        assert parsed.hostname == "login.microsoftonline.com"
        assert f"/{_TENANT_ID}/oauth2/v2.0/authorize" in parsed.path

    async def test_url_has_required_params(self, provider):
        client = _make_client_info()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)

        assert qs["client_id"] == [_API_CLIENT_ID]
        assert qs["response_type"] == ["code"]
        assert qs["redirect_uri"] == [f"{_SERVER_URL}/callback"]
        assert qs["scope"] == ["openid profile email"]
        assert qs["code_challenge_method"] == ["S256"]
        assert qs["response_mode"] == ["query"]
        # State and code_challenge should be present (opaque values)
        assert "state" in qs
        assert "code_challenge" in qs

    async def test_stores_pending_auth(self, provider):
        """After authorize(), the pending auth should be recoverable from the store."""
        client = _make_client_info()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        entra_state = qs["state"][0]

        store = await get_token_store()
        pending = await store.get("pending_auth", entra_state)
        assert pending is not None
        assert pending["client_id"] == client.client_id
        assert "entra_code_verifier" in pending
        assert "mcp_params" in pending

    async def test_pending_auth_has_expiry(self, provider):
        client = _make_client_info()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        entra_state = qs["state"][0]

        store = await get_token_store()
        pending = await store.get("pending_auth", entra_state)
        assert pending["expires_at"] > time.time()
        assert pending["expires_at"] <= time.time() + PENDING_AUTH_TTL + 1

    async def test_server_url_trailing_slash_stripped(self):
        """Trailing slash on server_url shouldn't double up."""
        p = EntraOAuthProvider(
            tenant_id=_TENANT_ID,
            api_client_id=_API_CLIENT_ID,
            server_url="https://ops.sjifire.org/",
            client_secret=_CLIENT_SECRET,
        )
        client = _make_client_info()
        params = _make_auth_params()

        url = await p.authorize(client, params)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        # Should be /callback, not //callback
        assert qs["redirect_uri"] == ["https://ops.sjifire.org/callback"]


# ===========================================================================
# 4. Handle callback -- Entra redirect back
# ===========================================================================


class TestHandleCallback:
    async def _setup_pending_auth(self, provider, entra_state: str = "test-entra-state"):
        """Manually store a pending auth entry so handle_callback can find it."""
        store = await get_token_store()
        await store.set(
            "pending_auth",
            entra_state,
            {
                "mcp_params": _serialize_auth_params(_make_auth_params()),
                "client_id": "claude-client-1",
                "entra_code_verifier": "test-verifier",
                "expires_at": time.time() + PENDING_AUTH_TTL,
            },
            PENDING_AUTH_TTL,
        )
        return store

    # --- Error cases ---

    async def test_entra_error_param_returns_400(self, provider):
        request = _build_request({"error": "access_denied", "error_description": "User+cancelled"})
        resp = await provider.handle_callback(request)
        assert resp.status_code == 400
        assert "access_denied" in resp.body.decode()

    async def test_missing_code_returns_400(self, provider):
        request = _build_request({"state": "some-state"})
        resp = await provider.handle_callback(request)
        assert resp.status_code == 400
        assert "Missing code or state" in resp.body.decode()

    async def test_missing_state_returns_400(self, provider):
        request = _build_request({"code": "some-code"})
        resp = await provider.handle_callback(request)
        assert resp.status_code == 400
        assert "Missing code or state" in resp.body.decode()

    async def test_missing_both_code_and_state_returns_400(self, provider):
        request = _build_request({})
        resp = await provider.handle_callback(request)
        assert resp.status_code == 400

    async def test_invalid_state_returns_400(self, provider):
        """Unknown state (not in store) should fail."""
        request = _build_request({"code": "entra-code", "state": "bogus-state"})
        resp = await provider.handle_callback(request)
        assert resp.status_code == 400
        assert "Invalid or expired state" in resp.body.decode()

    async def test_expired_state_returns_400(self, provider):
        """Expired pending auth should be rejected."""
        store = await get_token_store()
        await store.set(
            "pending_auth",
            "expired-state",
            {
                "mcp_params": _serialize_auth_params(_make_auth_params()),
                "client_id": "claude-client-1",
                "entra_code_verifier": "v",
                "expires_at": time.time() - 10,  # already expired
            },
            PENDING_AUTH_TTL,
        )
        # Clear L1 so expiry check happens on backing store read
        store._l1.clear()

        request = _build_request({"code": "entra-code", "state": "expired-state"})
        resp = await provider.handle_callback(request)
        assert resp.status_code == 400

    async def test_missing_client_secret_returns_500(self):
        """Provider without client_secret should error at token exchange."""
        p = EntraOAuthProvider(
            tenant_id=_TENANT_ID,
            api_client_id=_API_CLIENT_ID,
            server_url=_SERVER_URL,
            client_secret="",  # empty
        )
        await self._setup_pending_auth(p, "state-no-secret")

        request = _build_request({"code": "entra-code", "state": "state-no-secret"})
        resp = await p.handle_callback(request)
        assert resp.status_code == 500
        assert "missing client secret" in resp.body.decode()

    async def test_entra_token_exchange_failure_returns_502(self, provider):
        """Non-200 response from Entra token endpoint should fail."""
        await self._setup_pending_auth(provider, "state-fail")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = "invalid_grant"

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            request = _build_request({"code": "bad-code", "state": "state-fail"})
            resp = await provider.handle_callback(request)
            assert resp.status_code == 502
            assert "Token exchange failed" in resp.body.decode()

    async def test_no_id_token_returns_502(self, provider):
        """Entra response without id_token should fail."""
        await self._setup_pending_auth(provider, "state-no-id")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "at-only"}  # no id_token

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            request = _build_request({"code": "code", "state": "state-no-id"})
            resp = await provider.handle_callback(request)
            assert resp.status_code == 502
            assert "No id_token" in resp.body.decode()

    async def test_token_validation_failure_returns_502(self, provider):
        """Invalid id_token (fails validation) should return 502."""
        await self._setup_pending_auth(provider, "state-bad-jwt")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": "bad.jwt.token"}

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            # Make the validator raise
            provider._validator = MagicMock()
            provider._validator.validate_token.side_effect = Exception("Invalid signature")

            request = _build_request({"code": "code", "state": "state-bad-jwt"})
            resp = await provider.handle_callback(request)
            assert resp.status_code == 502
            assert "Token validation failed" in resp.body.decode()

    # --- Happy path ---

    async def test_happy_path_mints_code_and_redirects(self, provider):
        """Successful callback should mint an MCP auth code and redirect to Claude."""
        await self._setup_pending_auth(provider, "good-state")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id_token": "valid.jwt.token",
            "access_token": "entra-at",
        }

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            # Mock the validator to return our test user
            provider._validator = MagicMock()
            provider._validator.validate_token.return_value = _TEST_USER

            request = _build_request({"code": "entra-auth-code", "state": "good-state"})
            resp = await provider.handle_callback(request)

        assert resp.status_code == 302
        assert isinstance(resp, RedirectResponse)

        # The redirect should go to Claude's redirect_uri with code and state
        location = resp.headers["location"]
        parsed = urlparse(location)
        assert parsed.hostname == "claude.ai"
        assert parsed.path == "/callback"
        qs = parse_qs(parsed.query)
        assert "code" in qs
        assert qs["state"] == ["mcp-state-abc"]

    async def test_happy_path_stores_auth_code_with_user(self, provider):
        """The minted auth code should carry user identity in the store."""
        await self._setup_pending_auth(provider, "store-check-state")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": "valid.jwt.token"}

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            provider._validator = MagicMock()
            provider._validator.validate_token.return_value = _TEST_USER

            request = _build_request({"code": "code", "state": "store-check-state"})
            resp = await provider.handle_callback(request)

        # Extract the minted code from the redirect
        location = resp.headers["location"]
        mcp_code = parse_qs(urlparse(location).query)["code"][0]

        store = await get_token_store()
        doc = await store.get("auth_code", mcp_code)
        assert doc is not None
        assert doc["client_id"] == "claude-client-1"
        assert doc["scopes"] == ["mcp.access"]
        assert doc["user"]["email"] == "chief@sjifire.org"
        assert doc["user"]["name"] == "Fire Chief"
        assert doc["user"]["user_id"] == "oid-abc-123"

    async def test_happy_path_deletes_pending_auth(self, provider):
        """After callback, the pending auth entry should be consumed."""
        store = await self._setup_pending_auth(provider, "consumed-state")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": "valid.jwt.token"}

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            provider._validator = MagicMock()
            provider._validator.validate_token.return_value = _TEST_USER

            request = _build_request({"code": "code", "state": "consumed-state"})
            await provider.handle_callback(request)

        pending = await store.get("pending_auth", "consumed-state")
        assert pending is None

    async def test_happy_path_sends_correct_token_request(self, provider):
        """Verify the POST to Entra token endpoint has the right fields."""
        await self._setup_pending_auth(provider, "verify-post-state")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": "jwt"}

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            provider._validator = MagicMock()
            provider._validator.validate_token.return_value = _TEST_USER

            request = _build_request({"code": "entra-auth-code", "state": "verify-post-state"})
            await provider.handle_callback(request)

            # Check what was posted to Entra
            call_args = mock_client.post.call_args
            token_url = call_args[0][0]
            token_data = call_args[1]["data"]

            assert f"/{_TENANT_ID}/oauth2/v2.0/token" in token_url
            assert token_data["client_id"] == _API_CLIENT_ID
            assert token_data["grant_type"] == "authorization_code"
            assert token_data["code"] == "entra-auth-code"
            assert token_data["redirect_uri"] == f"{_SERVER_URL}/callback"
            assert token_data["client_secret"] == _CLIENT_SECRET
            assert "code_verifier" in token_data  # PKCE verifier from pending auth

    async def test_callback_replayed_state_fails(self, provider):
        """Using the same state twice should fail (pending auth is consumed)."""
        await self._setup_pending_auth(provider, "replay-state")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": "jwt"}

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            provider._validator = MagicMock()
            provider._validator.validate_token.return_value = _TEST_USER

            # First call succeeds
            request = _build_request({"code": "code", "state": "replay-state"})
            resp1 = await provider.handle_callback(request)
            assert resp1.status_code == 302

        # Second call with same state fails
        request2 = _build_request({"code": "code", "state": "replay-state"})
        resp2 = await provider.handle_callback(request2)
        assert resp2.status_code == 400
        assert "Invalid or expired state" in resp2.body.decode()


# ===========================================================================
# 5. Load authorization code
# ===========================================================================


class TestLoadAuthorizationCode:
    async def test_unknown_code_returns_none(self, provider):
        client = _make_client_info()
        result = await provider.load_authorization_code(client, "nonexistent-code")
        assert result is None

    async def test_valid_code_returns_authorization_code(self, provider):
        store = await get_token_store()
        now = time.time()
        await store.set(
            "auth_code",
            "valid-mcp-code",
            {
                "expires_at": now + AUTH_CODE_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                "code_challenge": "challenge-xyz",
                "redirect_uri": "https://claude.ai/callback",
                "redirect_uri_provided_explicitly": True,
                "resource": None,
                "user": _serialize_user(_TEST_USER),
            },
            AUTH_CODE_TTL,
        )

        client = _make_client_info()
        result = await provider.load_authorization_code(client, "valid-mcp-code")

        assert result is not None
        assert isinstance(result, AuthorizationCode)
        assert result.code == "valid-mcp-code"
        assert result.client_id == "claude-client-1"
        assert result.scopes == ["mcp.access"]
        assert result.code_challenge == "challenge-xyz"
        assert str(result.redirect_uri) == "https://claude.ai/callback"
        assert result.redirect_uri_provided_explicitly is True
        assert result.resource is None

    async def test_expired_code_returns_none(self, provider):
        store = await get_token_store()
        await store.set(
            "auth_code",
            "expired-code",
            {
                "expires_at": time.time() - 10,
                "client_id": "c1",
                "scopes": ["mcp.access"],
                "code_challenge": "c",
                "redirect_uri": "https://example.com/cb",
                "redirect_uri_provided_explicitly": True,
            },
            AUTH_CODE_TTL,
        )
        # Clear L1 so expiry check runs
        store._l1.clear()

        client = _make_client_info()
        result = await provider.load_authorization_code(client, "expired-code")
        assert result is None


# ===========================================================================
# 6. Exchange authorization code
# ===========================================================================


class TestExchangeAuthorizationCode:
    async def _store_auth_code(self, code: str = "exchange-code") -> None:
        """Store an auth code in the token store for exchange tests."""
        store = await get_token_store()
        await store.set(
            "auth_code",
            code,
            {
                "expires_at": time.time() + AUTH_CODE_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                "code_challenge": "challenge",
                "redirect_uri": "https://claude.ai/callback",
                "redirect_uri_provided_explicitly": True,
                "resource": None,
                "user": _serialize_user(_TEST_USER),
            },
            AUTH_CODE_TTL,
        )

    def _make_auth_code_obj(self, code: str = "exchange-code") -> AuthorizationCode:
        return AuthorizationCode(
            code=code,
            scopes=["mcp.access"],
            expires_at=time.time() + AUTH_CODE_TTL,
            client_id="claude-client-1",
            code_challenge="challenge",
            redirect_uri="https://claude.ai/callback",
            redirect_uri_provided_explicitly=True,
        )

    async def test_mints_access_and_refresh_tokens(self, provider):
        await self._store_auth_code()
        client = _make_client_info()
        auth_code = self._make_auth_code_obj()

        token = await provider.exchange_authorization_code(client, auth_code)

        assert token.access_token
        assert token.refresh_token
        assert token.token_type == "Bearer"
        assert token.expires_in == ACCESS_TOKEN_TTL
        assert token.scope == "mcp.access"

    async def test_deletes_auth_code_after_exchange(self, provider):
        await self._store_auth_code()
        client = _make_client_info()
        auth_code = self._make_auth_code_obj()

        await provider.exchange_authorization_code(client, auth_code)

        store = await get_token_store()
        assert await store.get("auth_code", "exchange-code") is None

    async def test_access_token_in_store_with_user(self, provider):
        await self._store_auth_code()
        client = _make_client_info()
        auth_code = self._make_auth_code_obj()

        token = await provider.exchange_authorization_code(client, auth_code)

        store = await get_token_store()
        doc = await store.get("access_token", token.access_token)
        assert doc is not None
        assert doc["client_id"] == "claude-client-1"
        assert doc["scopes"] == ["mcp.access"]
        assert doc["user"]["email"] == "chief@sjifire.org"

    async def test_refresh_token_in_store_with_user(self, provider):
        await self._store_auth_code()
        client = _make_client_info()
        auth_code = self._make_auth_code_obj()

        token = await provider.exchange_authorization_code(client, auth_code)

        store = await get_token_store()
        doc = await store.get("refresh_token", token.refresh_token)
        assert doc is not None
        assert doc["client_id"] == "claude-client-1"
        assert doc["user"]["email"] == "chief@sjifire.org"

    async def test_already_consumed_code_raises(self, provider):
        """Exchanging the same code twice should raise ValueError."""
        await self._store_auth_code("once-only")
        client = _make_client_info()
        auth_code = self._make_auth_code_obj("once-only")

        # First exchange succeeds
        await provider.exchange_authorization_code(client, auth_code)

        # Second exchange raises
        with pytest.raises(ValueError, match="already consumed"):
            await provider.exchange_authorization_code(client, auth_code)

    async def test_tokens_have_correct_expiry(self, provider):
        await self._store_auth_code()
        client = _make_client_info()
        auth_code = self._make_auth_code_obj()
        now = int(time.time())

        token = await provider.exchange_authorization_code(client, auth_code)

        store = await get_token_store()
        at_doc = await store.get("access_token", token.access_token)
        rt_doc = await store.get("refresh_token", token.refresh_token)

        # Allow 2s tolerance for test execution time
        assert abs(at_doc["expires_at"] - (now + ACCESS_TOKEN_TTL)) <= 2
        assert abs(rt_doc["expires_at"] - (now + REFRESH_TOKEN_TTL)) <= 2

    async def test_multiple_scopes_preserved(self, provider):
        """Scopes from the auth code should propagate to tokens."""
        store = await get_token_store()
        await store.set(
            "auth_code",
            "multi-scope",
            {
                "expires_at": time.time() + AUTH_CODE_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access", "openid"],
                "code_challenge": "c",
                "redirect_uri": "https://claude.ai/callback",
                "redirect_uri_provided_explicitly": True,
                "user": _serialize_user(_TEST_USER),
            },
            AUTH_CODE_TTL,
        )
        client = _make_client_info()
        auth_code = AuthorizationCode(
            code="multi-scope",
            scopes=["mcp.access", "openid"],
            expires_at=time.time() + AUTH_CODE_TTL,
            client_id="claude-client-1",
            code_challenge="c",
            redirect_uri="https://claude.ai/callback",
            redirect_uri_provided_explicitly=True,
        )

        token = await provider.exchange_authorization_code(client, auth_code)
        assert token.scope == "mcp.access openid"


# ===========================================================================
# 7. Load refresh token
# ===========================================================================


class TestLoadRefreshToken:
    async def test_unknown_token_returns_none(self, provider):
        client = _make_client_info()
        result = await provider.load_refresh_token(client, "nonexistent-rt")
        assert result is None

    async def test_valid_refresh_token(self, provider):
        store = await get_token_store()
        await store.set(
            "refresh_token",
            "valid-rt",
            {
                "expires_at": int(time.time()) + REFRESH_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                "user": _serialize_user(_TEST_USER),
            },
            REFRESH_TOKEN_TTL,
        )

        client = _make_client_info()
        result = await provider.load_refresh_token(client, "valid-rt")

        assert result is not None
        assert isinstance(result, RefreshToken)
        assert result.token == "valid-rt"
        assert result.client_id == "claude-client-1"
        assert result.scopes == ["mcp.access"]

    async def test_expired_refresh_token_returns_none(self, provider):
        store = await get_token_store()
        await store.set(
            "refresh_token",
            "expired-rt",
            {
                "expires_at": time.time() - 10,
                "client_id": "c1",
                "scopes": ["mcp.access"],
            },
            REFRESH_TOKEN_TTL,
        )
        store._l1.clear()

        client = _make_client_info()
        result = await provider.load_refresh_token(client, "expired-rt")
        assert result is None


# ===========================================================================
# 8. Exchange refresh token (rotation)
# ===========================================================================


class TestExchangeRefreshToken:
    async def _setup_tokens(self, provider):
        """Store access + refresh tokens for rotation tests. Returns (at_str, rt_str)."""
        store = await get_token_store()
        at_str = "old-access-token"
        rt_str = "old-refresh-token"
        user_data = _serialize_user(_TEST_USER)

        await store.set(
            "access_token",
            at_str,
            {
                "expires_at": time.time() + ACCESS_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                "user": user_data,
            },
            ACCESS_TOKEN_TTL,
        )
        await store.set(
            "refresh_token",
            rt_str,
            {
                "expires_at": time.time() + REFRESH_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                "user": user_data,
            },
            REFRESH_TOKEN_TTL,
        )
        return at_str, rt_str

    async def test_rotates_both_tokens(self, provider):
        old_at, old_rt = await self._setup_tokens(provider)
        client = _make_client_info()
        rt = RefreshToken(
            token=old_rt,
            client_id="claude-client-1",
            scopes=["mcp.access"],
        )

        new_token = await provider.exchange_refresh_token(client, rt, scopes=["mcp.access"])

        # New tokens should be different from old
        assert new_token.access_token != old_at
        assert new_token.refresh_token != old_rt
        assert new_token.token_type == "Bearer"
        assert new_token.expires_in == ACCESS_TOKEN_TTL

    async def test_old_tokens_revoked(self, provider):
        old_at, old_rt = await self._setup_tokens(provider)
        client = _make_client_info()
        rt = RefreshToken(
            token=old_rt,
            client_id="claude-client-1",
            scopes=["mcp.access"],
        )

        await provider.exchange_refresh_token(client, rt, scopes=["mcp.access"])

        store = await get_token_store()
        assert await store.get("access_token", old_at) is None
        assert await store.get("refresh_token", old_rt) is None

    async def test_user_carried_forward(self, provider):
        """User data from refresh token should be on the new tokens."""
        _, old_rt = await self._setup_tokens(provider)
        client = _make_client_info()
        rt = RefreshToken(
            token=old_rt,
            client_id="claude-client-1",
            scopes=["mcp.access"],
        )

        new_token = await provider.exchange_refresh_token(client, rt, scopes=["mcp.access"])

        store = await get_token_store()
        at_doc = await store.get("access_token", new_token.access_token)
        rt_doc = await store.get("refresh_token", new_token.refresh_token)

        assert at_doc["user"]["email"] == "chief@sjifire.org"
        assert at_doc["user"]["name"] == "Fire Chief"
        assert rt_doc["user"]["email"] == "chief@sjifire.org"

    async def test_new_tokens_in_store(self, provider):
        _, old_rt = await self._setup_tokens(provider)
        client = _make_client_info()
        rt = RefreshToken(
            token=old_rt,
            client_id="claude-client-1",
            scopes=["mcp.access"],
        )

        new_token = await provider.exchange_refresh_token(client, rt, scopes=["mcp.access"])

        store = await get_token_store()
        assert await store.get("access_token", new_token.access_token) is not None
        assert await store.get("refresh_token", new_token.refresh_token) is not None

    async def test_empty_scopes_uses_refresh_token_scopes(self, provider):
        """When scopes param is empty, should use scopes from the refresh token."""
        _, old_rt = await self._setup_tokens(provider)
        client = _make_client_info()
        rt = RefreshToken(
            token=old_rt,
            client_id="claude-client-1",
            scopes=["mcp.access", "extra"],
        )

        new_token = await provider.exchange_refresh_token(client, rt, scopes=[])

        assert new_token.scope == "mcp.access extra"

    async def test_scopes_override_when_provided(self, provider):
        """When scopes param is provided, it should be used."""
        _, old_rt = await self._setup_tokens(provider)
        client = _make_client_info()
        rt = RefreshToken(
            token=old_rt,
            client_id="claude-client-1",
            scopes=["mcp.access"],
        )

        new_token = await provider.exchange_refresh_token(client, rt, scopes=["mcp.access", "openid"])

        assert new_token.scope == "mcp.access openid"

    async def test_user_fallback_from_old_access_token(self, provider):
        """If refresh token has no user, should recover from old access token."""
        store = await get_token_store()
        user_data = _serialize_user(_TEST_USER)

        # Access token WITH user
        await store.set(
            "access_token",
            "at-with-user",
            {
                "expires_at": time.time() + ACCESS_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                "user": user_data,
            },
            ACCESS_TOKEN_TTL,
        )
        # Refresh token WITHOUT user
        await store.set(
            "refresh_token",
            "rt-no-user",
            {
                "expires_at": time.time() + REFRESH_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
            },
            REFRESH_TOKEN_TTL,
        )

        client = _make_client_info()
        rt = RefreshToken(
            token="rt-no-user",
            client_id="claude-client-1",
            scopes=["mcp.access"],
        )

        new_token = await provider.exchange_refresh_token(client, rt, scopes=["mcp.access"])

        new_at_doc = await store.get("access_token", new_token.access_token)
        assert new_at_doc["user"]["email"] == "chief@sjifire.org"

    async def test_no_user_anywhere_still_works(self, provider):
        """Rotation should succeed even if no user data is available."""
        store = await get_token_store()

        # Both tokens without user data
        await store.set(
            "access_token",
            "at-no-user",
            {
                "expires_at": time.time() + ACCESS_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
            },
            ACCESS_TOKEN_TTL,
        )
        await store.set(
            "refresh_token",
            "rt-no-user-2",
            {
                "expires_at": time.time() + REFRESH_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
            },
            REFRESH_TOKEN_TTL,
        )

        client = _make_client_info()
        rt = RefreshToken(
            token="rt-no-user-2",
            client_id="claude-client-1",
            scopes=["mcp.access"],
        )

        new_token = await provider.exchange_refresh_token(client, rt, scopes=["mcp.access"])
        assert new_token.access_token
        assert new_token.refresh_token

        # No user data on new tokens
        new_at_doc = await store.get("access_token", new_token.access_token)
        assert "user" not in new_at_doc


# ===========================================================================
# 9. Load access token (UserContext bridge)
# ===========================================================================


class TestLoadAccessToken:
    async def test_unknown_token_returns_none(self, provider):
        result = await provider.load_access_token("nonexistent-at")
        assert result is None

    async def test_valid_token_returns_access_token(self, provider):
        store = await get_token_store()
        now = int(time.time())
        await store.set(
            "access_token",
            "valid-at",
            {
                "expires_at": now + ACCESS_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                "user": _serialize_user(_TEST_USER),
            },
            ACCESS_TOKEN_TTL,
        )

        result = await provider.load_access_token("valid-at")

        assert result is not None
        assert isinstance(result, AccessToken)
        assert result.token == "valid-at"
        assert result.client_id == "claude-client-1"
        assert result.scopes == ["mcp.access"]
        assert result.expires_at == now + ACCESS_TOKEN_TTL

    async def test_sets_user_context(self, provider):
        """load_access_token should call set_current_user for downstream tools."""
        store = await get_token_store()
        await store.set(
            "access_token",
            "ctx-at",
            {
                "expires_at": int(time.time()) + ACCESS_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                "user": _serialize_user(_TEST_USER),
            },
            ACCESS_TOKEN_TTL,
        )

        await provider.load_access_token("ctx-at")

        user = get_current_user()
        assert user.email == "chief@sjifire.org"
        assert user.name == "Fire Chief"
        assert user.user_id == "oid-abc-123"
        assert user.groups == frozenset(["officer-group", "admin-group"])

    async def test_no_user_data_does_not_set_context(self, provider):
        """When there's no user in the token doc, UserContext should not be set."""
        store = await get_token_store()
        await store.set(
            "access_token",
            "no-user-at",
            {
                "expires_at": int(time.time()) + ACCESS_TOKEN_TTL,
                "client_id": "claude-client-1",
                "scopes": ["mcp.access"],
                # no "user" key
            },
            ACCESS_TOKEN_TTL,
        )

        result = await provider.load_access_token("no-user-at")
        assert result is not None

        # Current user should not be set -- get_current_user raises
        with pytest.raises(RuntimeError, match="No authenticated user"):
            get_current_user()

    async def test_expired_token_returns_none(self, provider):
        store = await get_token_store()
        await store.set(
            "access_token",
            "expired-at",
            {
                "expires_at": int(time.time()) - 10,
                "client_id": "c1",
                "scopes": ["mcp.access"],
            },
            ACCESS_TOKEN_TTL,
        )
        store._l1.clear()

        result = await provider.load_access_token("expired-at")
        assert result is None

    async def test_resource_preserved(self, provider):
        store = await get_token_store()
        now = int(time.time())
        await store.set(
            "access_token",
            "res-at",
            {
                "expires_at": now + ACCESS_TOKEN_TTL,
                "client_id": "c1",
                "scopes": ["mcp.access"],
                "resource": "https://api.example.com",
            },
            ACCESS_TOKEN_TTL,
        )

        result = await provider.load_access_token("res-at")
        assert result is not None
        assert result.resource == "https://api.example.com"


# ===========================================================================
# 10. Revoke token
# ===========================================================================


class TestRevokeToken:
    async def test_revoke_access_token(self, provider):
        store = await get_token_store()
        await store.set(
            "access_token",
            "revoke-at",
            {
                "expires_at": int(time.time()) + ACCESS_TOKEN_TTL,
                "client_id": "c1",
                "scopes": ["mcp.access"],
            },
            ACCESS_TOKEN_TTL,
        )

        at = AccessToken(
            token="revoke-at",
            client_id="c1",
            scopes=["mcp.access"],
        )
        await provider.revoke_token(at)

        assert await store.get("access_token", "revoke-at") is None

    async def test_revoke_refresh_token(self, provider):
        store = await get_token_store()
        await store.set(
            "refresh_token",
            "revoke-rt",
            {
                "expires_at": int(time.time()) + REFRESH_TOKEN_TTL,
                "client_id": "c1",
                "scopes": ["mcp.access"],
            },
            REFRESH_TOKEN_TTL,
        )

        rt = RefreshToken(
            token="revoke-rt",
            client_id="c1",
            scopes=["mcp.access"],
        )
        await provider.revoke_token(rt)

        assert await store.get("refresh_token", "revoke-rt") is None

    async def test_revoke_nonexistent_token_no_error(self, provider):
        """Revoking a token that doesn't exist should not raise."""
        at = AccessToken(
            token="ghost-at",
            client_id="c1",
            scopes=["mcp.access"],
        )
        await provider.revoke_token(at)  # should not raise

    async def test_revoke_only_removes_target(self, provider):
        """Revoking one token should not affect others."""
        store = await get_token_store()
        for tok_id in ("keep-at", "remove-at"):
            await store.set(
                "access_token",
                tok_id,
                {
                    "expires_at": int(time.time()) + ACCESS_TOKEN_TTL,
                    "client_id": "c1",
                    "scopes": ["mcp.access"],
                },
                ACCESS_TOKEN_TTL,
            )

        at_remove = AccessToken(token="remove-at", client_id="c1", scopes=["mcp.access"])
        await provider.revoke_token(at_remove)

        assert await store.get("access_token", "remove-at") is None
        assert await store.get("access_token", "keep-at") is not None


# ===========================================================================
# 11. End-to-end flow
# ===========================================================================


class TestEndToEndFlow:
    """Simulate the full OAuth flow through the provider."""

    async def test_full_flow_register_authorize_callback_exchange_load_revoke(self, provider):
        """Complete flow: register -> authorize -> callback -> exchange -> load -> revoke."""
        # 1. Register client
        client = _make_client_info("e2e-client")
        await provider.register_client(client)

        loaded_client = await provider.get_client("e2e-client")
        assert loaded_client is not None

        # 2. Authorize -> get Entra login URL
        params = _make_auth_params()
        url = await provider.authorize(loaded_client, params)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        entra_state = qs["state"][0]

        # 3. Simulate Entra callback
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": "valid.jwt.token"}

        with patch("sjifire.ops.oauth_provider.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            provider._validator = MagicMock()
            provider._validator.validate_token.return_value = _TEST_USER

            request = _build_request({"code": "entra-code", "state": entra_state})
            resp = await provider.handle_callback(request)

        assert resp.status_code == 302
        location = resp.headers["location"]
        mcp_code = parse_qs(urlparse(location).query)["code"][0]

        # 4. Load authorization code
        auth_code_obj = await provider.load_authorization_code(loaded_client, mcp_code)
        assert auth_code_obj is not None
        assert auth_code_obj.code == mcp_code

        # 5. Exchange for tokens
        token = await provider.exchange_authorization_code(loaded_client, auth_code_obj)
        assert token.access_token
        assert token.refresh_token

        # 6. Load access token (bridges to UserContext)
        access = await provider.load_access_token(token.access_token)
        assert access is not None
        user = get_current_user()
        assert user.email == "chief@sjifire.org"

        # 7. Refresh token rotation
        rt_obj = await provider.load_refresh_token(loaded_client, token.refresh_token)
        assert rt_obj is not None

        new_token = await provider.exchange_refresh_token(
            loaded_client, rt_obj, scopes=["mcp.access"]
        )
        assert new_token.access_token != token.access_token
        assert new_token.refresh_token != token.refresh_token

        # Old tokens should be gone
        store = await get_token_store()
        assert await store.get("access_token", token.access_token) is None
        assert await store.get("refresh_token", token.refresh_token) is None

        # New tokens should work
        new_access = await provider.load_access_token(new_token.access_token)
        assert new_access is not None

        # 8. Revoke
        await provider.revoke_token(new_access)
        assert await provider.load_access_token(new_token.access_token) is None


# ===========================================================================
# 12. Token store lazy initialization
# ===========================================================================


class TestTokenStoreLazyInit:
    async def test_store_is_lazily_initialized(self, provider):
        """The token store should only be created on first use."""
        assert provider._token_store is None

        # Trigger store init
        await provider.get_client("anything")

        assert provider._token_store is not None

    async def test_store_reused_across_calls(self, provider):
        """Same store instance should be reused."""
        await provider.get_client("a")
        store1 = provider._token_store

        await provider.get_client("b")
        store2 = provider._token_store

        assert store1 is store2


# ===========================================================================
# 13. TTL constants sanity
# ===========================================================================


class TestTTLConstants:
    def test_access_token_ttl_is_one_hour(self):
        assert ACCESS_TOKEN_TTL == 3600

    def test_refresh_token_ttl_is_one_day(self):
        assert REFRESH_TOKEN_TTL == 86400

    def test_auth_code_ttl_is_five_minutes(self):
        assert AUTH_CODE_TTL == 300

    def test_client_reg_ttl_is_one_day(self):
        assert CLIENT_REG_TTL == 86400

    def test_pending_auth_ttl_is_five_minutes(self):
        assert PENDING_AUTH_TTL == 300
