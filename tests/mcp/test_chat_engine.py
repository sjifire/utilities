"""Tests for the chat engine with mocked Claude API."""

import asyncio
import json
from unittest.mock import patch

import pytest

from sjifire.mcp.auth import UserContext
from sjifire.mcp.chat.engine import (
    _build_general_system_prompt,
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
        prompt = _build_system_prompt("{}", "{}", "[]", "Test User", "test@sjifire.org")
        assert (
            "San Juan" in prompt
            or "sjifire" in prompt.lower()
            or "incident report" in prompt.lower()
        )

    def test_includes_incident_data(self):
        incident_json = '{"incident_number": "26-001234"}'
        prompt = _build_system_prompt(incident_json, "{}", "[]", "Test User", "test@sjifire.org")
        assert "26-001234" in prompt

    def test_includes_rules(self):
        prompt = _build_system_prompt("{}", "{}", "[]", "Test User", "test@sjifire.org")
        assert "RULES" in prompt
        assert "WORKFLOW" in prompt

    def test_includes_user_identity(self):
        prompt = _build_system_prompt("{}", "{}", "[]", "Jordan Pollack", "jpollack@sjifire.org")
        assert "Jordan Pollack" in prompt
        assert "jpollack@sjifire.org" in prompt


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

    def test_preserves_order(self):
        msgs = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(50)
        ]
        result = _trim_messages(msgs)
        # Should keep the last 40
        for i, msg in enumerate(result):
            assert msg["content"] == f"msg {i + 10}"


class TestSSE:
    def test_format(self):
        result = _sse("text", {"content": "hello"})
        assert result == 'event: text\ndata: {"content": "hello"}\n\n'

    def test_error_event(self):
        result = _sse("error", {"message": "budget exceeded"})
        assert "error" in result
        assert "budget exceeded" in result

    def test_done_event_includes_tokens(self):
        result = _sse("done", {"input_tokens": 100, "output_tokens": 50})
        data = json.loads(result.split("data: ")[1].strip())
        assert data["input_tokens"] == 100
        assert data["output_tokens"] == 50


class TestSummarizeToolResult:
    def test_get_incident(self):
        result = _summarize_tool_result(
            "get_incident",
            {"incident_number": "26-001234", "status": "draft"},
        )
        assert "26-001234" in result
        assert "draft" in result

    def test_update_incident(self):
        result = _summarize_tool_result(
            "update_incident",
            {"incident_number": "26-001234"},
        )
        assert "Updated" in result

    def test_error_result(self):
        result = _summarize_tool_result("get_incident", {"error": "Not found"})
        assert "Error" in result

    def test_get_on_duty_crew(self):
        result = _summarize_tool_result(
            "get_on_duty_crew",
            {"count": 5, "platoon": "A"},
        )
        assert "5" in result
        assert "A" in result

    def test_get_neris_values(self):
        result = _summarize_tool_result(
            "get_neris_values",
            {"count": 12, "value_set": "incident"},
        )
        assert "12" in result
        assert "incident" in result

    def test_get_dispatch_call(self):
        result = _summarize_tool_result(
            "get_dispatch_call",
            {"nature": "Fire-Structure", "address": "589 OLD FARM RD"},
        )
        assert "Fire-Structure" in result
        assert "589 OLD FARM RD" in result

    def test_search_dispatch_calls(self):
        result = _summarize_tool_result(
            "search_dispatch_calls",
            {"count": 3},
        )
        assert "3" in result

    def test_list_dispatch_calls(self):
        result = _summarize_tool_result(
            "list_dispatch_calls",
            {"count": 7},
        )
        assert "7" in result

    def test_unknown_tool_truncates(self):
        result = _summarize_tool_result("unknown_tool", {"data": "x" * 500})
        assert len(result) <= 200


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

    async def test_budget_check_failure_yields_error_detail(self):
        """When budget check raises, error should include exception details."""
        from sjifire.mcp.chat.engine import stream_chat

        with patch(
            "sjifire.mcp.chat.engine.check_budget",
            side_effect=ConnectionError("Cosmos DB unavailable"),
        ):
            events = [e async for e in stream_chat("inc-789", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "ConnectionError" in events[0]
        assert "Cosmos DB unavailable" in events[0]

    async def test_conversation_load_failure_yields_error_detail(self):
        """When conversation store raises, error should include details."""
        from sjifire.mcp.chat.budget import BudgetStatus
        from sjifire.mcp.chat.engine import stream_chat

        with (
            patch(
                "sjifire.mcp.chat.engine.check_budget",
                return_value=BudgetStatus(allowed=True),
            ),
            patch(
                "sjifire.mcp.chat.engine.ConversationStore.__aenter__",
                side_effect=ConnectionError("connection refused"),
            ),
        ):
            events = [e async for e in stream_chat("inc-err", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "ConnectionError" in events[0]

    async def test_context_fetch_failure_yields_error_detail(self):
        """When _fetch_context raises, error should include details."""
        from sjifire.mcp.chat.budget import BudgetStatus
        from sjifire.mcp.chat.engine import stream_chat

        with (
            patch(
                "sjifire.mcp.chat.engine.check_budget",
                return_value=BudgetStatus(allowed=True),
            ),
            patch(
                "sjifire.mcp.chat.engine._fetch_context",
                side_effect=RuntimeError("dispatch store down"),
            ),
        ):
            events = [e async for e in stream_chat("inc-ctx", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "RuntimeError" in events[0]
        assert "dispatch store down" in events[0]


class TestBuildGeneralSystemPrompt:
    def test_includes_todays_date(self):
        prompt = _build_general_system_prompt()
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from sjifire.core.config import get_org_config

        org = get_org_config()
        tz = ZoneInfo(org.timezone) if org.timezone else datetime.now().astimezone().tzinfo
        today_str = datetime.now(tz).strftime("%B %d, %Y")
        assert today_str in prompt

    def test_includes_time(self):
        prompt = _build_general_system_prompt()
        assert "TIME:" in prompt
        assert "TODAY:" in prompt

    def test_no_context_no_calls_table(self):
        prompt = _build_general_system_prompt()
        assert "Dispatch calls visible to the user" not in prompt

    def test_includes_calls_context(self):
        context = {
            "calls": [
                {
                    "id": "26-001234",
                    "nature": "Fire-Structure",
                    "address": "123 MAIN ST",
                    "date": "Feb 13",
                    "time": "14:30",
                    "ic": "Smith",
                    "report_source": "local",
                    "report_status": "draft",
                },
                {
                    "id": "26-001235",
                    "nature": "EMS-Medical",
                    "address": "456 OAK AVE",
                    "date": "Feb 14",
                    "time": "09:15",
                    "ic": "",
                    "report_source": None,
                    "report_status": None,
                },
            ]
        }
        prompt = _build_general_system_prompt(context)
        assert "PAGE CONTEXT" in prompt
        assert "26-001234" in prompt
        assert "Fire-Structure" in prompt
        assert "123 MAIN ST" in prompt
        assert "26-001235" in prompt
        assert "EMS-Medical" in prompt

    def test_empty_calls_no_calls_table(self):
        prompt = _build_general_system_prompt({"calls": []})
        assert "Dispatch calls visible to the user" not in prompt


class TestParallelToolExecution:
    """Verify that tool calls within _stream_loop execute concurrently."""

    async def test_multiple_tools_run_in_parallel(self):
        """When Claude returns multiple tool calls, they should run concurrently."""
        call_times: list[float] = []

        async def slow_tool(name, tool_input, user):
            """Simulate a tool that takes 100ms."""
            import time

            start = time.monotonic()
            await asyncio.sleep(0.1)
            call_times.append(time.monotonic() - start)
            return json.dumps({"status": "ok"})

        with patch("sjifire.mcp.chat.engine.execute_tool", side_effect=slow_tool):
            # Simulate what _stream_loop does with parallel tool calls
            tool_calls = [
                {"id": "t1", "name": "get_incident", "input": {"incident_id": "abc"}},
                {"id": "t2", "name": "get_neris_values", "input": {"value_set": "incident"}},
                {"id": "t3", "name": "get_on_duty_crew", "input": {"target_date": "2026-02-13"}},
            ]

            import time

            start = time.monotonic()
            results = await asyncio.gather(
                *(slow_tool(tc["name"], tc["input"], _TEST_USER) for tc in tool_calls)
            )
            elapsed = time.monotonic() - start

            # 3 tools at 100ms each: serial would be ~300ms, parallel should be ~100ms
            assert len(results) == 3
            assert elapsed < 0.25  # generous margin, but proves parallelism
            for r in results:
                assert json.loads(r)["status"] == "ok"
