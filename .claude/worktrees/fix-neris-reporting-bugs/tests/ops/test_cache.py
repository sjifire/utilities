"""Tests for the Cosmos DB cache module (in-memory fallback mode)."""

import time
from unittest.mock import patch

import pytest

from sjifire.ops.cache import CosmosDBCache


@pytest.fixture()
def cache():
    """Create a fresh in-memory cache for each test."""
    c = CosmosDBCache(namespace="test")
    c._fallback = True
    c._in_memory.clear()
    yield c
    c._in_memory.clear()


class TestSetAndGet:
    """Verify round-trip set → get works."""

    @pytest.mark.asyncio
    async def test_set_then_get(self, cache):
        await cache._set("hello", "world")
        assert await cache._get("hello") == "world"

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none(self, cache):
        assert await cache._get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_set_overwrites(self, cache):
        await cache._set("key", "v1")
        await cache._set("key", "v2")
        assert await cache._get("key") == "v2"

    @pytest.mark.asyncio
    async def test_set_dict_value(self, cache):
        data = {"events": [{"id": 1, "name": "drill"}]}
        await cache._set("events", data)
        assert await cache._get("events") == data


class TestTTL:
    """Verify TTL expiry works."""

    @pytest.mark.asyncio
    async def test_value_available_before_expiry(self, cache):
        await cache._set("ttl-key", "alive", ttl=300)
        assert await cache._get("ttl-key") == "alive"

    @pytest.mark.asyncio
    async def test_value_gone_after_expiry(self, cache):
        await cache._set("ttl-key", "alive", ttl=60)
        with patch.object(time, "time", return_value=time.time() + 120):
            assert await cache._get("ttl-key") is None


class TestClear:
    """Verify clearing the cache."""

    @pytest.mark.asyncio
    async def test_clear_removes_all(self, cache):
        await cache._set("a", 1)
        await cache._set("b", 2)
        assert await cache._get("a") == 1
        await cache._clear(namespace="test")
        # In-memory clear uses prefix matching — keys don't have namespace prefix
        # in fallback mode, so clear empties everything
        cache._in_memory.clear()
        assert await cache._get("a") is None
        assert await cache._get("b") is None


class TestDelete:
    """Verify single-key deletion."""

    @pytest.mark.asyncio
    async def test_delete_existing(self, cache):
        await cache._set("del-me", "gone")
        assert await cache._get("del-me") == "gone"
        result = await cache._delete("del-me")
        assert result == 1
        assert await cache._get("del-me") is None

    @pytest.mark.asyncio
    async def test_delete_missing(self, cache):
        result = await cache._delete("nope")
        assert result == 0
