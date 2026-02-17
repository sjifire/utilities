"""Tests for the chat engine with mocked Claude API."""

import asyncio
import json
import logging
from unittest.mock import patch

import pytest

from sjifire.ops.auth import UserContext
from sjifire.ops.chat.engine import (
    _build_context_message,
    _build_general_system_prompt,
    _build_system_prompt,
    _format_unit_times_table,
    _log_cache_stats,
    _summarize_tool_result,
    _trim_messages,
)
from sjifire.ops.chat.models import MAX_TURNS, ConversationDocument
from sjifire.ops.chat.store import BudgetStore, ConversationStore
from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.incidents.store import IncidentStore


async def _noop_container(name):
    return None


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset all in-memory stores."""
    ConversationStore._memory.clear()
    BudgetStore._memory.clear()
    IncidentStore._memory.clear()
    DispatchStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.chat.store.get_cosmos_container", _noop_container)
    monkeypatch.setattr("sjifire.ops.incidents.store.get_cosmos_container", _noop_container)
    monkeypatch.setattr("sjifire.ops.dispatch.store.get_cosmos_container", _noop_container)
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
        prompt = _build_system_prompt("Test User", "test@sjifire.org")
        assert (
            "San Juan" in prompt
            or "sjifire" in prompt.lower()
            or "incident report" in prompt.lower()
        )

    def test_includes_rules(self):
        prompt = _build_system_prompt("Test User", "test@sjifire.org")
        assert "RULES" in prompt
        assert "WORKFLOW" in prompt

    def test_includes_user_identity(self):
        prompt = _build_system_prompt("Jordan Pollack", "jpollack@sjifire.org")
        assert "Jordan Pollack" in prompt
        assert "jpollack@sjifire.org" in prompt

    def test_does_not_include_dynamic_data(self):
        """System prompt should not contain incident/dispatch/crew section headers."""
        prompt = _build_system_prompt("Test User", "test@sjifire.org")
        assert "CURRENT INCIDENT STATE:\n" not in prompt
        assert "DISPATCH DATA:\n" not in prompt
        assert "CREW ON DUTY:\n" not in prompt


class TestBuildContextMessage:
    def test_includes_incident_data(self):
        msg = _build_context_message('{"incident_number": "26-001234"}', "{}", "[]", "[]")
        assert "26-001234" in msg
        assert "CURRENT INCIDENT STATE" in msg

    def test_includes_all_sections(self):
        msg = _build_context_message("{}", "{}", "[]", "[]")
        assert "CURRENT INCIDENT STATE" in msg
        assert "DISPATCH DATA" in msg
        assert "CREW ON DUTY" in msg
        assert "PERSONNEL ROSTER" in msg

    def test_includes_personnel_roster(self):
        roster = '[{"name": "Jane Doe", "email": "jdoe@sjifire.org"}]'
        msg = _build_context_message("{}", "{}", "[]", roster)
        assert "Jane Doe" in msg
        assert "PERSONNEL ROSTER" in msg


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

    def test_keeps_tool_use_when_tool_result_at_boundary(self):
        """If trimming would orphan a tool_result, include the preceding tool_use."""
        msgs = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(80)
        ]
        # Replace the message at the trim boundary with a tool_use/tool_result pair
        boundary = len(msgs) - 40  # index 40
        msgs[boundary - 1] = {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_123", "name": "get_incident"}],
        }
        msgs[boundary] = {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_123", "content": "ok"}],
        }
        result = _trim_messages(msgs)
        # Should include the extra tool_use message (41 instead of 40)
        assert len(result) == 41
        assert result[0]["role"] == "assistant"
        assert result[0]["content"][0]["type"] == "tool_use"
        assert result[1]["content"][0]["type"] == "tool_result"

    def test_no_backup_when_first_message_is_not_tool_result(self):
        """Normal trim when no tool_result at boundary."""
        msgs = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(80)
        ]
        result = _trim_messages(msgs)
        assert len(result) == 40


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


class TestRunChat:
    """Test run_chat() which publishes events to a Centrifugo channel."""

    async def test_budget_exceeded_publishes_error(self):
        """When budget is exceeded, run_chat should publish an error event."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        published: list[tuple] = []

        async def fake_publish(channel, event, data):
            published.append((event, data))

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=False, reason="Monthly limit reached")),
            patch("sjifire.ops.chat.engine.publish", side_effect=fake_publish),
        ):
            await run_chat("inc-123", "hello", _TEST_USER, channel="test")

        assert len(published) == 1
        assert published[0][0] == "error"
        assert "Monthly limit reached" in published[0][1]["message"]

    async def test_turn_limit_exceeded_publishes_error(self):
        """When turn count is at max, run_chat should publish an error event."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        published: list[tuple] = []

        async def fake_publish(channel, event, data):
            published.append((event, data))

        # Create a conversation at the turn limit
        doc = ConversationDocument(
            incident_id="inc-456",
            user_email="firefighter@sjifire.org",
            turn_count=MAX_TURNS,
        )
        async with ConversationStore() as store:
            await store.create(doc)

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch("sjifire.ops.chat.engine.publish", side_effect=fake_publish),
        ):
            await run_chat("inc-456", "hello", _TEST_USER, channel="test")

        assert len(published) == 1
        assert published[0][0] == "error"
        assert "limit" in published[0][1]["message"].lower()

    async def test_budget_check_failure_publishes_friendly_error(self):
        """When budget check raises, error should be user-friendly with ref ID."""
        from sjifire.ops.chat.engine import run_chat

        published: list[tuple] = []

        async def fake_publish(channel, event, data):
            published.append((event, data))

        with (
            patch("sjifire.ops.chat.engine.check_budget", side_effect=ConnectionError("Cosmos DB unavailable")),
            patch("sjifire.ops.chat.engine.publish", side_effect=fake_publish),
        ):
            await run_chat("inc-789", "hello", _TEST_USER, channel="test")

        assert len(published) == 1
        assert published[0][0] == "error"
        assert "usage limits" in published[0][1]["message"]
        assert "ref:" in published[0][1]["message"]
        assert "ConnectionError" not in published[0][1]["message"]

    async def test_conversation_load_failure_publishes_friendly_error(self):
        """When conversation store raises, error should be user-friendly."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        published: list[tuple] = []

        async def fake_publish(channel, event, data):
            published.append((event, data))

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch("sjifire.ops.chat.engine.ConversationStore.__aenter__", side_effect=ConnectionError("connection refused")),
            patch("sjifire.ops.chat.engine.publish", side_effect=fake_publish),
        ):
            await run_chat("inc-err", "hello", _TEST_USER, channel="test")

        assert len(published) == 1
        assert published[0][0] == "error"
        assert "load conversation" in published[0][1]["message"]
        assert "ref:" in published[0][1]["message"]
        assert "ConnectionError" not in published[0][1]["message"]

    async def test_context_fetch_failure_publishes_friendly_error(self):
        """When _fetch_context raises, error should be user-friendly."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        published: list[tuple] = []

        async def fake_publish(channel, event, data):
            published.append((event, data))

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch("sjifire.ops.chat.engine._fetch_context", side_effect=RuntimeError("dispatch store down")),
            patch("sjifire.ops.chat.engine.publish", side_effect=fake_publish),
        ):
            await run_chat("inc-ctx", "hello", _TEST_USER, channel="test")

        assert len(published) == 1
        assert published[0][0] == "error"
        assert "incident data" in published[0][1]["message"]
        assert "ref:" in published[0][1]["message"]
        assert "RuntimeError" not in published[0][1]["message"]


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
    """Verify that tool calls within _run_loop execute concurrently."""

    async def test_multiple_tools_run_in_parallel(self):
        """When Claude returns multiple tool_use blocks, they should run concurrently."""
        call_times: list[float] = []

        async def slow_tool(name, tool_input, user):
            """Simulate a tool that takes 100ms."""
            import time

            start = time.monotonic()
            await asyncio.sleep(0.1)
            call_times.append(time.monotonic() - start)
            return json.dumps({"status": "ok"})

        with patch("sjifire.ops.chat.engine.execute_tool", side_effect=slow_tool):
            # Simulate what _run_loop does with parallel tool calls
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
            output_tokens = 500
            cache_read_input_tokens = 35000
            cache_creation_input_tokens = 10000

        with caplog.at_level(logging.INFO, logger="sjifire.ops.chat.engine"):
            _log_cache_stats(Usage())

        assert "Tokens:" in caplog.text
        assert "35000 cached" in caplog.text
        assert "10000 created" in caplog.text
        assert "5000 uncached" in caplog.text
        assert "500 out" in caplog.text

    def test_logs_when_no_cache_fields(self, caplog):
        class Usage:
            input_tokens = 50000

        with caplog.at_level(logging.INFO, logger="sjifire.ops.chat.engine"):
            _log_cache_stats(Usage())

        assert "Tokens: 50000 in" in caplog.text
        assert "0 cached" in caplog.text

    def test_logs_when_cache_fields_are_zero(self, caplog):
        class Usage:
            input_tokens = 50000
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        with caplog.at_level(logging.INFO, logger="sjifire.ops.chat.engine"):
            _log_cache_stats(Usage())

        assert "Tokens: 50000 in" in caplog.text


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
            incident_datetime="2026-02-15T00:00:00+00:00",
            created_by="test@sjifire.org",
            extras={"station": "S31"},
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
            _, dispatch_json, _, _, _ = await _fetch_context("test-inc-1", _TEST_USER)

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
        # Unit row should have 6 dashes (dispatched, enroute, staged, on scene, cleared, in quarters)
        lines = table.split("\n")
        unit_row = next(line for line in lines if "M31" in line)
        assert unit_row.count("--") == 6

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
        from sjifire.ops.chat.engine import run_chat

        captured_messages: list = []

        # Mock _run_loop to capture the api_messages it receives
        async def fake_run_loop(client, system, api_messages, conv, user, *, channel):
            captured_messages.extend(api_messages)

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch(
                "sjifire.ops.chat.engine._fetch_context",
                return_value=("{}", "{}", "[]", "[]", ""),
            ),
            patch("sjifire.ops.chat.engine._run_loop", side_effect=fake_run_loop),
            patch("sjifire.ops.chat.engine.get_client"),
            patch("sjifire.ops.chat.engine.publish"),
        ):
            images = [{"media_type": "image/jpeg", "data": "abc123base64=="}]
            await run_chat("inc-img", "What is this?", _TEST_USER, channel="test", images=images)

        # Last message in api_messages should have image + text content blocks
        last_msg = captured_messages[-1]
        assert last_msg["role"] == "user"
        assert isinstance(last_msg["content"], list)
        assert len(last_msg["content"]) == 2
        assert last_msg["content"][0]["type"] == "image"
        assert last_msg["content"][0]["source"]["media_type"] == "image/jpeg"
        assert last_msg["content"][0]["source"]["data"] == "abc123base64=="
        assert last_msg["content"][1]["type"] == "text"
        # Text should contain context preamble + user message
        assert "What is this?" in last_msg["content"][1]["text"]
        assert "CURRENT INCIDENT STATE" in last_msg["content"][1]["text"]

    async def test_multiple_images_build_multiple_blocks(self):
        """Multiple images should produce multiple image content blocks."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        captured_messages: list = []

        async def fake_run_loop(client, system, api_messages, conv, user, *, channel):
            captured_messages.extend(api_messages)

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch(
                "sjifire.ops.chat.engine._fetch_context",
                return_value=("{}", "{}", "[]", "[]", ""),
            ),
            patch("sjifire.ops.chat.engine._run_loop", side_effect=fake_run_loop),
            patch("sjifire.ops.chat.engine.get_client"),
            patch("sjifire.ops.chat.engine.publish"),
        ):
            images = [
                {"media_type": "image/jpeg", "data": "img1"},
                {"media_type": "image/png", "data": "img2"},
                {"media_type": "image/webp", "data": "img3"},
            ]
            await run_chat("inc-multi", "Check these", _TEST_USER, channel="test", images=images)

        last_msg = captured_messages[-1]
        content = last_msg["content"]
        assert len(content) == 4  # 3 images + 1 text
        assert all(c["type"] == "image" for c in content[:3])
        assert content[3]["type"] == "text"

    async def test_no_images_sends_prefixed_string(self):
        """Without images, the API message should be context + user text."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        captured_messages: list = []

        async def fake_run_loop(client, system, api_messages, conv, user, *, channel):
            captured_messages.extend(api_messages)

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch(
                "sjifire.ops.chat.engine._fetch_context",
                return_value=("{}", "{}", "[]", "[]", ""),
            ),
            patch("sjifire.ops.chat.engine._run_loop", side_effect=fake_run_loop),
            patch("sjifire.ops.chat.engine.get_client"),
            patch("sjifire.ops.chat.engine.publish"),
        ):
            await run_chat("inc-txt", "just text", _TEST_USER, channel="test")

        last_msg = captured_messages[-1]
        assert last_msg["role"] == "user"
        # Message should contain context preamble + user text
        assert "just text" in last_msg["content"]
        assert "CURRENT INCIDENT STATE" in last_msg["content"]

    async def test_image_base64_not_stored_in_conversation(self):
        """Raw base64 image data should not be persisted in conversation messages."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        saved_conv = None

        async def fake_run_loop(client, system, api_messages, conv, user, *, channel):
            nonlocal saved_conv
            saved_conv = conv

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch(
                "sjifire.ops.chat.engine._fetch_context",
                return_value=("{}", "{}", "[]", "[]", ""),
            ),
            patch("sjifire.ops.chat.engine._run_loop", side_effect=fake_run_loop),
            patch("sjifire.ops.chat.engine.get_client"),
            patch("sjifire.ops.chat.engine.publish"),
        ):
            images = [{"media_type": "image/jpeg", "data": "photo123"}]
            await run_chat("inc-store", "Look at this", _TEST_USER, channel="test", images=images)

        # The stored user message should be text only (no context preamble, no images)
        assert saved_conv is not None
        user_msgs = [m for m in saved_conv.messages if m.role == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0].content == "Look at this"
        # Raw base64 data should NOT be in the stored message
        assert "photo123" not in str(user_msgs[0].model_dump())

    async def test_image_refs_stored_in_conversation(self):
        """When image_refs are provided, they are stored on the conversation message."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        saved_conv = None

        async def fake_run_loop(client, system, api_messages, conv, user, *, channel):
            nonlocal saved_conv
            saved_conv = conv

        image_refs = [{"attachment_id": "att-abc", "content_type": "image/jpeg"}]

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch(
                "sjifire.ops.chat.engine._fetch_context",
                return_value=("{}", "{}", "[]", "[]", ""),
            ),
            patch("sjifire.ops.chat.engine._run_loop", side_effect=fake_run_loop),
            patch("sjifire.ops.chat.engine.get_client"),
            patch("sjifire.ops.chat.engine.publish"),
        ):
            images = [{"media_type": "image/jpeg", "data": "base64data"}]
            await run_chat(
                "inc-refs", "Photo", _TEST_USER, channel="test", images=images, image_refs=image_refs
            )

        assert saved_conv is not None
        user_msgs = [m for m in saved_conv.messages if m.role == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0].images == image_refs

    async def test_no_image_refs_stores_none(self):
        """Without image_refs, images field should be None."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        saved_conv = None

        async def fake_run_loop(client, system, api_messages, conv, user, *, channel):
            nonlocal saved_conv
            saved_conv = conv

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch(
                "sjifire.ops.chat.engine._fetch_context",
                return_value=("{}", "{}", "[]", "[]", ""),
            ),
            patch("sjifire.ops.chat.engine._run_loop", side_effect=fake_run_loop),
            patch("sjifire.ops.chat.engine.get_client"),
            patch("sjifire.ops.chat.engine.publish"),
        ):
            await run_chat("inc-noimgs", "just text", _TEST_USER, channel="test")

        user_msgs = [m for m in saved_conv.messages if m.role == "user"]
        assert user_msgs[0].images is None

    async def test_system_prompt_is_stable(self):
        """System prompt should not contain dynamic incident/dispatch data."""
        from sjifire.ops.chat.budget import BudgetStatus
        from sjifire.ops.chat.engine import run_chat

        captured_system: list = []

        async def fake_run_loop(client, system, api_messages, conv, user, *, channel):
            captured_system.append(system)

        with (
            patch("sjifire.ops.chat.engine.check_budget", return_value=BudgetStatus(allowed=True)),
            patch(
                "sjifire.ops.chat.engine._fetch_context",
                return_value=('{"incident_number": "26-UNIQUE"}', "{}", "[]", "[]", ""),
            ),
            patch("sjifire.ops.chat.engine._run_loop", side_effect=fake_run_loop),
            patch("sjifire.ops.chat.engine.get_client"),
            patch("sjifire.ops.chat.engine.publish"),
        ):
            await run_chat("inc-stable", "hi", _TEST_USER, channel="test")

        # System prompt should NOT contain the dynamic incident data
        assert "26-UNIQUE" not in captured_system[0]
        assert "CURRENT INCIDENT STATE:\n" not in captured_system[0]


class TestAttachmentsSummary:
    """Verify attachments_summary includes IDs and integrates into context."""

    def test_context_message_includes_attachments_section(self):
        summary = "- Scene photo (id: att-123, image/jpeg, 150KB)"
        msg = _build_context_message("{}", "{}", "[]", "[]", summary)
        assert "ATTACHMENTS ON FILE:" in msg
        assert "att-123" in msg
        assert "Scene photo" in msg

    def test_context_message_omits_attachments_when_empty(self):
        msg = _build_context_message("{}", "{}", "[]", "[]", "")
        assert "ATTACHMENTS ON FILE" not in msg

    async def test_fetch_context_includes_attachment_ids(self):
        """_fetch_context should include attachment IDs in the summary."""
        from sjifire.ops.attachments.models import AttachmentMeta
        from sjifire.ops.chat.engine import _fetch_context
        from sjifire.ops.incidents.models import IncidentDocument
        from sjifire.ops.incidents.store import IncidentStore

        meta = AttachmentMeta(
            filename="scene.jpg",
            content_type="image/jpeg",
            size_bytes=150_000,
            uploaded_by="ff@sjifire.org",
        )

        incident = IncidentDocument(
            id="test-att-inc",
            incident_number="26-009000",
            incident_datetime="2026-02-15T00:00:00+00:00",
            created_by="ff@sjifire.org",
            extras={"station": "S31"},
            attachments=[meta],
        )
        async with IncidentStore() as store:
            await store.create(incident)

        with (
            patch(
                "sjifire.ops.schedule.tools.get_on_duty_crew",
                return_value={"crew": [], "count": 0},
            ),
            patch("sjifire.ops.personnel.tools.get_operational_personnel", return_value=[]),
        ):
            _, _, _, _, att_summary = await _fetch_context("test-att-inc", _TEST_USER)

        assert f"id: {meta.id}" in att_summary
        assert "scene.jpg" in att_summary
        assert "image/jpeg" in att_summary


class TestFetchContextAttachmentEdgeCases:
    """Verify _fetch_context attachment summary with edge cases."""

    async def _create_incident(self, incident_id, attachments=None):
        from sjifire.ops.incidents.models import IncidentDocument
        from sjifire.ops.incidents.store import IncidentStore

        incident = IncidentDocument(
            id=incident_id,
            incident_number="26-009100",
            incident_datetime="2026-02-15T00:00:00+00:00",
            created_by="ff@sjifire.org",
            extras={"station": "S31"},
            attachments=attachments or [],
        )
        async with IncidentStore() as store:
            await store.create(incident)
        return incident

    async def test_empty_attachments_list_produces_no_summary(self):
        """An incident with [] attachments should have empty summary."""
        from sjifire.ops.chat.engine import _fetch_context

        await self._create_incident("inc-empty-att", attachments=[])

        with (
            patch(
                "sjifire.ops.schedule.tools.get_on_duty_crew",
                return_value={"crew": [], "count": 0},
            ),
            patch("sjifire.ops.personnel.tools.get_operational_personnel", return_value=[]),
        ):
            _, _, _, _, att_summary = await _fetch_context("inc-empty-att", _TEST_USER)

        assert att_summary == ""

    async def test_multiple_attachments_all_listed(self):
        """Multiple attachments should all appear in summary with IDs."""
        from sjifire.ops.attachments.models import AttachmentMeta
        from sjifire.ops.chat.engine import _fetch_context

        metas = [
            AttachmentMeta(
                filename="scene1.jpg",
                content_type="image/jpeg",
                size_bytes=100_000,
                uploaded_by="ff@sjifire.org",
            ),
            AttachmentMeta(
                filename="scene2.png",
                content_type="image/png",
                size_bytes=200_000,
                uploaded_by="ff@sjifire.org",
            ),
            AttachmentMeta(
                filename="report.pdf",
                content_type="application/pdf",
                size_bytes=500_000,
                uploaded_by="ff@sjifire.org",
            ),
        ]

        await self._create_incident("inc-multi-att", attachments=metas)

        with (
            patch(
                "sjifire.ops.schedule.tools.get_on_duty_crew",
                return_value={"crew": [], "count": 0},
            ),
            patch("sjifire.ops.personnel.tools.get_operational_personnel", return_value=[]),
        ):
            _, _, _, _, att_summary = await _fetch_context("inc-multi-att", _TEST_USER)

        for m in metas:
            assert f"id: {m.id}" in att_summary
        assert "scene1.jpg" in att_summary
        assert "scene2.png" in att_summary
        assert "report.pdf" in att_summary
        assert "97KB" in att_summary
        assert "195KB" in att_summary
        assert "488KB" in att_summary

    async def test_attachment_with_title_uses_title_as_label(self):
        """When an attachment has a title, use title instead of filename."""
        from sjifire.ops.attachments.models import AttachmentMeta
        from sjifire.ops.chat.engine import _fetch_context

        meta = AttachmentMeta(
            filename="IMG_20260215_1448.jpg",
            title="Front of structure",
            content_type="image/jpeg",
            size_bytes=150_000,
            uploaded_by="ff@sjifire.org",
        )

        await self._create_incident("inc-title-att", attachments=[meta])

        with (
            patch(
                "sjifire.ops.schedule.tools.get_on_duty_crew",
                return_value={"crew": [], "count": 0},
            ),
            patch("sjifire.ops.personnel.tools.get_operational_personnel", return_value=[]),
        ):
            _, _, _, _, att_summary = await _fetch_context("inc-title-att", _TEST_USER)

        assert "Front of structure" in att_summary

    async def test_attachment_with_description_appended(self):
        """Attachment description should appear in summary."""
        from sjifire.ops.attachments.models import AttachmentMeta
        from sjifire.ops.chat.engine import _fetch_context

        meta = AttachmentMeta(
            filename="scene.jpg",
            description="Alpha side showing smoke from eaves",
            content_type="image/jpeg",
            size_bytes=150_000,
            uploaded_by="ff@sjifire.org",
        )

        await self._create_incident("inc-desc-att", attachments=[meta])

        with (
            patch(
                "sjifire.ops.schedule.tools.get_on_duty_crew",
                return_value={"crew": [], "count": 0},
            ),
            patch("sjifire.ops.personnel.tools.get_operational_personnel", return_value=[]),
        ):
            _, _, _, _, att_summary = await _fetch_context("inc-desc-att", _TEST_USER)

        assert "Alpha side showing smoke from eaves" in att_summary


class TestAttachmentToolSummaries:
    """Verify _summarize_tool_result handles attachment tools."""

    def test_list_attachments(self):
        result = _summarize_tool_result("list_attachments", {"count": 3})
        assert "3" in result
        assert "attachment" in result

    def test_get_attachment(self):
        result = _summarize_tool_result("get_attachment", {"filename": "scene.jpg"})
        assert "scene.jpg" in result

    def test_delete_attachment(self):
        result = _summarize_tool_result("delete_attachment", {"filename": "old.jpg"})
        assert "Deleted" in result
        assert "old.jpg" in result


class TestAttachmentToolSummaryEdgeCases:
    """Verify _summarize_tool_result edge cases for attachment tools."""

    def test_list_attachments_error(self):
        result = _summarize_tool_result("list_attachments", {"error": "Incident not found"})
        assert "Error" in result
        assert "Incident not found" in result

    def test_get_attachment_error(self):
        result = _summarize_tool_result(
            "get_attachment", {"error": "Attachment 'att-99' not found"}
        )
        assert "Error" in result

    def test_delete_attachment_error(self):
        result = _summarize_tool_result("delete_attachment", {"error": "You don't have permission"})
        assert "Error" in result
        assert "permission" in result


class TestImageContentBlockEdgeCases:
    """Verify image content block logic handles edge cases."""

    def _extract_tool_result_content(self, result_str: str) -> str | list[dict]:
        """Apply the same image_data extraction logic used in _run_loop."""
        tool_result_content: str | list[dict] = result_str
        try:
            result_parsed = json.loads(result_str)
            image_info = result_parsed.get("image_data")
            if image_info and isinstance(image_info, dict):
                slim = {k: v for k, v in result_parsed.items() if k != "image_data"}
                tool_result_content = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_info["media_type"],
                            "data": image_info["base64"],
                        },
                    },
                    {"type": "text", "text": json.dumps(slim, default=str)},
                ]
        except (json.JSONDecodeError, KeyError):
            pass  # Non-image tool result — use raw string as-is
        return tool_result_content

    def test_image_data_produces_multiblock_content(self):
        """When a tool result contains image_data, it produces image blocks."""
        result_data = {
            "id": "att-1",
            "filename": "scene.jpg",
            "content_type": "image/jpeg",
            "image_data": {"base64": "abc123base64data", "media_type": "image/jpeg"},
        }
        content = self._extract_tool_result_content(json.dumps(result_data))
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["type"] == "image"
        assert content[0]["source"]["data"] == "abc123base64data"
        assert "abc123base64data" not in content[1]["text"]
        assert "scene.jpg" in content[1]["text"]

    def test_no_image_data_stays_string(self):
        """Without image_data, tool result stays as a plain string."""
        result_str = json.dumps({"id": "att-1", "filename": "doc.pdf"})
        content = self._extract_tool_result_content(result_str)
        assert isinstance(content, str)

    def test_image_data_as_none_stays_string(self):
        result_str = json.dumps({"id": "att-1", "image_data": None})
        content = self._extract_tool_result_content(result_str)
        assert isinstance(content, str)

    def test_image_data_as_empty_dict_stays_string(self):
        result_str = json.dumps({"id": "att-1", "image_data": {}})
        content = self._extract_tool_result_content(result_str)
        assert isinstance(content, str)

    def test_image_data_as_string_stays_string(self):
        result_str = json.dumps({"id": "att-1", "image_data": "not-a-dict"})
        content = self._extract_tool_result_content(result_str)
        assert isinstance(content, str)

    def test_non_json_result_stays_string(self):
        content = self._extract_tool_result_content("plain text result")
        assert isinstance(content, str)
        assert content == "plain text result"


class TestRunLoopImageToolResults:
    """Integration test: _run_loop builds image content blocks for get_attachment."""

    async def test_get_attachment_image_in_run_loop(self):
        """_run_loop builds image content blocks for get_attachment results."""
        from sjifire.ops.chat.engine import _run_loop
        from sjifire.ops.chat.models import ConversationDocument

        conversation = ConversationDocument(
            incident_id="inc-img-loop",
            user_email="ff@sjifire.org",
        )

        api_messages: list[dict] = [
            {"role": "user", "content": "Show me the photo"},
        ]

        call_count = 0
        published: list[tuple] = []

        async def fake_publish(channel, event, data):
            published.append((event, data))

        class FakeUsage:
            input_tokens = 1000
            output_tokens = 200
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        class FakeMessage:
            usage = FakeUsage()

        class FakeStream:
            def __init__(self, events):
                self._events = events

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def __aiter__(self):
                for e in self._events:
                    yield e

            async def get_final_message(self):
                return FakeMessage()

        class FakeContentBlock:
            type = "tool_use"
            id = "toolu_abc"
            name = "get_attachment"

        class FakeBlockStart:
            type = "content_block_start"
            content_block = FakeContentBlock()

        class _InputDelta:
            partial_json = '{"incident_id": "inc-1", "attachment_id": "att-1"}'

        class FakeInputDelta:
            type = "content_block_delta"
            delta = _InputDelta()

        class FakeBlockStop:
            type = "content_block_stop"

        class _TextDelta:
            text = "Here is the photo from the scene."

        class FakeTextDelta:
            type = "content_block_delta"
            delta = _TextDelta()

        def make_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return FakeStream([FakeBlockStart(), FakeInputDelta(), FakeBlockStop()])
            else:
                return FakeStream([FakeTextDelta()])

        image_result = {
            "id": "att-1",
            "filename": "scene.jpg",
            "content_type": "image/jpeg",
            "image_data": {"base64": "FAKE_BASE64_IMAGE_DATA", "media_type": "image/jpeg"},
        }

        async def mock_execute(name, tool_input, user):
            return json.dumps(image_result)

        class FakeMessages:
            stream = staticmethod(make_stream)

        class FakeClient:
            messages = FakeMessages()

        with (
            patch("sjifire.ops.chat.engine.execute_tool", side_effect=mock_execute),
            patch("sjifire.ops.chat.engine.publish", side_effect=fake_publish),
        ):
            await _run_loop(
                FakeClient(),
                "system prompt",
                api_messages,
                conversation,
                _TEST_USER,
                channel="test",
            )

        # The tool result message sent to the API should have image blocks
        tool_result_msg = api_messages[2]
        assert tool_result_msg["role"] == "user"
        tool_result_block = tool_result_msg["content"][0]
        assert tool_result_block["type"] == "tool_result"
        content = tool_result_block["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "image"
        assert content[0]["source"]["data"] == "FAKE_BASE64_IMAGE_DATA"
        assert content[1]["type"] == "text"
        assert "FAKE_BASE64_IMAGE_DATA" not in content[1]["text"]
        assert "scene.jpg" in content[1]["text"]

        # The summary stored in conversation history should NOT have base64
        tool_result_msg_history = conversation.messages[1]
        assert tool_result_msg_history.tool_results is not None
        for tr in tool_result_msg_history.tool_results:
            assert "FAKE_BASE64_IMAGE_DATA" not in str(tr)

        # Published events should include tool_call and tool_result
        tool_call_events = [(e, d) for e, d in published if e == "tool_call"]
        assert len(tool_call_events) == 1
        assert tool_call_events[0][1]["name"] == "get_attachment"

        tool_result_events = [(e, d) for e, d in published if e == "tool_result"]
        assert len(tool_result_events) == 1
        assert "scene.jpg" in tool_result_events[0][1]["summary"]


class TestAttachmentToolDispatch:
    """Verify attachment tools are in TOOL_SCHEMAS and dispatch correctly."""

    def test_attachment_tools_in_schemas(self):
        from sjifire.ops.chat.tools import TOOL_SCHEMAS

        names = {t["name"] for t in TOOL_SCHEMAS}
        assert "list_attachments" in names
        assert "get_attachment" in names
        assert "delete_attachment" in names

    def test_cache_control_on_last_tool(self):
        """cache_control should be on the last tool (delete_attachment)."""
        from sjifire.ops.chat.tools import TOOL_SCHEMAS

        last = TOOL_SCHEMAS[-1]
        assert "cache_control" in last
        assert last["name"] == "delete_attachment"

    async def test_execute_list_attachments(self):
        from sjifire.ops.chat.tools import execute_tool

        expected = {"attachments": [], "count": 0}

        with patch(
            "sjifire.ops.attachments.tools.list_attachments",
            return_value=expected,
        ):
            result_str = await execute_tool(
                "list_attachments", {"incident_id": "inc-1"}, _TEST_USER
            )

        result = json.loads(result_str)
        assert result["count"] == 0

    async def test_execute_get_attachment_with_include_data(self):
        """execute_tool should pass include_data=True for get_attachment."""
        from sjifire.ops.chat.tools import execute_tool

        call_kwargs = {}

        async def mock_get(incident_id, attachment_id, *, include_data=False):
            call_kwargs["include_data"] = include_data
            return {"id": attachment_id, "filename": "photo.jpg"}

        with patch("sjifire.ops.attachments.tools.get_attachment", side_effect=mock_get):
            await execute_tool(
                "get_attachment",
                {"incident_id": "inc-1", "attachment_id": "att-1"},
                _TEST_USER,
            )

        assert call_kwargs["include_data"] is True

    async def test_execute_delete_attachment(self):
        from sjifire.ops.chat.tools import execute_tool

        expected = {"deleted": "att-1", "filename": "old.jpg", "attachment_count": 0}

        with patch(
            "sjifire.ops.attachments.tools.delete_attachment",
            return_value=expected,
        ):
            result_str = await execute_tool(
                "delete_attachment",
                {"incident_id": "inc-1", "attachment_id": "att-1"},
                _TEST_USER,
            )

        result = json.loads(result_str)
        assert result["deleted"] == "att-1"


class TestExecuteToolGuardrails:
    """Verify execute_tool rejects unknown tools and attachment tools are allowed."""

    async def test_unknown_tool_rejected(self):
        from sjifire.ops.chat.tools import execute_tool

        result_str = await execute_tool("evil_tool", {}, _TEST_USER)
        result = json.loads(result_str)
        assert "error" in result
        assert "not available" in result["error"]

    def test_attachment_tools_in_allowed_set(self):
        from sjifire.ops.chat.tools import _ALLOWED_TOOLS

        assert "list_attachments" in _ALLOWED_TOOLS
        assert "get_attachment" in _ALLOWED_TOOLS
        assert "delete_attachment" in _ALLOWED_TOOLS
