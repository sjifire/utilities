"""Two-layer token store: TTLCache (L1) backed by Cosmos DB (L2).

Stores OAuth tokens (access, refresh, auth codes) in Cosmos DB for
multi-replica and restart resilience. A per-replica L1 TTLCache reduces
Cosmos DB reads on every ``load_access_token`` call.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory dict
for local development with ``mcp dev``.
"""

import asyncio
import contextlib
import logging
import time
from typing import ClassVar

from cachetools import TTLCache

from sjifire.core.config import get_cosmos_container
from sjifire.ops.auth import UserContext

logger = logging.getLogger(__name__)
CONTAINER_NAME = "oauth-tokens"


def _serialize_user(user: UserContext) -> dict:
    """Convert UserContext to a JSON-safe dict."""
    return {
        "email": user.email,
        "name": user.name,
        "user_id": user.user_id,
        "groups": sorted(user.groups),
    }


def _deserialize_user(data: dict) -> UserContext:
    """Reconstruct UserContext from a stored dict."""
    return UserContext(
        email=data["email"],
        name=data["name"],
        user_id=data["user_id"],
        groups=frozenset(data.get("groups", [])),
    )


class TokenStore:
    """Two-layer token store with L1 TTLCache and Cosmos DB backing.

    Singleton — call ``get_token_store()`` to obtain the shared instance.

    Document shape::

        {
            "id": "<token_string>",
            "token_type": "access_token",  # partition key
            "ttl": 3600,                   # Cosmos auto-delete
            "expires_at": 1707750000,
            "client_id": "...",
            "scopes": ["mcp.access"],
            "user": {"email": "...", "name": "...", ...}
        }
    """

    # Shared in-memory backing store (dev mode, persists for server lifetime)
    _memory: ClassVar[dict[str, dict]] = {}

    def __init__(self) -> None:
        """Create store. Call ``initialize()`` to connect."""
        self._l1: TTLCache = TTLCache(maxsize=256, ttl=120)
        self._container = None
        self._in_memory = False

    async def initialize(self) -> None:
        """Get a container client from the shared Cosmos connection pool."""
        self._container = await get_cosmos_container(CONTAINER_NAME)
        if self._container is None:
            self._in_memory = True

    async def get(self, token_type: str, token_id: str) -> dict | None:
        """Load a token document by type and ID.

        Checks L1 cache first, then Cosmos DB (or dev-mode dict).

        Args:
            token_type: One of "access_token", "refresh_token", "auth_code"
            token_id: The opaque token string

        Returns:
            Document dict if found and not expired, None otherwise
        """
        cache_key = f"{token_type}:{token_id}"

        # L1 hit
        cached = self._l1.get(cache_key)
        if cached is not None:
            if cached.get("expires_at") and cached["expires_at"] < time.time():
                self._l1.pop(cache_key, None)
                return None
            return cached

        # L2: dev-mode dict or Cosmos
        if self._in_memory:
            doc = self._memory.get(cache_key)
        else:
            try:
                doc = await self._container.read_item(
                    item=token_id,
                    partition_key=token_type,
                )
            except Exception as exc:
                # CosmosResourceNotFoundError → expected (token doesn't exist)
                # Other exceptions → log as warning (transient Cosmos issue)
                exc_name = type(exc).__name__
                if "NotFound" not in exc_name:
                    logger.warning("TokenStore.get(%s, %s) failed: %s", token_type, token_id, exc)
                doc = None

        if doc is None:
            return None

        # Check expiry (defense-in-depth; Cosmos TTL also handles this)
        if doc.get("expires_at") and doc["expires_at"] < time.time():
            return None

        # Populate L1
        self._l1[cache_key] = doc
        return doc

    async def set(
        self,
        token_type: str,
        token_id: str,
        data: dict,
        ttl: int,
    ) -> None:
        """Store a token document.

        Writes to both L1 cache and Cosmos DB (or dev-mode dict).

        Args:
            token_type: One of "access_token", "refresh_token", "auth_code"
            token_id: The opaque token string
            data: Document fields (client_id, scopes, user, etc.)
            ttl: Time-to-live in seconds (Cosmos auto-deletes expired docs)
        """
        doc = {
            "id": token_id,
            "token_type": token_type,
            "ttl": ttl,
            **data,
        }

        cache_key = f"{token_type}:{token_id}"
        self._l1[cache_key] = doc

        if self._in_memory:
            self._memory[cache_key] = doc
        else:
            await self._container.upsert_item(body=doc)

    async def delete(self, token_type: str, token_id: str) -> None:
        """Remove a token document.

        Args:
            token_type: One of "access_token", "refresh_token", "auth_code"
            token_id: The opaque token string
        """
        cache_key = f"{token_type}:{token_id}"
        self._l1.pop(cache_key, None)

        if self._in_memory:
            self._memory.pop(cache_key, None)
        else:
            with contextlib.suppress(Exception):
                await self._container.delete_item(
                    item=token_id,
                    partition_key=token_type,
                )

    async def delete_by_client(
        self,
        token_type: str,
        client_id: str,
    ) -> dict | None:
        """Delete all tokens of a type for a given client_id.

        Used during token rotation to revoke old access tokens.

        Args:
            token_type: Token type to search (e.g. "access_token")
            client_id: The OAuth client ID

        Returns:
            The first matching document (for extracting user), or None
        """
        first_doc: dict | None = None

        if self._in_memory:
            to_delete = []
            for key, doc in self._memory.items():
                if doc.get("token_type") == token_type and doc.get("client_id") == client_id:
                    if first_doc is None:
                        first_doc = doc
                    to_delete.append(key)
            for key in to_delete:
                self._memory.pop(key, None)
                self._l1.pop(key, None)
        else:
            query = "SELECT * FROM c WHERE c.client_id = @cid AND c.token_type = @type"
            parameters = [
                {"name": "@cid", "value": client_id},
                {"name": "@type", "value": token_type},
            ]
            async for item in self._container.query_items(
                query=query,
                parameters=parameters,
                partition_key=token_type,
            ):
                if first_doc is None:
                    first_doc = item
                cache_key = f"{token_type}:{item['id']}"
                self._l1.pop(cache_key, None)
                with contextlib.suppress(Exception):
                    await self._container.delete_item(
                        item=item["id"],
                        partition_key=token_type,
                    )

        return first_doc

    async def close(self) -> None:
        """Clear local state (shared Cosmos client stays alive)."""
        self._container = None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: TokenStore | None = None
_init_lock: asyncio.Lock | None = None


def _get_init_lock() -> asyncio.Lock:
    """Get or create the init lock (must be called within a running event loop)."""
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock


async def get_token_store() -> TokenStore:
    """Get or create the shared TokenStore singleton."""
    global _instance
    if _instance is not None:
        return _instance
    async with _get_init_lock():
        # Re-check after acquiring lock
        if _instance is None:
            store = TokenStore()
            await store.initialize()
            _instance = store
    return _instance
