"""Entra ID JWT validation and user context for the ops server.

Validates access tokens issued by Entra ID, extracts user identity,
and checks group membership for role-based access control.

Group membership is used internally only -- never exposed to tools or users.
"""

import base64
import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field

import jwt
from jwt import PyJWKClient
from starlette.requests import Request

logger = logging.getLogger(__name__)

# Context variable holding the authenticated user for the current request
_current_user: ContextVar[UserContext | None] = ContextVar("current_user", default=None)

# Editor group ID — read once, supports old env var name for transition
_EDITOR_GROUP_ID: str | None = None


def _get_editor_group_id() -> str:
    """Return the Entra ID group ID for incident report editors."""
    global _EDITOR_GROUP_ID
    if _EDITOR_GROUP_ID is None:
        _EDITOR_GROUP_ID = os.getenv("ENTRA_REPORT_EDITORS_GROUP_ID", "")
    return _EDITOR_GROUP_ID


@dataclass(frozen=True)
class UserContext:
    """Authenticated user extracted from the Entra ID JWT."""

    email: str
    name: str
    user_id: str  # Entra object ID (oid claim)
    groups: frozenset[str] = field(default_factory=frozenset)  # Group object IDs

    @property
    def is_editor(self) -> bool:
        """Check if user is in the incident report editors group.

        The group ID is configured via ENTRA_REPORT_EDITORS_GROUP_ID.
        If not configured, no one has editor privileges (safe default).
        """
        group_id = _get_editor_group_id()
        return bool(group_id and group_id in self.groups)


# ---------------------------------------------------------------------------
# Live Graph API group membership check
# ---------------------------------------------------------------------------


async def check_is_editor(user_id: str, *, fallback: bool = False) -> bool:
    """Check group membership via Graph API.

    Falls back to the token-based ``is_editor`` property if the Graph API
    call fails (e.g., missing credentials in dev mode).

    Args:
        user_id: Entra object ID of the user
        fallback: Value of ``user.is_editor`` to use if Graph API fails
    """
    group_id = _get_editor_group_id()
    if not group_id:
        return False

    try:
        return await _check_member_groups(user_id, group_id)
    except Exception:
        logger.debug("Graph API group check failed for %s, using fallback", user_id, exc_info=True)
        return fallback


async def _check_member_groups(user_id: str, group_id: str) -> bool:
    """Call MS Graph checkMemberGroups to verify membership."""
    import httpx

    tenant_id = os.getenv("ENTRA_MCP_API_TENANT_ID") or os.getenv("MS_GRAPH_TENANT_ID", "")
    client_id = os.getenv("MS_GRAPH_CLIENT_ID", "")
    client_secret = os.getenv("MS_GRAPH_CLIENT_SECRET", "")

    if not all([tenant_id, client_id, client_secret]):
        raise RuntimeError("Graph API credentials not configured")

    # Get app-only token
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        # Check group membership
        resp = await client.post(
            f"https://graph.microsoft.com/v1.0/users/{user_id}/checkMemberGroups",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"groupIds": [group_id]},
        )
        resp.raise_for_status()
        return group_id in resp.json().get("value", [])


def get_current_user() -> UserContext:
    """Get the authenticated user for the current request.

    Raises:
        RuntimeError: If no user is authenticated in the current context.
    """
    user = _current_user.get()
    if user is not None:
        return user

    raise RuntimeError("No authenticated user in context")


def set_current_user(user: UserContext) -> None:
    """Set the authenticated user for the current request."""
    _current_user.set(user)


def get_easyauth_user(request: Request) -> UserContext | None:
    """Extract user identity from Azure Container Apps EasyAuth headers.

    EasyAuth injects ``X-MS-CLIENT-PRINCIPAL`` as a Base64-encoded JSON
    blob containing the authenticated user's claims.  Returns None when
    the header is absent (unauthenticated request).
    """
    principal_b64 = request.headers.get("X-MS-CLIENT-PRINCIPAL")
    if not principal_b64:
        return None

    try:
        data = json.loads(base64.b64decode(principal_b64))
    except (ValueError, KeyError):
        logger.warning("Failed to decode X-MS-CLIENT-PRINCIPAL header")
        return None

    claims_list = data.get("claims", [])

    # Build single-value lookup and collect multi-value groups
    claims: dict[str, str] = {}
    groups: set[str] = set()
    for c in claims_list:
        typ, val = c.get("typ", ""), c.get("val", "")
        claims[typ] = val
        if typ == "groups":
            groups.add(val)

    email = (
        claims.get("preferred_username")
        or claims.get("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress")
        or ""
    )
    name = claims.get("name", email.split("@")[0] if email else "Unknown")
    user_id = claims.get("http://schemas.microsoft.com/identity/claims/objectidentifier", "")

    return UserContext(
        email=email.lower(),
        name=name,
        user_id=user_id,
        groups=frozenset(groups),
    )


class EntraTokenValidator:
    """Validates Entra ID JWT access tokens.

    Uses the JWKS endpoint to verify token signatures and checks
    audience, issuer, and extracts user claims.
    """

    def __init__(
        self,
        tenant_id: str,
        api_client_id: str,
    ) -> None:
        """Initialize validator.

        Args:
            tenant_id: Entra ID tenant ID
            api_client_id: Client ID of the API app registration (audience)
        """
        self.tenant_id = tenant_id
        self.api_client_id = api_client_id
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        self._jwks_client: PyJWKClient | None = None

    @property
    def jwks_client(self) -> PyJWKClient:
        """Lazily create and cache the JWKS client."""
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(self.jwks_url, cache_keys=True)
        return self._jwks_client

    def validate_token(self, token: str) -> UserContext:
        """Validate a Bearer token and return the user context.

        Args:
            token: Raw JWT access token (without "Bearer " prefix)

        Returns:
            UserContext with user identity and group memberships

        Raises:
            jwt.InvalidTokenError: If the token is invalid
        """
        signing_key = self.jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.api_client_id,
            issuer=self.issuer,
        )

        email = payload.get("preferred_username") or payload.get("email") or payload.get("upn", "")
        name = payload.get("name", email.split("@")[0] if email else "Unknown")
        user_id = payload.get("oid", "")

        # Groups come from the "groups" claim if the app registration
        # is configured to include group IDs in the token
        groups = frozenset(payload.get("groups", []))

        return UserContext(
            email=email.lower(),
            name=name,
            user_id=user_id,
            groups=groups,
        )
