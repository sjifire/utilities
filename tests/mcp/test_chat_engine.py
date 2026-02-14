"""Tests for the chat engine with mocked Claude API."""

from unittest.mock import patch

import pytest

from sjifire.mcp.auth import UserContext
from sjifire.mcp.chat.engine import (
    _build_system_prompt,
    _sse,
    _summarize_tool_result,
    _trim_messages,
)
from sjifire.mcp.chat.models import MAX_TURNS, ConversationDocument
from sjifire.mcp.chat.store import BudgetStore, ConversationStore
from sjifire.mcp.incidents.store import IncidentStore


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset all in-memory stores."""
    ConversationStore._memory.clear()
    BudgetStore._memory.clear()
    IncidentStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.mcp.chat.store.load_dotenv", lambda: None)
    monkeypatch.setattr("sjifire.mcp.incidents.store.load_dotenv", lambda: None)
    yield
    ConversationStore._memory.clear()
    BudgetStore._memory.clear()
    IncidentStore._memory.clear()


_TEST_USER = UserContext(
    email="firefighter@sjifire.org",
    name="Test User",
    user_id="test-uid",
    groups=frozenset(),
)


class TestBuildSystemPrompt:
    def test_includes_org_name(self):
        prompt = _build_system_prompt("{}", "{}", "[]")
        assert (
            "San Juan" in prompt
            or "sjifire" in prompt.lower()
            or "incident report" in prompt.lower()
        )

    def test_includes_incident_data(self):
        incident_json = '{"incident_number": "26-001234"}'
        prompt = _build_system_prompt(incident_json, "{}", "[]")
        assert "26-001234" in prompt

    def test_includes_rules(self):
        prompt = _build_system_prompt("{}", "{}", "[]")
        assert "RULES" in prompt
        assert "WORKFLOW" in prompt


class TestTrimMessages:
    def test_no_trim_needed(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        result = _trim_messages(msgs)
        assert len(result) == 2

    def test_trims_old_messages(self):
        msgs = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(100)
        ]
        result = _trim_messages(msgs)
        assert len(result) == 40  # MAX_CONTEXT_MESSAGES * 2
        assert result[0]["content"] == "msg 60"


class TestSSE:
    def test_format(self):
        result = _sse("text", {"content": "hello"})
        assert result == 'event: text\ndata: {"content": "hello"}\n\n'

    def test_error_event(self):
        result = _sse("error", {"message": "budget exceeded"})
        assert "error" in result
        assert "budget exceeded" in result


class TestSummarizeToolResult:
    def test_get_incident(self):
        result = _summarize_tool_result(
            "get_incident",
            {
                "incident_number": "26-001234",
                "status": "draft",
            },
        )
        assert "26-001234" in result
        assert "draft" in result

    def test_update_incident(self):
        result = _summarize_tool_result(
            "update_incident",
            {
                "incident_number": "26-001234",
            },
        )
        assert "Updated" in result

    def test_error_result(self):
        result = _summarize_tool_result("get_incident", {"error": "Not found"})
        assert "Error" in result

    def test_get_on_duty_crew(self):
        result = _summarize_tool_result(
            "get_on_duty_crew",
            {
                "count": 5,
                "platoon": "A",
            },
        )
        assert "5" in result
        assert "A" in result


class TestStreamChat:
    async def test_budget_exceeded_yields_error(self):
        """When budget is exceeded, stream should yield an error event."""
        from sjifire.mcp.chat.budget import BudgetStatus
        from sjifire.mcp.chat.engine import stream_chat

        with patch(
            "sjifire.mcp.chat.engine.check_budget",
            return_value=BudgetStatus(allowed=False, reason="Monthly limit reached"),
        ):
            events = [e async for e in stream_chat("inc-123", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "error" in events[0]
        assert "Monthly limit reached" in events[0]

    async def test_turn_limit_exceeded_yields_error(self):
        """When turn count is at max, stream should yield an error event."""
        from sjifire.mcp.chat.budget import BudgetStatus
        from sjifire.mcp.chat.engine import stream_chat

        # Create a conversation at the turn limit
        doc = ConversationDocument(
            incident_id="inc-456",
            user_email="firefighter@sjifire.org",
            turn_count=MAX_TURNS,
        )
        async with ConversationStore() as store:
            await store.create(doc)

        with patch(
            "sjifire.mcp.chat.engine.check_budget",
            return_value=BudgetStatus(allowed=True),
        ):
            events = [e async for e in stream_chat("inc-456", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "error" in events[0]
        assert "limit" in events[0].lower()
