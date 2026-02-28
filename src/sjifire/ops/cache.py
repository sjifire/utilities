"""Cosmos DB backend for aiocache.

Provides a shared, distributed cache backed by Azure Cosmos DB so
cached data survives container restarts and is visible across replicas.

Usage::

    from sjifire.ops.cache import cosmos_cache

    # Simple get/set
    await cosmos_cache.set("training:events", data, ttl=1800)
    data = await cosmos_cache.get("training:events")

    # Decorator
    from aiocache import cached
    from sjifire.ops.cache import cosmos_cache

    @cached(cache=cosmos_cache, ttl=1800, key="training:events")
    async def fetch_events():
        ...

When ``COSMOS_ENDPOINT`` is not configured, falls back to aiocache's
built-in ``SimpleMemoryCache`` so local development works without Azure.

Cosmos container: ``cache`` with partition key ``/ns`` (namespace).
Documents have a ``ttl`` field — Cosmos DB automatically expires them
via its built-in TTL support (no manual cleanup needed).
"""

import logging
import time

from aiocache.base import BaseCache
from aiocache.serializers import JsonSerializer

logger = logging.getLogger(__name__)

CONTAINER_NAME = "cache"


class CosmosDBBackend(BaseCache):
    """aiocache backend that stores entries in Azure Cosmos DB.

    Each cache entry is a Cosmos document::

        {
            "id": "<namespace>:<key>",
            "ns": "<namespace>",
            "val": <serialized value>,
            "ttl": <seconds>,          # Cosmos built-in TTL
            "exp": <unix timestamp>,    # for client-side TTL check
        }

    Cosmos DB's built-in TTL handles expiration automatically — documents
    are garbage-collected after their ``ttl`` seconds elapse. The ``exp``
    field is a belt-and-suspenders client-side check for edge cases where
    a read races with TTL expiration.
    """

    def __init__(self, namespace: str = "default", **kwargs):  # noqa: D107
        super().__init__(namespace=namespace, **kwargs)
        self._container = None
        self._in_memory: dict[str, dict] = {}
        self._fallback = False

    async def _get_container(self):
        """Lazy-init Cosmos container client."""
        if self._fallback:
            return None
        if self._container is not None:
            return self._container

        from sjifire.core.config import get_cosmos_container

        container = await get_cosmos_container(CONTAINER_NAME)
        if container is None:
            self._fallback = True
            logger.info("Cosmos DB not configured — cache using in-memory fallback")
            return None
        self._container = container
        return container

    def _ns(self) -> str:
        return self.namespace or "default"

    # ── Core operations ──────────────────────────────────────────────

    async def _get(self, key, encoding="utf-8", _conn=None):
        container = await self._get_container()
        doc_id = key

        if container is None:
            entry = self._in_memory.get(doc_id)
            if entry and (entry.get("exp", 0) == 0 or entry["exp"] > time.time()):
                return entry.get("val")
            self._in_memory.pop(doc_id, None)
            return None

        try:
            result = await container.read_item(item=doc_id, partition_key=self._ns())
            if result.get("exp", 0) and result["exp"] <= time.time():
                return None
            return result.get("val")
        except Exception:
            return None

    async def _gets(self, key, encoding="utf-8", _conn=None):
        return await self._get(key, encoding=encoding, _conn=_conn)

    async def _multi_get(self, keys, encoding="utf-8", _conn=None):
        return [await self._get(k, encoding=encoding, _conn=_conn) for k in keys]

    async def _set(self, key, value, ttl=None, _cas_token=None, _conn=None):
        container = await self._get_container()
        doc_id = key
        now = time.time()

        doc = {
            "id": doc_id,
            "ns": self._ns(),
            "val": value,
        }
        if ttl:
            doc["ttl"] = int(ttl)
            doc["exp"] = now + ttl
        else:
            doc["exp"] = 0

        if container is None:
            self._in_memory[doc_id] = doc
            return True

        try:
            await container.upsert_item(body=doc)
            return True
        except Exception:
            logger.warning("Cache set failed for %s", doc_id, exc_info=True)
            return False

    async def _multi_set(self, pairs, ttl=None, _conn=None):
        for key, value in pairs:
            await self._set(key, value, ttl=ttl)
        return True

    async def _add(self, key, value, ttl=None, _conn=None):
        existing = await self._get(key)
        if existing is not None:
            raise ValueError(f"Key {key} already exists")
        return await self._set(key, value, ttl=ttl)

    async def _exists(self, key, _conn=None):
        return await self._get(key) is not None

    async def _increment(self, key, delta, _conn=None):
        val = await self._get(key)
        if val is None:
            val = 0
        new_val = int(val) + delta
        await self._set(key, new_val)
        return new_val

    async def _expire(self, key, ttl, _conn=None):
        val = await self._get(key)
        if val is not None:
            await self._set(key, val, ttl=ttl)
            return True
        return False

    async def _delete(self, key, _conn=None):
        container = await self._get_container()
        doc_id = key

        if container is None:
            return 1 if self._in_memory.pop(doc_id, None) is not None else 0

        try:
            await container.delete_item(item=doc_id, partition_key=self._ns())
            return 1
        except Exception:
            return 0

    async def _clear(self, namespace=None, _conn=None):
        ns = namespace or self._ns()
        container = await self._get_container()

        if container is None:
            keys = [k for k in self._in_memory if k.startswith(f"{ns}:")]
            for k in keys:
                del self._in_memory[k]
            return True

        try:
            query = "SELECT c.id FROM c WHERE c.ns = @ns"
            params = [{"name": "@ns", "value": ns}]
            async for item in container.query_items(query=query, parameters=params):
                await container.delete_item(item=item["id"], partition_key=ns)
            return True
        except Exception:
            logger.warning("Cache clear failed for namespace %s", ns, exc_info=True)
            return False

    async def _raw(self, command, *args, encoding="utf-8", _conn=None, **kwargs):
        raise NotImplementedError("raw commands not supported for Cosmos backend")

    async def _redlock_release(self, key, value):
        return 0


class CosmosDBCache(CosmosDBBackend):
    """Cosmos DB cache with JSON serialization.

    Config options:

    :param namespace: Partition key prefix for cache entries. Default "default".
    :param serializer: Defaults to JsonSerializer.
    :param timeout: Operation timeout in seconds. Default 5.
    """

    NAME = "cosmosdb"

    def __init__(self, serializer=None, **kwargs):  # noqa: D107
        super().__init__(serializer=serializer or JsonSerializer(), **kwargs)

    @classmethod
    def parse_uri_path(cls, path):  # noqa: D102
        return {}


# ── Module-level singleton ───────────────────────────────────────────
# Import and use this directly:
#   from sjifire.ops.cache import cosmos_cache
cosmos_cache = CosmosDBCache(namespace="default")
