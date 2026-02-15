"""OAuth Authorization Server provider — MCP proxy to Entra ID.

Claude.ai uses Dynamic Client Registration (RFC 7591) which Entra ID
doesn't support.  This provider acts as the OAuth authorization server
for Claude.ai and delegates the actual user login to Entra ID.

Flow::

    Claude.ai  ←→  MCP Server (OAuth AS)  ←→  Entra ID (user login)

All state (tokens, auth codes, client registrations, pending auth flows)
is stored in Cosmos DB so it survives restarts and works across replicas.
A per-replica L1 TTLCache in the TokenStore reduces Cosmos reads.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from sjifire.ops.auth import EntraTokenValidator, set_current_user
from sjifire.ops.token_store import _deserialize_user, _serialize_user, get_token_store

if TYPE_CHECKING:
    from sjifire.ops.token_store import TokenStore

logger = logging.getLogger(__name__)


def _serialize_auth_params(params: AuthorizationParams) -> dict:
    """Convert AuthorizationParams to a JSON-safe dict for Cosmos storage."""
    return {
        "state": params.state,
        "scopes": params.scopes,
        "code_challenge": params.code_challenge,
        "redirect_uri": str(params.redirect_uri),
        "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
        "resource": str(params.resource) if params.resource else None,
    }


def _deserialize_auth_params(data: dict) -> AuthorizationParams:
    """Reconstruct AuthorizationParams from a stored dict."""
    return AuthorizationParams(
        state=data.get("state"),
        scopes=data.get("scopes", ["mcp.access"]),
        code_challenge=data.get("code_challenge"),
        redirect_uri=data.get("redirect_uri"),
        redirect_uri_provided_explicitly=data.get("redirect_uri_provided_explicitly", True),
        resource=data.get("resource"),
    )


# Token lifetimes (seconds)
ACCESS_TOKEN_TTL = 3600  # 1 hour
REFRESH_TOKEN_TTL = 86400  # 24 hours
AUTH_CODE_TTL = 300  # 5 minutes
CLIENT_REG_TTL = 86400  # 24 hours
PENDING_AUTH_TTL = 300  # 5 minutes (matches auth code)


class EntraOAuthProvider:
    """OAuth AS provider that delegates authentication to Entra ID.

    Implements the MCP SDK's ``OAuthAuthorizationServerProvider`` protocol.
    Handles dynamic client registration from Claude.ai, redirects users to
    Entra ID for login, and mints its own opaque tokens backed by the
    authenticated user identity.
    """

    def __init__(
        self,
        tenant_id: str,
        api_client_id: str,
        server_url: str,
        client_secret: str = "",
    ) -> None:
        """Initialize with Entra ID tenant and app registration details."""
        self.tenant_id = tenant_id
        self.api_client_id = api_client_id
        self.server_url = server_url.rstrip("/")
        self.client_secret = client_secret

        self._validator = EntraTokenValidator(tenant_id, api_client_id)

        # Lazy-initialized Cosmos-backed store (shared across replicas)
        self._token_store: TokenStore | None = None

    async def _store(self) -> TokenStore:
        """Get the shared TokenStore (lazy init)."""
        if self._token_store is None:
            self._token_store = await get_token_store()
        return self._token_store

    # ------------------------------------------------------------------
    # Client Registration (RFC 7591)
    # ------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Look up a dynamically registered client from Cosmos DB."""
        store = await self._store()
        doc = await store.get("client_reg", client_id)
        if doc is None:
            return None
        return OAuthClientInformationFull.model_validate(doc["client_data"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Save a dynamically registered client (RFC 7591) to Cosmos DB."""
        if client_info.client_id:
            store = await self._store()
            await store.set(
                "client_reg",
                client_info.client_id,
                {"client_data": client_info.model_dump(mode="json")},
                CLIENT_REG_TTL,
            )

    # ------------------------------------------------------------------
    # Authorization — redirect to Entra ID
    # ------------------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Save MCP params and redirect the user to Entra ID login."""
        # State for our Entra request (links callback back to MCP flow)
        entra_state = secrets.token_urlsafe(32)

        # PKCE verifier/challenge for our Entra request
        entra_code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(entra_code_verifier.encode()).digest()
        entra_code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

        # Store pending auth in Cosmos so any replica can handle the callback
        store = await self._store()
        await store.set(
            "pending_auth",
            entra_state,
            {
                "mcp_params": _serialize_auth_params(params),
                "client_id": client.client_id,
                "entra_code_verifier": entra_code_verifier,
                "expires_at": time.time() + PENDING_AUTH_TTL,
            },
            PENDING_AUTH_TTL,
        )

        entra_params = {
            "client_id": self.api_client_id,
            "response_type": "code",
            "redirect_uri": f"{self.server_url}/callback",
            "scope": "openid profile email",
            "state": entra_state,
            "code_challenge": entra_code_challenge,
            "code_challenge_method": "S256",
            "response_mode": "query",
        }

        return (
            f"https://login.microsoftonline.com/{self.tenant_id}"
            f"/oauth2/v2.0/authorize?{urlencode(entra_params)}"
        )

    # ------------------------------------------------------------------
    # Entra callback — exchange code, mint MCP auth code
    # ------------------------------------------------------------------

    async def handle_callback(self, request: Request) -> Response:
        """Handle Entra ID redirect after user login.

        Exchanges the Entra authorization code for an id_token, extracts
        user identity, creates an MCP authorization code, and redirects
        back to Claude.ai's redirect_uri.
        """
        error = request.query_params.get("error")
        if error:
            desc = request.query_params.get("error_description", "")
            logger.error("Entra ID error: %s — %s", error, desc)
            return Response(f"Authentication failed: {error}", status_code=400)

        entra_code = request.query_params.get("code")
        entra_state = request.query_params.get("state")

        if not entra_code or not entra_state:
            return Response("Missing code or state", status_code=400)

        store = await self._store()
        pending = await store.get("pending_auth", entra_state)
        if not pending:
            return Response("Invalid or expired state", status_code=400)
        await store.delete("pending_auth", entra_state)

        mcp_params = _deserialize_auth_params(pending["mcp_params"])
        client_id: str = pending["client_id"]
        entra_code_verifier: str = pending["entra_code_verifier"]

        # Exchange Entra auth code for tokens
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        token_data = {
            "client_id": self.api_client_id,
            "grant_type": "authorization_code",
            "code": entra_code,
            "redirect_uri": f"{self.server_url}/callback",
            "code_verifier": entra_code_verifier,
        }
        if not self.client_secret:
            logger.error("ENTRA_MCP_API_CLIENT_SECRET is required for token exchange")
            return Response("Server misconfiguration: missing client secret", status_code=500)
        token_data["client_secret"] = self.client_secret

        async with httpx.AsyncClient() as http:
            resp = await http.post(token_url, data=token_data)

        if resp.status_code != 200:
            logger.error("Entra token exchange failed: %s", resp.text)
            return Response("Token exchange failed", status_code=502)

        id_token = resp.json().get("id_token")
        if not id_token:
            return Response("No id_token in response", status_code=502)

        # Validate id_token signature and extract user claims
        try:
            user = self._validator.validate_token(id_token)
        except Exception:
            logger.exception("id_token validation failed")
            return Response("Token validation failed", status_code=502)

        # Mint an MCP authorization code and store in Cosmos
        mcp_code = secrets.token_urlsafe(32)
        now = time.time()
        scopes = mcp_params.scopes or ["mcp.access"]

        store = await self._store()
        await store.set(
            "auth_code",
            mcp_code,
            {
                "expires_at": now + AUTH_CODE_TTL,
                "client_id": client_id,
                "scopes": scopes,
                "code_challenge": mcp_params.code_challenge,
                "redirect_uri": str(mcp_params.redirect_uri),
                "redirect_uri_provided_explicitly": mcp_params.redirect_uri_provided_explicitly,
                "resource": str(mcp_params.resource) if mcp_params.resource else None,
                "user": _serialize_user(user),
            },
            AUTH_CODE_TTL,
        )

        redirect_url = construct_redirect_uri(
            str(mcp_params.redirect_uri),
            code=mcp_code,
            state=mcp_params.state,
        )
        return RedirectResponse(redirect_url, status_code=302)

    # ------------------------------------------------------------------
    # Token exchange
    # ------------------------------------------------------------------

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        """Load an authorization code if still valid."""
        store = await self._store()
        doc = await store.get("auth_code", authorization_code)
        if doc is None:
            return None

        return AuthorizationCode(
            code=authorization_code,
            scopes=doc.get("scopes", ["mcp.access"]),
            expires_at=doc["expires_at"],
            client_id=doc["client_id"],
            code_challenge=doc.get("code_challenge"),
            redirect_uri=doc.get("redirect_uri"),
            redirect_uri_provided_explicitly=doc.get("redirect_uri_provided_explicitly", True),
            resource=doc.get("resource"),
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Mint MCP access + refresh tokens and associate with user identity."""
        store = await self._store()

        # Get the user from the auth code document before deleting it.
        # If the code was already consumed (race), reject the exchange.
        code_doc = await store.get("auth_code", authorization_code.code)
        if code_doc is None:
            raise ValueError("Authorization code already consumed or expired")
        user_data = code_doc.get("user")
        await store.delete("auth_code", authorization_code.code)

        now = int(time.time())
        access_token_str = secrets.token_urlsafe(32)
        refresh_token_str = secrets.token_urlsafe(32)
        scopes = authorization_code.scopes

        token_data = {
            "expires_at": now + ACCESS_TOKEN_TTL,
            "client_id": authorization_code.client_id,
            "scopes": scopes,
            "resource": getattr(authorization_code, "resource", None),
        }
        if user_data:
            token_data["user"] = user_data

        await store.set("access_token", access_token_str, token_data, ACCESS_TOKEN_TTL)

        refresh_data = {
            "expires_at": now + REFRESH_TOKEN_TTL,
            "client_id": authorization_code.client_id,
            "scopes": scopes,
        }
        if user_data:
            refresh_data["user"] = user_data

        await store.set("refresh_token", refresh_token_str, refresh_data, REFRESH_TOKEN_TTL)

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",  # noqa: S106
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(scopes),
            refresh_token=refresh_token_str,
        )

    # ------------------------------------------------------------------
    # Refresh token
    # ------------------------------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        """Load a refresh token if still valid."""
        store = await self._store()
        doc = await store.get("refresh_token", refresh_token)
        if doc is None:
            return None

        return RefreshToken(
            token=refresh_token,
            client_id=doc["client_id"],
            scopes=doc.get("scopes", ["mcp.access"]),
            expires_at=doc.get("expires_at"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotate both access and refresh tokens."""
        store = await self._store()

        # Get user from refresh token doc (no scan needed — user is embedded)
        rt_doc = await store.get("refresh_token", refresh_token.token)
        user_data = rt_doc.get("user") if rt_doc else None

        # If user not on refresh token, try getting from old access token
        if user_data is None:
            old_doc = await store.delete_by_client("access_token", refresh_token.client_id)
            if old_doc:
                user_data = old_doc.get("user")
        else:
            # Still revoke old access tokens for this client
            await store.delete_by_client("access_token", refresh_token.client_id)

        await store.delete("refresh_token", refresh_token.token)

        now = int(time.time())
        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        effective_scopes = scopes or refresh_token.scopes

        access_data = {
            "expires_at": now + ACCESS_TOKEN_TTL,
            "client_id": refresh_token.client_id,
            "scopes": effective_scopes,
        }
        if user_data:
            access_data["user"] = user_data

        await store.set("access_token", new_access, access_data, ACCESS_TOKEN_TTL)

        refresh_data = {
            "expires_at": now + REFRESH_TOKEN_TTL,
            "client_id": refresh_token.client_id,
            "scopes": effective_scopes,
        }
        if user_data:
            refresh_data["user"] = user_data

        await store.set("refresh_token", new_refresh, refresh_data, REFRESH_TOKEN_TTL)

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",  # noqa: S106
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(effective_scopes),
            refresh_token=new_refresh,
        )

    # ------------------------------------------------------------------
    # Access token — bridges SDK auth to our UserContext
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Load access token and set UserContext for downstream MCP tools."""
        store = await self._store()
        doc = await store.get("access_token", token)
        if doc is None:
            return None

        # Bridge: set our UserContext so tools call get_current_user() unchanged
        user_data = doc.get("user")
        if user_data:
            user = _deserialize_user(user_data)
            set_current_user(user)
            logger.debug("Authenticated: %s (%s)", user.name, user.email)

        return AccessToken(
            token=token,
            client_id=doc["client_id"],
            scopes=doc.get("scopes", ["mcp.access"]),
            expires_at=doc["expires_at"],
            resource=doc.get("resource"),
        )

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke an access or refresh token."""
        store = await self._store()
        if isinstance(token, AccessToken):
            await store.delete("access_token", token.token)
        elif isinstance(token, RefreshToken):
            await store.delete("refresh_token", token.token)
