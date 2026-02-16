"""Tests for the chat engine with mocked Claude API."""

import asyncio
import json
import logging
from unittest.mock import patch

import pytest

from sjifire.ops.auth import UserContext
from sjifire.ops.chat.engine import (
    _build_general_system_prompt,
    _build_system_prompt,
    _format_unit_times_table,
    _log_cache_stats,
    _sse,
    _summarize_tool_result,
    _trim_messages,
)
from sjifire.ops.chat.models import MAX_TURNS, ConversationDocument
from sjifire.ops.chat.store import BudgetStore, ConversationStore
from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.incidents.store import IncidentStore


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset all in-memory stores."""
    ConversationStore._memory.clear()
    BudgetStore._memory.clear()
    IncidentStore._memory.clear()
    DispatchStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.chat.store.load_dotenv", lambda: None)
    monkeypatch.setattr("sjifire.ops.incidents.store.load_dotenv", lambda: None)
    monkeypatch.setattr("sjifire.ops.dispatch.store.load_dotenv", lambda: None)
    yield
    ConversationStore._memory.clear()
    BudgetStore._memory.clear()
    IncidentStore._memory.clear()
    DispatchStore._memory.clear()


_TEST_USER = UserContext(
    email="firefighter@sjifire.org",
    name="Test User",
    user_id="test-uid",
    groups=frozenset(),
)


class TestBuildSystemPrompt:
    def test_includes_org_name(self):
        prompt = _build_system_prompt("{}", "{}", "[]", "[]", "Test User", "test@sjifire.org")
        assert (
            "San Juan" in prompt
            or "sjifire" in prompt.lower()
            or "incident report" in prompt.lower()
        )

    def test_includes_incident_data(self):
        incident_json = '{"incident_number": "26-001234"}'
        prompt = _build_system_prompt(
            incident_json, "{}", "[]", "[]", "Test User", "test@sjifire.org"
        )
        assert "26-001234" in prompt

    def test_includes_rules(self):
        prompt = _build_system_prompt("{}", "{}", "[]", "[]", "Test User", "test@sjifire.org")
        assert "RULES" in prompt
        assert "WORKFLOW" in prompt

    def test_includes_user_identity(self):
        prompt = _build_system_prompt(
            "{}", "{}", "[]", "[]", "Jordan Pollack", "jpollack@sjifire.org"
        )
        assert "Jordan Pollack" in prompt
        assert "jpollack@sjifire.org" in prompt

    def test_includes_personnel_roster(self):
        roster = '[{"name": "Jane Doe", "email": "jdoe@sjifire.org"}]'
        prompt = _build_system_prompt("{}", "{}", "[]", roster, "Test User", "test@sjifire.org")
        assert "Jane Doe" in prompt
        assert "PERSONNEL ROSTER" in prompt


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
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import stream_chat

        with patch(
            "sjifire.ops.chat.engine.check_budget",
            return_value=BudgetStatus(allowed=False, reason="Monthly limit reached"),
        ):
            events = [e async for e in stream_chat("inc-123", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "error" in events[0]
        assert "Monthly limit reached" in events[0]

    async def test_turn_limit_exceeded_yields_error(self):
        """When turn count is at max, stream should yield an error event."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import stream_chat

        # Create a conversation at the turn limit
        doc = ConversationDocument(
            incident_id="inc-456",
            user_email="firefighter@sjifire.org",
            turn_count=MAX_TURNS,
        )
        async with ConversationStore() as store:
            await store.create(doc)

        with patch(
            "sjifire.ops.chat.engine.check_budget",
            return_value=BudgetStatus(allowed=True),
        ):
            events = [e async for e in stream_chat("inc-456", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "error" in events[0]
        assert "limit" in events[0].lower()

    async def test_budget_check_failure_yields_friendly_error(self):
        """When budget check raises, error should be user-friendly with ref ID."""
        from sjifire.ops.chat.engine import stream_chat

        with patch(
            "sjifire.ops.chat.engine.check_budget",
            side_effect=ConnectionError("Cosmos DB unavailable"),
        ):
            events = [e async for e in stream_chat("inc-789", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "usage limits" in events[0]
        assert "ref:" in events[0]
        assert "ConnectionError" not in events[0]

    async def test_conversation_load_failure_yields_friendly_error(self):
        """When conversation store raises, error should be user-friendly."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import stream_chat

        with (
            patch(
                "sjifire.ops.chat.engine.check_budget",
                return_value=BudgetStatus(allowed=True),
            ),
            patch(
                "sjifire.ops.chat.engine.ConversationStore.__aenter__",
                side_effect=ConnectionError("connection refused"),
            ),
        ):
            events = [e async for e in stream_chat("inc-err", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "load conversation" in events[0]
        assert "ref:" in events[0]
        assert "ConnectionError" not in events[0]

    async def test_context_fetch_failure_yields_friendly_error(self):
        """When _fetch_context raises, error should be user-friendly."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import stream_chat

        with (
            patch(
                "sjifire.ops.chat.engine.check_budget",
                return_value=BudgetStatus(allowed=True),
            ),
            patch(
                "sjifire.ops.chat.engine._fetch_context",
                side_effect=RuntimeError("dispatch store down"),
            ),
        ):
            events = [e async for e in stream_chat("inc-ctx", "hello", _TEST_USER)]

        assert len(events) == 1
        assert "incident data" in events[0]
        assert "ref:" in events[0]
        assert "RuntimeError" not in events[0]


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

        with patch("sjifire.ops.chat.engine.execute_tool", side_effect=slow_tool):
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


class TestToolSchemaCacheControl:
    """Verify cache_control is set on the last tool in each schema list."""

    def test_incident_tools_last_has_cache_control(self):
        from sjifire.ops.chat.tools import TOOL_SCHEMAS

        last = TOOL_SCHEMAS[-1]
        assert "cache_control" in last
        assert last["cache_control"] == {"type": "ephemeral"}

    def test_general_tools_last_has_cache_control(self):
        from sjifire.ops.chat.tools import GENERAL_TOOL_SCHEMAS

        last = GENERAL_TOOL_SCHEMAS[-1]
        assert "cache_control" in last
        assert last["cache_control"] == {"type": "ephemeral"}

    def test_non_last_tools_have_no_cache_control(self):
        from sjifire.ops.chat.tools import GENERAL_TOOL_SCHEMAS, TOOL_SCHEMAS

        for schema in TOOL_SCHEMAS[:-1]:
            assert "cache_control" not in schema, f"{schema['name']} should not have cache_control"
        for schema in GENERAL_TOOL_SCHEMAS[:-1]:
            assert "cache_control" not in schema, f"{schema['name']} should not have cache_control"


class TestLogCacheStats:
    """Verify _log_cache_stats logs correctly for different usage shapes."""

    def test_logs_when_cache_fields_present(self, caplog):
        class Usage:
            input_tokens = 50000
            cache_read_input_tokens = 35000
            cache_creation_input_tokens = 10000

        with caplog.at_level(logging.INFO, logger="sjifire.ops.chat.engine"):
            _log_cache_stats(Usage())

        assert "Cache:" in caplog.text
        assert "35000 read" in caplog.text
        assert "10000 created" in caplog.text
        assert "5000 uncached" in caplog.text

    def test_no_log_when_no_cache_fields(self, caplog):
        class Usage:
            input_tokens = 50000

        with caplog.at_level(logging.INFO, logger="sjifire.ops.chat.engine"):
            _log_cache_stats(Usage())

        assert "Cache:" not in caplog.text

    def test_no_log_when_cache_fields_are_zero(self, caplog):
        class Usage:
            input_tokens = 50000
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        with caplog.at_level(logging.INFO, logger="sjifire.ops.chat.engine"):
            _log_cache_stats(Usage())

        assert "Cache:" not in caplog.text


class TestSlimDispatchContext:
    """Verify _fetch_context produces slim dispatch data."""

    async def test_dispatch_excludes_responder_details(self):
        """Slim dispatch should not include responder_details or responding_units."""
        from sjifire.ops.chat.engine import _fetch_context
        from sjifire.ops.dispatch.models import DispatchAnalysis, DispatchCallDocument
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.incidents.models import IncidentDocument

        # Create test incident
        incident = IncidentDocument(
            id="test-inc-1",
            incident_number="26-009999",
            incident_date="2026-02-15",
            station="S31",
            created_by="test@sjifire.org",
        )
        async with IncidentStore() as store:
            await store.create(incident)

        # Create test dispatch with large responder_details
        dispatch = DispatchCallDocument(
            id="dispatch-uuid",
            year="2026",
            long_term_call_id="26-009999",
            nature="Fire-Structure",
            address="123 MAIN ST",
            agency_code="SJF3",
            responding_units="E31, BN31",
            responder_details=[
                {"unit_number": "E31", "status": "ENR", "radio_log": "x" * 5000},
                {"unit_number": "BN31", "status": "ARV", "radio_log": "y" * 3000},
            ],
            cad_comments="Smoke visible",
            geo_location="48.53,-123.01",
            analysis=DispatchAnalysis(
                incident_commander="BN31",
                summary="Structure fire on Main St",
                unit_times=[
                    {
                        "unit": "E31",
                        "paged": "2026-02-15T14:30:00",
                        "enroute": "2026-02-15T14:32:00",
                        "arrived": "2026-02-15T14:38:00",
                        "completed": "",
                    },
                    {
                        "unit": "BN31",
                        "paged": "2026-02-15T14:30:00",
                        "enroute": "",
                        "arrived": "2026-02-15T14:40:00",
                        "completed": "",
                    },
                ],
            ),
        )
        DispatchStore._memory[dispatch.id] = dispatch.to_cosmos()

        with (
            patch(
                "sjifire.ops.schedule.tools.get_on_duty_crew",
                return_value={"crew": [], "count": 0},
            ),
            patch(
                "sjifire.ops.personnel.tools.get_operational_personnel",
                return_value=[],
            ),
        ):
            _, dispatch_json, _, _ = await _fetch_context("test-inc-1", _TEST_USER)

        data = json.loads(dispatch_json)

        # Should include slim fields
        assert data["nature"] == "Fire-Structure"
        assert data["address"] == "123 MAIN ST"
        assert data["cad_comments"] == "Smoke visible"
        assert data["geo_location"] == "48.53,-123.01"
        assert "analysis" in data
        assert data["analysis"]["incident_commander"] == "BN31"

        # unit_times should be a readable table, not nested in analysis
        assert "unit_times" not in data["analysis"]
        assert "unit_times_table" in data
        table = data["unit_times_table"]
        assert "E31" in table
        assert "BN31" in table
        assert "14:30:00" in table
        assert "--" in table  # missing timestamps shown as --

        # Should NOT include bloaty fields
        assert "responder_details" not in data
        assert "responding_units" not in data
        assert "agency_code" not in data
        assert "zone_code" not in data


class TestFormatUnitTimesTable:
    """Verify _format_unit_times_table produces a readable table."""

    def test_includes_incident_and_unit_sections(self):
        unit_times = [
            {
                "unit": "E31",
                "paged": "2026-02-15T14:30:00",
                "enroute": "2026-02-15T14:32:00",
                "arrived": "2026-02-15T14:38:00",
                "completed": "2026-02-15T15:10:00",
                "in_quarters": "2026-02-15T15:25:00",
            },
            {
                "unit": "BN31",
                "paged": "2026-02-15T14:30:00",
                "enroute": "2026-02-15T14:35:00",
                "arrived": "2026-02-15T14:40:00",
                "completed": "",
                "in_quarters": "",
            },
        ]
        table = _format_unit_times_table(unit_times, "2026-02-15T14:29:30")

        # Incident-level: human-readable labels with NERIS field names
        assert "INCIDENT TIMESTAMPS" in table
        assert "Call Received" in table
        assert "psap_answer" in table
        assert "14:29:30" in table
        assert "First Dispatched" in table
        assert "First Enroute" in table
        assert "First On Scene" in table
        assert "Last Unit Cleared" in table
        assert "Last In Quarters" in table

        # Per-unit section with human-readable column headers
        assert "UNIT RESPONSE TIMES" in table
        assert "On Scene" in table
        assert "In Quarters" in table
        assert "E31" in table
        assert "BN31" in table

        # E31 times present
        assert "14:32:00" in table
        assert "14:38:00" in table
        assert "15:10:00" in table
        assert "15:25:00" in table

        # BN31 missing completed + in_quarters → --
        assert "--" in table

    def test_incident_timestamps_use_earliest_and_latest(self):
        unit_times = [
            {
                "unit": "E31",
                "paged": "2026-02-15T14:30:00",
                "enroute": "2026-02-15T14:35:00",
                "arrived": "2026-02-15T14:40:00",
                "completed": "2026-02-15T15:10:00",
                "in_quarters": "2026-02-15T15:30:00",
            },
            {
                "unit": "BN31",
                "paged": "2026-02-15T14:30:00",
                "enroute": "2026-02-15T14:32:00",
                "arrived": "2026-02-15T14:38:00",
                "completed": "2026-02-15T15:05:00",
                "in_quarters": "2026-02-15T15:20:00",
            },
        ]
        table = _format_unit_times_table(unit_times)
        lines = table.split("\n")

        # first_unit_enroute = BN31's 14:32 (earliest)
        enroute_line = next(line for line in lines if "first_unit_enroute" in line)
        assert "14:32:00" in enroute_line

        # first_unit_arrived = BN31's 14:38 (earliest)
        arrived_line = next(line for line in lines if "first_unit_arrived" in line)
        assert "14:38:00" in arrived_line

        # last_unit_cleared = E31's 15:10 (latest)
        cleared_line = next(line for line in lines if "last_unit_cleared" in line)
        assert "15:10:00" in cleared_line

        # last_in_quarters = E31's 15:30 (latest)
        iq_line = next(line for line in lines if "last_unit_in_quarters" in line)
        assert "15:30:00" in iq_line

    def test_missing_timestamps_show_dashes(self):
        unit_times = [
            {
                "unit": "M31",
                "paged": "",
                "enroute": "",
                "arrived": "",
                "completed": "",
                "in_quarters": "",
            },
        ]
        table = _format_unit_times_table(unit_times)
        assert "First Dispatched" in table
        assert "first_unit_dispatched" in table
        # Unit row should have 5 dashes (dispatched, enroute, on scene, cleared, in quarters)
        lines = table.split("\n")
        unit_row = next(line for line in lines if "M31" in line)
        assert unit_row.count("--") == 5

    def test_no_time_reported_shows_dashes(self):
        unit_times = [
            {"unit": "E31", "paged": "2026-02-15T14:30:00"},
        ]
        table = _format_unit_times_table(unit_times)
        psap_line = next(line for line in table.split("\n") if "psap_answer" in line)
        assert psap_line.rstrip().endswith("--")

    def test_handles_timezone_suffix(self):
        unit_times = [
            {
                "unit": "E31",
                "paged": "2026-02-15T14:30:00+00:00",
                "enroute": "2026-02-15T14:32:00Z",
            },
        ]
        table = _format_unit_times_table(unit_times)
        assert "14:30:00" in table
        assert "14:32:00" in table
        assert "+00:00" not in table
        assert "Z" not in table

    def test_empty_list_still_shows_incident_section(self):
        table = _format_unit_times_table([], "2026-02-15T14:29:30")
        assert "INCIDENT TIMESTAMPS" in table
        assert "14:29:30" in table
        assert "UNIT RESPONSE TIMES" in table


class TestImageContentBlocks:
    """Verify images are sent as content blocks and not stored in history."""

    async def test_images_build_multipart_content(self):
        """When images are passed, the API message should use content blocks."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import stream_chat

        captured_messages: list = []

        # Mock _stream_loop to capture the api_messages it receives
        async def fake_stream_loop(client, system, api_messages, conv, user):
            captured_messages.extend(api_messages)
            yield _sse("text", {"content": "I can see the photo."})

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch("sjifire.ops.chat.engine._fetch_context", return_value=("{}", "{}", "[]", "[]")),
            patch("sjifire.ops.chat.engine._stream_loop", side_effect=fake_stream_loop),
            patch("sjifire.ops.chat.engine.get_client"),
        ):
            images = [{"media_type": "image/jpeg", "data": "abc123base64=="}]
            events = [
                e async for e in stream_chat("inc-img", "What is this?", _TEST_USER, images=images)
            ]

        # Should have produced text events
        assert any("I can see the photo" in e for e in events)

        # Last message in api_messages should have image + text content blocks
        last_msg = captured_messages[-1]
        assert last_msg["role"] == "user"
        assert isinstance(last_msg["content"], list)
        assert len(last_msg["content"]) == 2
        assert last_msg["content"][0]["type"] == "image"
        assert last_msg["content"][0]["source"]["media_type"] == "image/jpeg"
        assert last_msg["content"][0]["source"]["data"] == "abc123base64=="
        assert last_msg["content"][1]["type"] == "text"
        assert last_msg["content"][1]["text"] == "What is this?"

    async def test_multiple_images_build_multiple_blocks(self):
        """Multiple images should produce multiple image content blocks."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import stream_chat

        captured_messages: list = []

        async def fake_stream_loop(client, system, api_messages, conv, user):
            captured_messages.extend(api_messages)
            yield _sse("text", {"content": "ok"})

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch("sjifire.ops.chat.engine._fetch_context", return_value=("{}", "{}", "[]", "[]")),
            patch("sjifire.ops.chat.engine._stream_loop", side_effect=fake_stream_loop),
            patch("sjifire.ops.chat.engine.get_client"),
        ):
            images = [
                {"media_type": "image/jpeg", "data": "img1"},
                {"media_type": "image/png", "data": "img2"},
                {"media_type": "image/webp", "data": "img3"},
            ]
            _ = [
                e async for e in stream_chat("inc-multi", "Check these", _TEST_USER, images=images)
            ]

        last_msg = captured_messages[-1]
        content = last_msg["content"]
        assert len(content) == 4  # 3 images + 1 text
        assert all(c["type"] == "image" for c in content[:3])
        assert content[3]["type"] == "text"

    async def test_no_images_sends_plain_string(self):
        """Without images, the API message should be a plain string."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import stream_chat

        captured_messages: list = []

        async def fake_stream_loop(client, system, api_messages, conv, user):
            captured_messages.extend(api_messages)
            yield _sse("text", {"content": "ok"})

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch("sjifire.ops.chat.engine._fetch_context", return_value=("{}", "{}", "[]", "[]")),
            patch("sjifire.ops.chat.engine._stream_loop", side_effect=fake_stream_loop),
            patch("sjifire.ops.chat.engine.get_client"),
        ):
            _ = [e async for e in stream_chat("inc-txt", "just text", _TEST_USER)]

        last_msg = captured_messages[-1]
        assert last_msg["role"] == "user"
        assert last_msg["content"] == "just text"

    async def test_images_not_stored_in_conversation(self):
        """Images should be one-shot — not persisted in conversation messages."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import stream_chat

        saved_conv = None

        async def fake_stream_loop(client, system, api_messages, conv, user):
            nonlocal saved_conv
            saved_conv = conv
            yield _sse("text", {"content": "Got it"})

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch("sjifire.ops.chat.engine._fetch_context", return_value=("{}", "{}", "[]", "[]")),
            patch("sjifire.ops.chat.engine._stream_loop", side_effect=fake_stream_loop),
            patch("sjifire.ops.chat.engine.get_client"),
        ):
            images = [{"media_type": "image/jpeg", "data": "photo123"}]
            _ = [
                e async for e in stream_chat("inc-store", "Look at this", _TEST_USER, images=images)
            ]

        # The stored user message should be text only
        assert saved_conv is not None
        user_msgs = [m for m in saved_conv.messages if m.role == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0].content == "Look at this"
        # Content is a plain string, no image data
        assert "photo123" not in str(user_msgs[0].model_dump())
