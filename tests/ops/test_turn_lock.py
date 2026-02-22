"""Tests for the distributed turn lock (in-memory mode)."""

import pytest

from sjifire.ops.chat.turn_lock import TurnLockStore


async def _noop_container(name):
    return None


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset in-memory store and ensure Cosmos env vars are unset."""
    TurnLockStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.chat.turn_lock.get_cosmos_container", _noop_container)
    yield
    TurnLockStore._memory.clear()


class TestTurnLockAcquire:
    async def test_acquire_succeeds_when_unlocked(self):
        async with TurnLockStore() as store:
            lock = await store.acquire("inc-1", "alice@sjifire.org", "Alice")
        assert lock is not None
        assert lock.holder_email == "alice@sjifire.org"
        assert lock.holder_name == "Alice"
        assert lock.incident_id == "inc-1"

    async def test_acquire_fails_when_held_by_another(self):
        async with TurnLockStore() as store:
            await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            lock = await store.acquire("inc-1", "bob@sjifire.org", "Bob")
        assert lock is None

    async def test_acquire_rejected_for_same_user(self):
        """Same user cannot re-acquire — prevents concurrent engine tasks."""
        async with TurnLockStore() as store:
            lock1 = await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            lock2 = await store.acquire("inc-1", "alice@sjifire.org", "Alice")
        assert lock1 is not None
        assert lock2 is None

    async def test_different_incidents_independent(self):
        async with TurnLockStore() as store:
            lock_a = await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            lock_b = await store.acquire("inc-2", "bob@sjifire.org", "Bob")
        assert lock_a is not None
        assert lock_b is not None


class TestTurnLockRelease:
    async def test_release_by_holder(self):
        async with TurnLockStore() as store:
            await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            released = await store.release("inc-1", "alice@sjifire.org")
        assert released is True

    async def test_release_by_non_holder_fails(self):
        async with TurnLockStore() as store:
            await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            released = await store.release("inc-1", "bob@sjifire.org")
        assert released is False

    async def test_release_nonexistent_fails(self):
        async with TurnLockStore() as store:
            released = await store.release("inc-1", "alice@sjifire.org")
        assert released is False

    async def test_acquire_after_release(self):
        async with TurnLockStore() as store:
            await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            await store.release("inc-1", "alice@sjifire.org")
            lock = await store.acquire("inc-1", "bob@sjifire.org", "Bob")
        assert lock is not None
        assert lock.holder_email == "bob@sjifire.org"


class TestTurnLockGet:
    async def test_get_returns_lock(self):
        async with TurnLockStore() as store:
            await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            lock = await store.get("inc-1")
        assert lock is not None
        assert lock.holder_email == "alice@sjifire.org"
        assert lock.holder_name == "Alice"

    async def test_get_returns_none_when_unlocked(self):
        async with TurnLockStore() as store:
            lock = await store.get("inc-1")
        assert lock is None

    async def test_get_after_release_returns_none(self):
        async with TurnLockStore() as store:
            await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            await store.release("inc-1", "alice@sjifire.org")
            lock = await store.get("inc-1")
        assert lock is None


class TestTurnLockIsHeldBy:
    async def test_is_held_by_correct_user(self):
        async with TurnLockStore() as store:
            await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            lock = await store.get("inc-1")
        assert lock.is_held_by("alice@sjifire.org")

    async def test_is_held_by_wrong_user(self):
        async with TurnLockStore() as store:
            await store.acquire("inc-1", "alice@sjifire.org", "Alice")
            lock = await store.get("inc-1")
        assert not lock.is_held_by("bob@sjifire.org")
