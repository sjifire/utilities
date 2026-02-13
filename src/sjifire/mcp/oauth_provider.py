"""OAuth Authorization Server provider — MCP proxy to Entra ID.

Claude.ai uses Dynamic Client Registration (RFC 7591) which Entra ID
doesn't support.  This provider acts as the OAuth authorization server
for Claude.ai and delegates the actual user login to Entra ID.

Flow::

    Claude.ai  ←→  MCP Server (OAuth AS)  ←→  Entra ID (user login)

In-memory stores (dict-based) are fine for a single-replica Container App
that scales to zero — sessions naturally expire on restart.
"""

import base64
import hashlib
import logging
import secrets
import time
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

from sjifire.mcp.auth import EntraTokenValidator, UserContext, set_current_user

logger = logging.getLogger(__name__)

# Token lifetimes (seconds)
ACCESS_TOKEN_TTL = 3600  # 1 hour
REFRESH_TOKEN_TTL = 86400  # 24 hours
AUTH_CODE_TTL = 300  # 5 minutes


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

        # In-memory stores
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        # Entra state → {mcp_params, client_id, entra_code_verifier}
        self._pending_auth: dict[str, dict] = {}
        # token string → UserContext
        self._token_user_map: dict[str, UserContext] = {}

    # ------------------------------------------------------------------
    # Client Registration (RFC 7591)
    # ------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Look up a dynamically registered client."""
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Save a dynamically registered client (RFC 7591)."""
        if client_info.client_id:
            self._clients[client_info.client_id] = client_info

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

        self._pending_auth[entra_state] = {
            "mcp_params": params,
            "client_id": client.client_id,
            "entra_code_verifier": entra_code_verifier,
        }

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

        pending = self._pending_auth.pop(entra_state, None)
        if not pending:
            return Response("Invalid or expired state", status_code=400)

        mcp_params: AuthorizationParams = pending["mcp_params"]
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
        if self.client_secret:
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

        # Mint an MCP authorization code
        mcp_code = secrets.token_urlsafe(32)
        now = time.time()

        self._auth_codes[mcp_code] = AuthorizationCode(
            code=mcp_code,
            scopes=mcp_params.scopes or ["mcp.access"],
            expires_at=now + AUTH_CODE_TTL,
            client_id=client_id,
            code_challenge=mcp_params.code_challenge,
            redirect_uri=mcp_params.redirect_uri,
            redirect_uri_provided_explicitly=mcp_params.redirect_uri_provided_explicitly,
            resource=mcp_params.resource,
        )

        # Stash user identity keyed by auth code for the token exchange step
        self._token_user_map[f"code:{mcp_code}"] = user

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
        code_obj = self._auth_codes.get(authorization_code)
        if code_obj and code_obj.expires_at > time.time():
            return code_obj
        return None

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Mint MCP access + refresh tokens and associate with user identity."""
        self._auth_codes.pop(authorization_code.code, None)
        user = self._token_user_map.pop(f"code:{authorization_code.code}", None)

        now = int(time.time())
        access_token_str = secrets.token_urlsafe(32)
        refresh_token_str = secrets.token_urlsafe(32)
        scopes = authorization_code.scopes

        self._access_tokens[access_token_str] = AccessToken(
            token=access_token_str,
            client_id=authorization_code.client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL,
            resource=authorization_code.resource,
        )

        self._refresh_tokens[refresh_token_str] = RefreshToken(
            token=refresh_token_str,
            client_id=authorization_code.client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL,
        )

        if user:
            self._token_user_map[access_token_str] = user

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
        rt = self._refresh_tokens.get(refresh_token)
        if rt and (rt.expires_at is None or rt.expires_at > int(time.time())):
            return rt
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotate both access and refresh tokens."""
        # Find and remove old access token for this client to transfer user
        old_user: UserContext | None = None
        for tok_str, at in list(self._access_tokens.items()):
            if at.client_id == refresh_token.client_id:
                old_user = self._token_user_map.pop(tok_str, None)
                del self._access_tokens[tok_str]
                break

        self._refresh_tokens.pop(refresh_token.token, None)

        now = int(time.time())
        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        effective_scopes = scopes or refresh_token.scopes

        self._access_tokens[new_access] = AccessToken(
            token=new_access,
            client_id=refresh_token.client_id,
            scopes=effective_scopes,
            expires_at=now + ACCESS_TOKEN_TTL,
        )

        self._refresh_tokens[new_refresh] = RefreshToken(
            token=new_refresh,
            client_id=refresh_token.client_id,
            scopes=effective_scopes,
            expires_at=now + REFRESH_TOKEN_TTL,
        )

        if old_user:
            self._token_user_map[new_access] = old_user

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
        at = self._access_tokens.get(token)
        if at is None:
            return None
        if at.expires_at is not None and at.expires_at < int(time.time()):
            return None

        # Bridge: set our UserContext so tools call get_current_user() unchanged
        user = self._token_user_map.get(token)
        if user:
            set_current_user(user)
            logger.debug("Authenticated: %s (%s)", user.name, user.email)

        return at

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke an access or refresh token."""
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
            self._token_user_map.pop(token.token, None)
        elif isinstance(token, RefreshToken):
            self._refresh_tokens.pop(token.token, None)
