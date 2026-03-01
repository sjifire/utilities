"""Entra ID JWT validation and user context for the ops server.

Validates access tokens issued by Entra ID, extracts user identity,
and checks group membership for role-based access control.

Group membership is used internally only -- never exposed to tools or users.
"""

import base64
import json
import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

import jwt
from jwt import PyJWKClient
from starlette.requests import Request

logger = logging.getLogger(__name__)

# Cached Graph API app-only token (shared across requests)
_graph_token: str | None = None
_graph_token_expires: float = 0

# Cached group membership results: {(user_id, group_id): (is_member, expires_at)}
_group_cache: dict[tuple[str, str], tuple[bool, float]] = {}
_GROUP_CACHE_TTL = 60  # seconds

# Legacy alias — kept for code that references the editor cache directly
_editor_cache = _group_cache
_EDITOR_CACHE_TTL = _GROUP_CACHE_TTL

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


async def check_group_membership(user_id: str, group_id: str, *, fallback: bool = False) -> bool:
    """Check whether a user belongs to an Entra ID group (cached 60s).

    Args:
        user_id: Entra object ID of the user
        group_id: Entra object ID of the group
        fallback: Value to return if the Graph API call fails
    """
    if not group_id or not user_id:
        return fallback

    cache_key = (user_id, group_id)
    cached = _group_cache.get(cache_key)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    try:
        result = await _check_member_groups(user_id, group_id)
        _group_cache[cache_key] = (result, time.monotonic() + _GROUP_CACHE_TTL)
        return result
    except Exception:
        logger.debug("Graph API group check failed for %s, using fallback", user_id, exc_info=True)
        return fallback


async def check_is_editor(user_id: str, *, fallback: bool = False, email: str = "") -> bool:
    """Check editor group membership via Graph API (cached for 60s).

    Convenience wrapper around ``check_group_membership`` for the
    incident report editors group.

    When ``user_id`` is empty (e.g., stale Centrifugo connections pre-dating
    the user_id field), resolves the object ID from ``email`` via Graph API.

    Args:
        user_id: Entra object ID of the user
        fallback: Value of ``user.is_editor`` to use if Graph API fails
        email: User email, used to resolve user_id when empty
    """
    group_id = _get_editor_group_id()
    if not group_id:
        return False

    # Resolve user_id from email if missing (stale connections)
    if not user_id and email:
        user_id = await _resolve_user_id(email)
        if not user_id:
            logger.warning("Cannot resolve user_id for %s, using fallback=%s", email, fallback)
            return fallback

    return await check_group_membership(user_id, group_id, fallback=fallback)


# Cache resolved user_id by email: {email: (user_id, expires_at)}
_user_id_cache: dict[str, tuple[str, float]] = {}


async def _resolve_user_id(email: str) -> str:
    """Resolve Entra object ID from email via Graph API (cached)."""
    cached = _user_id_cache.get(email)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    try:
        import httpx

        access_token = await _get_graph_app_token()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/users/{email}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"$select": "id"},
            )
            resp.raise_for_status()
            user_id = resp.json().get("id", "")
            if user_id:
                _user_id_cache[email] = (user_id, time.monotonic() + _EDITOR_CACHE_TTL)
                logger.info("Resolved user_id for %s: %s", email, user_id)
            return user_id
    except Exception:
        logger.warning("Failed to resolve user_id for %s", email, exc_info=True)
        return ""


async def _get_graph_app_token() -> str:
    """Get a cached app-only Graph API token, refreshing if expired."""
    global _graph_token, _graph_token_expires

    if _graph_token and _graph_token_expires > time.monotonic():
        return _graph_token

    import httpx

    tenant_id = os.getenv("ENTRA_MCP_API_TENANT_ID") or os.getenv("MS_GRAPH_TENANT_ID", "")
    client_id = os.getenv("MS_GRAPH_CLIENT_ID", "")
    client_secret = os.getenv("MS_GRAPH_CLIENT_SECRET", "")

    if not all([tenant_id, client_id, client_secret]):
        raise RuntimeError("Graph API credentials not configured")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _graph_token = data["access_token"]
    # Token typically valid for 3600s; refresh 5 min early
    _graph_token_expires = time.monotonic() + data.get("expires_in", 3600) - 300
    return _graph_token


async def _check_member_groups(user_id: str, group_id: str) -> bool:
    """Call MS Graph checkMemberGroups to verify membership."""
    import httpx

    access_token = await _get_graph_app_token()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://graph.microsoft.com/v1.0/users/{user_id}/checkMemberGroups",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"groupIds": [group_id]},
        )
        resp.raise_for_status()
        return group_id in resp.json().get("value", [])


async def check_doc_view_access(
    doc_created_by: str,
    personnel_emails: frozenset[str],
    user_email: str,
    is_editor: bool,
) -> bool:
    """Check if user can view a document (creator, personnel, or editor)."""
    if user_email == doc_created_by or user_email in personnel_emails:
        return True
    try:
        user = get_current_user()
        return await check_is_editor(user.user_id, fallback=is_editor)
    except RuntimeError:
        return is_editor


async def check_doc_edit_access(
    doc_created_by: str,
    user_email: str,
    is_editor: bool,
) -> bool:
    """Check if user can edit a document (creator or editor)."""
    if user_email == doc_created_by:
        return True
    try:
        user = get_current_user()
        return await check_is_editor(user.user_id, fallback=is_editor)
    except RuntimeError:
        return is_editor


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


def get_request_user(request: Request) -> UserContext | None:
    """Extract authenticated user from EasyAuth headers and set context.

    Convenience wrapper used by HTTP route handlers — combines
    ``get_easyauth_user`` + ``set_current_user`` in one call.
    """
    user = get_easyauth_user(request)
    if user:
        _current_user.set(user)
    return user


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
