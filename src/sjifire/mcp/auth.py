"""Entra ID JWT validation and user context for the MCP server.

Validates access tokens issued by Entra ID, extracts user identity,
and checks group membership for role-based access control.

Group membership is used internally only -- never exposed to tools or users.
"""

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field

import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

# Context variable holding the authenticated user for the current request
_current_user: ContextVar[UserContext | None] = ContextVar("current_user", default=None)


@dataclass(frozen=True)
class UserContext:
    """Authenticated user extracted from the Entra ID JWT."""

    email: str
    name: str
    user_id: str  # Entra object ID (oid claim)
    groups: frozenset[str] = field(default_factory=frozenset)  # Group object IDs

    @property
    def is_officer(self) -> bool:
        """Check if user is in the incident officers group.

        The officer group ID is configured via ENTRA_MCP_OFFICER_GROUP_ID.
        If not configured, no one has officer privileges (safe default).
        """
        officer_group = os.getenv("ENTRA_MCP_OFFICER_GROUP_ID", "")
        return bool(officer_group and officer_group in self.groups)


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
