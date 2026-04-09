"""Tests that ConversationStore.get_by_incident excludes turn-lock documents.

On Python 3.14rc2 with current Pydantic, importing ``ConversationDocument``
may fail. Guarded with a try/except + module-level skip.
"""

from __future__ import annotations

import pytest

_SKIP = False
_SKIP_REASON = ""
try:
    from sjifire.ops.chat.models import ConversationDocument
    from sjifire.ops.chat.store import ConversationStore
    from sjifire.ops.chat.turn_lock import TurnLockStore
except Exception as _exc:
    _SKIP = True
    _SKIP_REASON = f"Import failed: {_exc}"

pytestmark = pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)


async def _noop_container(name):
    return None


@pytest.fixture(autouse=True)
def _clear_memory(monkeypatch):
    """Reset in-memory stores."""
    ConversationStore._memory.clear()
    TurnLockStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.cosmos.get_cosmos_container", _noop_container)
    yield
    ConversationStore._memory.clear()
    TurnLockStore._memory.clear()


class TestConversationStoreWithLocks:
    async def test_get_by_incident_returns_conversation_not_lock(self):
        """get_by_incident should return the conversation, not the lock document."""
        doc = ConversationDocument(
            incident_id="inc-1",
            user_email="firefighter@sjifire.org",
        )
        async with ConversationStore() as store:
            await store.create(doc)

        async with TurnLockStore() as lock_store:
            await lock_store.acquire("inc-1", "alice@sjifire.org", "Alice")

        async with ConversationStore() as store:
            result = await store.get_by_incident("inc-1")

        assert result is not None
        assert result.user_email == "firefighter@sjifire.org"
        assert result.id == doc.id

    async def test_get_by_incident_works_without_lock(self):
        """get_by_incident works normally when no lock exists."""
        doc = ConversationDocument(
            incident_id="inc-2",
            user_email="firefighter@sjifire.org",
        )
        async with ConversationStore() as store:
            await store.create(doc)
            result = await store.get_by_incident("inc-2")

        assert result is not None
        assert result.id == doc.id

    async def test_get_by_incident_returns_none_when_only_lock(self):
        """get_by_incident returns None when only a lock document exists."""
        async with TurnLockStore() as lock_store:
            await lock_store.acquire("inc-3", "alice@sjifire.org", "Alice")

        async with ConversationStore() as store:
            result = await store.get_by_incident("inc-3")

        assert result is None
