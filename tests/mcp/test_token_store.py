"""Tests for TokenStore in-memory mode."""

import time

import pytest

from sjifire.mcp.auth import UserContext
from sjifire.mcp.token_store import (
    TokenStore,
    _deserialize_user,
    _serialize_user,
    get_token_store,
)

# Re-usable test user
_TEST_USER = UserContext(
    email="chief@sjifire.org",
    name="Fire Chief",
    user_id="abc-123",
    groups=frozenset(["officer-group", "admin-group"]),
)


@pytest.fixture(autouse=True)
def _clear_state(monkeypatch):
    """Reset in-memory store, L1 cache, and singleton."""
    import sjifire.mcp.token_store as mod

    TokenStore._memory.clear()
    mod._instance = None
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.mcp.token_store.load_dotenv", lambda: None)
    yield
    TokenStore._memory.clear()
    mod._instance = None


class TestSerialization:
    def test_round_trip_user(self):
        serialized = _serialize_user(_TEST_USER)
        assert isinstance(serialized["groups"], list)
        assert serialized["groups"] == ["admin-group", "officer-group"]

        restored = _deserialize_user(serialized)
        assert restored.email == _TEST_USER.email
        assert restored.name == _TEST_USER.name
        assert restored.user_id == _TEST_USER.user_id
        assert restored.groups == _TEST_USER.groups
        assert isinstance(restored.groups, frozenset)

    def test_empty_groups(self):
        user = UserContext(email="a@b.com", name="A", user_id="1")
        serialized = _serialize_user(user)
        assert serialized["groups"] == []
        restored = _deserialize_user(serialized)
        assert restored.groups == frozenset()

    def test_missing_groups_key(self):
        data = {"email": "a@b.com", "name": "A", "user_id": "1"}
        restored = _deserialize_user(data)
        assert restored.groups == frozenset()


class TestCRUD:
    async def test_set_and_get(self):
        store = TokenStore()
        await store.initialize()

        await store.set(
            "access_token",
            "tok-1",
            {
                "expires_at": time.time() + 3600,
                "client_id": "client-1",
                "scopes": ["mcp.access"],
                "user": _serialize_user(_TEST_USER),
            },
            3600,
        )

        doc = await store.get("access_token", "tok-1")
        assert doc is not None
        assert doc["client_id"] == "client-1"
        assert doc["scopes"] == ["mcp.access"]

        user = _deserialize_user(doc["user"])
        assert user.email == "chief@sjifire.org"

    async def test_get_nonexistent_returns_none(self):
        store = TokenStore()
        await store.initialize()
        assert await store.get("access_token", "nonexistent") is None

    async def test_get_wrong_type_returns_none(self):
        store = TokenStore()
        await store.initialize()
        await store.set(
            "access_token",
            "tok-1",
            {
                "expires_at": time.time() + 3600,
                "client_id": "c1",
            },
            3600,
        )
        assert await store.get("refresh_token", "tok-1") is None

    async def test_delete(self):
        store = TokenStore()
        await store.initialize()
        await store.set(
            "refresh_token",
            "rt-1",
            {
                "expires_at": time.time() + 86400,
                "client_id": "c1",
            },
            86400,
        )

        await store.delete("refresh_token", "rt-1")
        assert await store.get("refresh_token", "rt-1") is None

    async def test_delete_nonexistent_no_error(self):
        store = TokenStore()
        await store.initialize()
        await store.delete("access_token", "nonexistent")

    async def test_all_token_types(self):
        store = TokenStore()
        await store.initialize()

        for token_type in ("access_token", "refresh_token", "auth_code"):
            await store.set(
                token_type,
                f"tok-{token_type}",
                {
                    "expires_at": time.time() + 300,
                    "client_id": "c1",
                },
                300,
            )
            doc = await store.get(token_type, f"tok-{token_type}")
            assert doc is not None
            assert doc["token_type"] == token_type


class TestExpiry:
    async def test_expired_doc_returns_none(self):
        store = TokenStore()
        await store.initialize()
        await store.set(
            "access_token",
            "tok-expired",
            {
                "expires_at": time.time() - 10,
                "client_id": "c1",
            },
            3600,
        )

        # Clear L1 so it falls through to backing store
        store._l1.clear()
        assert await store.get("access_token", "tok-expired") is None

    async def test_expired_in_l1_returns_none(self):
        store = TokenStore()
        await store.initialize()

        # Manually populate L1 with expired doc
        store._l1["access_token:tok-exp"] = {
            "id": "tok-exp",
            "token_type": "access_token",
            "expires_at": time.time() - 5,
            "client_id": "c1",
        }
        assert await store.get("access_token", "tok-exp") is None


class TestL1Cache:
    async def test_l1_hit_avoids_backing_store(self):
        store = TokenStore()
        await store.initialize()

        await store.set(
            "access_token",
            "tok-cached",
            {
                "expires_at": time.time() + 3600,
                "client_id": "c1",
            },
            3600,
        )

        # Verify it's in L1
        assert "access_token:tok-cached" in store._l1

        # Remove from backing store — L1 should still serve it
        TokenStore._memory.pop("access_token:tok-cached", None)

        doc = await store.get("access_token", "tok-cached")
        assert doc is not None
        assert doc["client_id"] == "c1"

    async def test_l1_miss_populates_from_backing(self):
        store = TokenStore()
        await store.initialize()

        await store.set(
            "access_token",
            "tok-l2",
            {
                "expires_at": time.time() + 3600,
                "client_id": "c1",
            },
            3600,
        )

        # Clear L1 to force backing store read
        store._l1.clear()
        doc = await store.get("access_token", "tok-l2")
        assert doc is not None
        # Should now be in L1
        assert "access_token:tok-l2" in store._l1

    async def test_delete_clears_l1(self):
        store = TokenStore()
        await store.initialize()

        await store.set(
            "access_token",
            "tok-del",
            {
                "expires_at": time.time() + 3600,
                "client_id": "c1",
            },
            3600,
        )
        assert "access_token:tok-del" in store._l1

        await store.delete("access_token", "tok-del")
        assert "access_token:tok-del" not in store._l1


class TestDeleteByClient:
    async def test_deletes_matching_tokens(self):
        store = TokenStore()
        await store.initialize()

        await store.set(
            "access_token",
            "at-1",
            {
                "expires_at": time.time() + 3600,
                "client_id": "client-A",
                "user": _serialize_user(_TEST_USER),
            },
            3600,
        )
        await store.set(
            "access_token",
            "at-2",
            {
                "expires_at": time.time() + 3600,
                "client_id": "client-A",
            },
            3600,
        )
        await store.set(
            "access_token",
            "at-other",
            {
                "expires_at": time.time() + 3600,
                "client_id": "client-B",
            },
            3600,
        )

        first = await store.delete_by_client("access_token", "client-A")
        assert first is not None
        assert first["client_id"] == "client-A"

        # Both client-A tokens should be gone
        assert await store.get("access_token", "at-1") is None
        assert await store.get("access_token", "at-2") is None

        # client-B token should remain
        assert await store.get("access_token", "at-other") is not None

    async def test_returns_none_when_no_match(self):
        store = TokenStore()
        await store.initialize()
        result = await store.delete_by_client("access_token", "nonexistent-client")
        assert result is None

    async def test_returns_user_from_first_match(self):
        store = TokenStore()
        await store.initialize()

        await store.set(
            "access_token",
            "at-user",
            {
                "expires_at": time.time() + 3600,
                "client_id": "client-X",
                "user": _serialize_user(_TEST_USER),
            },
            3600,
        )

        first = await store.delete_by_client("access_token", "client-X")
        assert first is not None
        user = _deserialize_user(first["user"])
        assert user.email == "chief@sjifire.org"
        assert user.groups == frozenset(["officer-group", "admin-group"])


class TestSingleton:
    async def test_get_token_store_returns_same_instance(self):
        store1 = await get_token_store()
        store2 = await get_token_store()
        assert store1 is store2

    async def test_singleton_works_in_memory_mode(self):
        store = await get_token_store()
        assert store._in_memory is True
        await store.set(
            "access_token",
            "singleton-tok",
            {
                "expires_at": time.time() + 3600,
                "client_id": "c1",
            },
            3600,
        )
        doc = await store.get("access_token", "singleton-tok")
        assert doc is not None


class TestDevModeFullFlow:
    """End-to-end flow in dev mode (no Cosmos, in-memory only)."""

    async def test_auth_code_to_access_token_flow(self):
        store = TokenStore()
        await store.initialize()
        assert store._in_memory is True

        user_data = _serialize_user(_TEST_USER)

        # 1. Store auth code (from handle_callback)
        await store.set(
            "auth_code",
            "code-1",
            {
                "expires_at": time.time() + 300,
                "client_id": "client-1",
                "scopes": ["mcp.access"],
                "user": user_data,
            },
            300,
        )

        # 2. Load auth code (from load_authorization_code)
        code_doc = await store.get("auth_code", "code-1")
        assert code_doc is not None
        assert code_doc["client_id"] == "client-1"

        # 3. Exchange: delete auth code, create access + refresh tokens
        await store.delete("auth_code", "code-1")

        await store.set(
            "access_token",
            "at-new",
            {
                "expires_at": time.time() + 3600,
                "client_id": "client-1",
                "scopes": ["mcp.access"],
                "user": user_data,
            },
            3600,
        )
        await store.set(
            "refresh_token",
            "rt-new",
            {
                "expires_at": time.time() + 86400,
                "client_id": "client-1",
                "scopes": ["mcp.access"],
                "user": user_data,
            },
            86400,
        )

        # Auth code should be gone
        assert await store.get("auth_code", "code-1") is None

        # Tokens should be available
        at_doc = await store.get("access_token", "at-new")
        assert at_doc is not None
        user = _deserialize_user(at_doc["user"])
        assert user.email == "chief@sjifire.org"

        rt_doc = await store.get("refresh_token", "rt-new")
        assert rt_doc is not None

    async def test_token_rotation_flow(self):
        store = TokenStore()
        await store.initialize()

        user_data = _serialize_user(_TEST_USER)

        # Setup: existing access + refresh tokens
        await store.set(
            "access_token",
            "old-at",
            {
                "expires_at": time.time() + 3600,
                "client_id": "client-1",
                "user": user_data,
            },
            3600,
        )
        await store.set(
            "refresh_token",
            "old-rt",
            {
                "expires_at": time.time() + 86400,
                "client_id": "client-1",
                "user": user_data,
            },
            86400,
        )

        # Rotation: delete old, create new
        rt_doc = await store.get("refresh_token", "old-rt")
        assert rt_doc is not None
        old_user = rt_doc.get("user")

        await store.delete_by_client("access_token", "client-1")
        await store.delete("refresh_token", "old-rt")

        await store.set(
            "access_token",
            "new-at",
            {
                "expires_at": time.time() + 3600,
                "client_id": "client-1",
                "user": old_user,
            },
            3600,
        )
        await store.set(
            "refresh_token",
            "new-rt",
            {
                "expires_at": time.time() + 86400,
                "client_id": "client-1",
                "user": old_user,
            },
            86400,
        )

        # Old tokens gone
        assert await store.get("access_token", "old-at") is None
        assert await store.get("refresh_token", "old-rt") is None

        # New tokens present with user preserved
        new_at = await store.get("access_token", "new-at")
        assert new_at is not None
        assert _deserialize_user(new_at["user"]).email == "chief@sjifire.org"
