"""Tests for ConversationStore and BudgetStore in-memory mode."""

import pytest

from sjifire.ops.chat.models import ConversationDocument, ConversationMessage
from sjifire.ops.chat.store import BudgetStore, ConversationStore


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset in-memory stores and ensure Cosmos env vars are unset."""
    ConversationStore._memory.clear()
    BudgetStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.chat.store.load_dotenv", lambda: None)
    yield
    ConversationStore._memory.clear()
    BudgetStore._memory.clear()


def _make_conversation(**overrides) -> ConversationDocument:
    defaults = {
        "incident_id": "test-incident-123",
        "user_email": "firefighter@sjifire.org",
    }
    defaults.update(overrides)
    return ConversationDocument(**defaults)


class TestConversationStore:
    async def test_create_and_get(self):
        doc = _make_conversation()
        async with ConversationStore() as store:
            created = await store.create(doc)
            fetched = await store.get(created.id, "test-incident-123")
        assert fetched is not None
        assert fetched.user_email == "firefighter@sjifire.org"

    async def test_get_nonexistent_returns_none(self):
        async with ConversationStore() as store:
            result = await store.get("nonexistent", "test-incident")
        assert result is None

    async def test_get_by_incident(self):
        doc = _make_conversation(incident_id="inc-456")
        async with ConversationStore() as store:
            await store.create(doc)
            fetched = await store.get_by_incident("inc-456")
        assert fetched is not None
        assert fetched.id == doc.id

    async def test_get_by_incident_not_found(self):
        async with ConversationStore() as store:
            result = await store.get_by_incident("nonexistent")
        assert result is None

    async def test_update(self):
        doc = _make_conversation()
        async with ConversationStore() as store:
            await store.create(doc)
            doc.turn_count = 5
            doc.messages.append(ConversationMessage(role="user", content="Hello"))
            updated = await store.update(doc)
        assert updated.turn_count == 5
        assert len(updated.messages) == 1

    async def test_wrong_incident_id_returns_none(self):
        doc = _make_conversation(incident_id="inc-A")
        async with ConversationStore() as store:
            await store.create(doc)
            result = await store.get(doc.id, "inc-B")
        assert result is None


class TestBudgetStore:
    async def test_get_or_create_new(self):
        async with BudgetStore() as store:
            budget = await store.get_or_create("user@sjifire.org", "2026-02")
        assert budget.user_email == "user@sjifire.org"
        assert budget.month == "2026-02"
        assert budget.input_tokens == 0

    async def test_get_or_create_existing(self):
        async with BudgetStore() as store:
            budget1 = await store.get_or_create("user@sjifire.org", "2026-02")
            budget1.input_tokens = 1000
            await store.update(budget1)
            budget2 = await store.get_or_create("user@sjifire.org", "2026-02")
        assert budget2.input_tokens == 1000

    async def test_update(self):
        async with BudgetStore() as store:
            budget = await store.get_or_create("user@sjifire.org", "2026-02")
            budget.input_tokens = 5000
            budget.output_tokens = 1000
            budget.estimated_cost_usd = 0.03
            updated = await store.update(budget)
        assert updated.input_tokens == 5000
        assert updated.output_tokens == 1000

    async def test_separate_months(self):
        async with BudgetStore() as store:
            feb = await store.get_or_create("user@sjifire.org", "2026-02")
            mar = await store.get_or_create("user@sjifire.org", "2026-03")
        assert feb.id != mar.id
        assert feb.month == "2026-02"
        assert mar.month == "2026-03"
