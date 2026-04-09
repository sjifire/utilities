"""Kiosk token signing and verification using ``itsdangerous``.

Tokens are cryptographically signed with a server secret stored in
Azure Key Vault (``KIOSK_SIGNING_KEY``).  Validation is pure
signature verification — no database lookup required.

To revoke **all** tokens, rotate the signing key in Key Vault and
redeploy.  Individual revocation is unnecessary for 1-3 station TVs.

In dev mode (no signing key set), a deterministic fallback key is used
so ``uv run ops-server`` + ``uv run kiosk-token create`` work locally
without any Azure setup.
"""

import logging
import os

from itsdangerous import BadSignature, URLSafeSerializer

logger = logging.getLogger(__name__)

_DEV_KEY = "kiosk-dev-signing-key-not-for-production"


def _get_serializer() -> URLSafeSerializer:
    """Build a serializer using the signing key from the environment."""
    key = os.getenv("KIOSK_SIGNING_KEY", "")
    if not key:
        logger.warning("No KIOSK_SIGNING_KEY set — using dev fallback (not for production)")
        key = _DEV_KEY
    return URLSafeSerializer(key, salt="kiosk-token")


def create_token(label: str = "") -> str:
    """Sign a new kiosk token.

    Args:
        label: Human-readable label embedded in the token payload.

    Returns:
        URL-safe signed token string.
    """
    s = _get_serializer()
    return s.dumps({"label": label})


def validate_token(token: str) -> dict | None:
    """Verify a kiosk token signature.

    Args:
        token: The signed token string from the URL.

    Returns:
        The token payload dict if valid, or ``None`` if the signature
        is invalid or the key has been rotated.
    """
    s = _get_serializer()
    try:
        return s.loads(token)
    except BadSignature:
        return None
