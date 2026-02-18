"""Tests for schedule_refresh task — JSON extraction + HTML fallback.

Generates On Duty event HTML via AllDayDutyEvent.body_html (which now
embeds a CREW_DATA JSON comment), parses it back with parse_duty_event_html,
and verifies crew data matches the original input.

Also tests the legacy HTML-table fallback for events created before JSON
embedding was added.
"""

import json
import os
import re
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.calendar.models import CREW_DATA_MARKER, AllDayDutyEvent, CrewMember
from sjifire.ops.schedule.tools import (
    _parse_crew_data_json,
    _parse_duty_event_html_tables,
    fetch_schedule_from_outlook,
    parse_duty_event_html,
)
from sjifire.ops.tasks.schedule_refresh import schedule_refresh


@pytest.fixture(autouse=True)
def _dev_mode():
    """Ensure dev mode (no Cosmos/Aladtec config) so imports work."""
    with patch.dict(
        os.environ,
        {
            "COSMOS_ENDPOINT": "",
            "COSMOS_KEY": "",
            "ALADTEC_URL": "https://example.aladtec.com",
            "ALADTEC_USERNAME": "test",
            "ALADTEC_PASSWORD": "test",
        },
        clear=False,
    ):
        yield


def _make_duty_event(shift_hour: int = 18) -> AllDayDutyEvent:
    """Build an AllDayDutyEvent with representative crew data."""
    return AllDayDutyEvent(
        event_date=date(2026, 2, 17),
        shift_change_hour=shift_hour,
        until_platoon="A Platoon",
        from_platoon="B Platoon",
        until_crew={
            "Station 31": [
                CrewMember(name="Alice Smith", position="Captain", email="alice@sjifire.org"),
                CrewMember(name="Bob Jones", position="Firefighter", phone="360-555-1234"),
            ],
        },
        from_crew={
            "Station 31": [
                CrewMember(name="Charlie Brown", position="Captain", email="charlie@sjifire.org"),
                CrewMember(name="Dana White", position="Apparatus Operator"),
                CrewMember(
                    name="Eve Davis",
                    position="Firefighter",
                    email="eve@sjifire.org",
                    phone="360-555-5678",
                ),
            ],
            "Fireboat 31 Standby": [
                CrewMember(name="Frank Miller", position="Marine: Pilot"),
            ],
        },
    )


def _strip_crew_data_comment(html: str) -> str:
    """Remove the CREW_DATA comment to simulate a legacy (pre-JSON) event."""
    return re.sub(r"<!--\s*CREW_DATA:.*?-->", "", html, flags=re.DOTALL)


# ---------------------------------------------------------------------------
# JSON comment embedding in body_html
# ---------------------------------------------------------------------------


class TestCrewDataJsonEmbedding:
    """Verify AllDayDutyEvent.body_html embeds structured JSON."""

    def test_body_html_contains_crew_data_comment(self):
        """body_html must contain a CREW_DATA HTML comment."""
        event = _make_duty_event()
        html = event.body_html
        assert f"<!-- {CREW_DATA_MARKER}" in html
        assert "-->" in html

    def test_crew_data_json_is_valid(self):
        """The embedded JSON is parseable."""
        event = _make_duty_event()
        html = event.body_html
        match = re.search(r"<!--\s*CREW_DATA:(.*?)-->", html, re.DOTALL)
        assert match, "CREW_DATA comment not found"
        data = json.loads(match.group(1))
        assert data["version"] == 1

    def test_crew_data_has_correct_structure(self):
        """JSON has all expected fields with correct values."""
        event = _make_duty_event()
        data = event._crew_data_json
        assert data["shift_change_hour"] == 18
        assert data["from_platoon"] == "B Platoon"
        assert data["until_platoon"] == "A Platoon"
        assert "Station 31" in data["from_crew"]
        assert "Fireboat 31 Standby" in data["from_crew"]
        assert "Station 31" in data["until_crew"]

    def test_crew_data_positions_are_cleaned(self):
        """Positions in JSON use cleaned form (no colons)."""
        event = _make_duty_event()
        data = event._crew_data_json
        fireboat_crew = data["from_crew"]["Fireboat 31 Standby"]
        assert fireboat_crew[0]["position"] == "Marine Pilot"

    def test_crew_data_from_crew_members(self):
        """JSON from_crew has correct member count and names."""
        event = _make_duty_event()
        data = event._crew_data_json
        s31 = data["from_crew"]["Station 31"]
        names = [m["name"] for m in s31]
        assert len(s31) == 3
        assert "Charlie Brown" in names
        assert "Dana White" in names
        assert "Eve Davis" in names

    def test_crew_data_until_crew_excluded_from_from(self):
        """Until crew does not appear in from_crew."""
        event = _make_duty_event()
        data = event._crew_data_json
        all_from_names = [
            m["name"] for members in data["from_crew"].values() for m in members
        ]
        assert "Alice Smith" not in all_from_names
        assert "Bob Jones" not in all_from_names


# ---------------------------------------------------------------------------
# JSON extraction (fast path)
# ---------------------------------------------------------------------------


class TestParseCrewDataJson:
    """Test _parse_crew_data_json extracts structured data from HTML comment."""

    def test_extracts_from_body_html(self):
        """Extracts crew from a body_html with CREW_DATA comment."""
        event = _make_duty_event()
        result = _parse_crew_data_json(event.body_html)
        assert result is not None
        entries, platoon = result
        assert platoon == "B Platoon"
        assert len(entries) == 4

    def test_returns_none_for_html_without_comment(self):
        """Returns None when no CREW_DATA comment is present."""
        result = _parse_crew_data_json("<h3>From 1800 (B Platoon)</h3><table></table>")
        assert result is None

    def test_returns_none_for_invalid_json(self):
        """Returns None (not crash) when JSON is malformed."""
        html = "<!-- CREW_DATA:{invalid json} -->"
        result = _parse_crew_data_json(html)
        assert result is None

    def test_returns_none_for_empty_html(self):
        """Returns None for empty string."""
        result = _parse_crew_data_json("")
        assert result is None


# ---------------------------------------------------------------------------
# Round-trip: body_html → parse_duty_event_html (JSON path)
# ---------------------------------------------------------------------------


class TestParseDutyEventHtml:
    """Test parse_duty_event_html with JSON-embedded events (current format)."""

    def test_round_trip_extracts_from_crew(self):
        """HTML generated by AllDayDutyEvent.body_html parses back correctly."""
        event = _make_duty_event()
        html = event.body_html
        entries, platoon = parse_duty_event_html(html, event.event_date)

        assert platoon == "B Platoon"
        # Should get 4 entries from the "From" section (3 Station 31 + 1 Fireboat)
        assert len(entries) == 4

        names = [e.name for e in entries]
        assert "Charlie Brown" in names
        assert "Dana White" in names
        assert "Eve Davis" in names
        assert "Frank Miller" in names

        # "Until" section crew should NOT appear
        assert "Alice Smith" not in names
        assert "Bob Jones" not in names

    def test_sections_preserved(self):
        """Section headers (Station 31, Fireboat) are captured."""
        event = _make_duty_event()
        html = event.body_html
        entries, _ = parse_duty_event_html(html, event.event_date)

        sections = {e.section for e in entries}
        assert "Station 31" in sections
        assert "Fireboat 31 Standby" in sections

    def test_positions_preserved(self):
        """Position names come through correctly."""
        event = _make_duty_event()
        html = event.body_html
        entries, _ = parse_duty_event_html(html, event.event_date)

        by_name = {e.name: e for e in entries}
        assert by_name["Charlie Brown"].position == "Captain"
        assert by_name["Dana White"].position == "Apparatus Operator"
        assert by_name["Eve Davis"].position == "Firefighter"
        assert by_name["Frank Miller"].position == "Marine Pilot"

    def test_shift_change_hour_in_time_fields(self):
        """Entries have start_time == end_time == shift change hour."""
        event = _make_duty_event(shift_hour=18)
        html = event.body_html
        entries, _ = parse_duty_event_html(html, event.event_date)

        for entry in entries:
            assert entry.start_time == "18:00"
            assert entry.end_time == "18:00"

    def test_empty_html_returns_empty(self):
        """Empty or missing HTML returns no entries."""
        entries, platoon = parse_duty_event_html("", date(2026, 2, 17))
        assert entries == []
        assert platoon == ""


# ---------------------------------------------------------------------------
# Legacy HTML table fallback (pre-JSON events)
# ---------------------------------------------------------------------------


class TestLegacyHtmlFallback:
    """Test parse_duty_event_html falls back to HTML parsing for old events."""

    def test_legacy_html_round_trip(self):
        """Events without CREW_DATA comment still parse via HTML tables."""
        event = _make_duty_event()
        html = _strip_crew_data_comment(event.body_html)
        # Verify comment is actually stripped
        assert "CREW_DATA" not in html

        entries, platoon = parse_duty_event_html(html, event.event_date)
        assert platoon == "B Platoon"
        assert len(entries) == 4
        names = [e.name for e in entries]
        assert "Charlie Brown" in names
        assert "Frank Miller" in names

    def test_legacy_no_from_section_returns_empty(self):
        """HTML with only an Until section returns no entries."""
        html = (
            "<h3>Until 1800 (A Platoon)</h3><table><tr><td>Alice</td><td>Captain</td></tr></table>"
        )
        entries, platoon = parse_duty_event_html(html, date(2026, 2, 17))
        assert entries == []
        assert platoon == ""

    def test_legacy_platoon_without_parens(self):
        """Section header without platoon in parentheses works."""
        html = """
        <h3>From 1800</h3>
        <table>
            <tr><td colspan="3">Station 31</td></tr>
            <tr><td>Alice</td><td>Captain</td><td></td></tr>
        </table>
        """
        entries, platoon = parse_duty_event_html(html, date(2026, 2, 17))
        assert len(entries) == 1
        assert platoon == ""
        assert entries[0].name == "Alice"

    def test_legacy_positions_preserved(self):
        """Legacy path preserves positions from HTML tables."""
        event = _make_duty_event()
        html = _strip_crew_data_comment(event.body_html)
        entries, _ = _parse_duty_event_html_tables(html)

        by_name = {e.name: e for e in entries}
        assert by_name["Charlie Brown"].position == "Captain"
        assert by_name["Dana White"].position == "Apparatus Operator"
        assert by_name["Frank Miller"].position == "Marine Pilot"

    def test_legacy_sections_preserved(self):
        """Legacy path preserves section headers."""
        event = _make_duty_event()
        html = _strip_crew_data_comment(event.body_html)
        entries, _ = _parse_duty_event_html_tables(html)

        sections = {e.section for e in entries}
        assert "Station 31" in sections
        assert "Fireboat 31 Standby" in sections


# ---------------------------------------------------------------------------
# JSON and HTML produce identical results
# ---------------------------------------------------------------------------


class TestJsonHtmlParity:
    """Verify JSON extraction and HTML parsing return the same data."""

    def test_json_and_html_paths_match(self):
        """Both paths produce identical entries for the same event."""
        event = _make_duty_event()
        html = event.body_html

        json_entries, json_platoon = _parse_crew_data_json(html)
        html_entries, html_platoon = _parse_duty_event_html_tables(html)

        assert json_platoon == html_platoon

        json_set = {(e.name, e.position, e.section) for e in json_entries}
        html_set = {(e.name, e.position, e.section) for e in html_entries}
        assert json_set == html_set

    def test_json_and_html_shift_times_match(self):
        """Both paths set the same shift change time."""
        event = _make_duty_event(shift_hour=7)
        html = event.body_html

        json_entries, _ = _parse_crew_data_json(html)
        html_entries, _ = _parse_duty_event_html_tables(html)

        for e in json_entries:
            assert e.start_time == "07:00"
            assert e.end_time == "07:00"
        for e in html_entries:
            assert e.start_time == "07:00"
            assert e.end_time == "07:00"


# ---------------------------------------------------------------------------
# schedule_refresh task
# ---------------------------------------------------------------------------


class TestScheduleRefreshTask:
    """Test the schedule_refresh task function."""

    @pytest.mark.asyncio
    async def test_upserts_parsed_events_to_store(self):
        """Task fetches events, parses them, and upserts to Cosmos."""
        event = _make_duty_event()
        html = event.body_html

        mock_events = {date(2026, 2, 17): html}

        upserted: list = []

        async def fake_upsert(doc):
            upserted.append(doc)

        mock_store = AsyncMock()
        mock_store.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store.__aexit__ = AsyncMock(return_value=None)
        mock_store.upsert = fake_upsert

        with (
            patch(
                "sjifire.ops.schedule.tools._fetch_group_calendar_events",
                new_callable=AsyncMock,
                return_value=mock_events,
            ),
            patch(
                "sjifire.ops.schedule.store.ScheduleStore",
                return_value=mock_store,
            ),
        ):
            count = await schedule_refresh()

        assert count == 1
        assert len(upserted) == 1
        doc = upserted[0]
        assert doc.date == "2026-02-17"
        assert doc.platoon == "B Platoon"
        assert len(doc.entries) == 4

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_events(self):
        """Task returns 0 when no calendar events are found."""
        with patch(
            "sjifire.ops.schedule.tools._fetch_group_calendar_events",
            new_callable=AsyncMock,
            return_value={},
        ):
            count = await schedule_refresh()

        assert count == 0


# ---------------------------------------------------------------------------
# fetch_schedule_from_outlook
# ---------------------------------------------------------------------------


class TestFetchScheduleFromOutlook:
    """Test the shared fetch_schedule_from_outlook function."""

    @pytest.mark.asyncio
    async def test_returns_day_schedule_cache_docs(self):
        """Converts calendar events into DayScheduleCache documents."""
        event = _make_duty_event()
        mock_events = {date(2026, 2, 17): event.body_html}

        with patch(
            "sjifire.ops.schedule.tools._fetch_group_calendar_events",
            new_callable=AsyncMock,
            return_value=mock_events,
        ):
            results = await fetch_schedule_from_outlook(date(2026, 2, 17), date(2026, 2, 17))

        assert len(results) == 1
        doc = results[0]
        assert doc.date == "2026-02-17"
        assert doc.platoon == "B Platoon"
        assert len(doc.entries) == 4

    @pytest.mark.asyncio
    async def test_skips_dates_with_no_parseable_crew(self):
        """Dates where HTML has no From section are skipped."""
        mock_events = {
            date(2026, 2, 17): "<p>No schedule data</p>",
        }

        with patch(
            "sjifire.ops.schedule.tools._fetch_group_calendar_events",
            new_callable=AsyncMock,
            return_value=mock_events,
        ):
            results = await fetch_schedule_from_outlook(date(2026, 2, 17), date(2026, 2, 17))

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_events(self):
        """Empty calendar returns empty list."""
        with patch(
            "sjifire.ops.schedule.tools._fetch_group_calendar_events",
            new_callable=AsyncMock,
            return_value={},
        ):
            results = await fetch_schedule_from_outlook(date(2026, 2, 17), date(2026, 2, 17))

        assert results == []


# ---------------------------------------------------------------------------
# _ensure_cache fallback
# ---------------------------------------------------------------------------


class TestEnsureCacheFallback:
    """Test that _ensure_cache falls back to Outlook, not Aladtec."""

    @pytest.mark.asyncio
    async def test_stale_cache_triggers_outlook_fallback(self):
        """When cache is stale, _ensure_cache calls fetch_schedule_from_outlook."""
        from sjifire.ops.schedule.tools import _ensure_cache

        event = _make_duty_event()
        mock_events = {date(2026, 2, 17): event.body_html}

        # Mock store returns empty (cache miss)
        mock_store = AsyncMock()
        mock_store.get_range = AsyncMock(return_value={})
        mock_store.upsert = AsyncMock()

        with patch(
            "sjifire.ops.schedule.tools._fetch_group_calendar_events",
            new_callable=AsyncMock,
            return_value=mock_events,
        ) as mock_fetch:
            result = await _ensure_cache(mock_store, ["2026-02-17"])

        # Verify Outlook was called
        mock_fetch.assert_awaited_once()
        # Verify data was cached
        mock_store.upsert.assert_awaited()
        assert "2026-02-17" in result
        assert result["2026-02-17"].platoon == "B Platoon"

    @pytest.mark.asyncio
    async def test_fresh_cache_does_not_call_outlook(self):
        """When cache is fresh, _ensure_cache does NOT call Outlook."""
        from sjifire.ops.schedule.models import DayScheduleCache, ScheduleEntryCache
        from sjifire.ops.schedule.tools import _ensure_cache

        fresh_day = DayScheduleCache(
            id="2026-02-17",
            date="2026-02-17",
            platoon="C Platoon",
            entries=[
                ScheduleEntryCache(
                    name="Test User",
                    position="Firefighter",
                    section="Station 31",
                    start_time="18:00",
                    end_time="18:00",
                )
            ],
        )

        mock_store = AsyncMock()
        mock_store.get_range = AsyncMock(return_value={"2026-02-17": fresh_day})

        with patch(
            "sjifire.ops.schedule.tools._fetch_group_calendar_events",
            new_callable=AsyncMock,
        ) as mock_fetch:
            result = await _ensure_cache(mock_store, ["2026-02-17"])

        # Outlook should NOT be called — cache is fresh
        mock_fetch.assert_not_awaited()
        assert result["2026-02-17"].platoon == "C Platoon"


# ---------------------------------------------------------------------------
# Guard: no Aladtec scraper imports in schedule tools
# ---------------------------------------------------------------------------


class TestNoAladtecInScheduleTools:
    """Guard: schedule tools must not import the Aladtec scraper."""

    def test_no_aladtec_import_in_tools(self):
        """tools.py must not import or call the Aladtec schedule scraper."""
        import ast
        import inspect

        from sjifire.ops.schedule import tools

        source = inspect.getsource(tools)
        tree = ast.parse(source)

        # Check all import statements for aladtec references
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "aladtec" not in alias.name.lower(), (
                        f"schedule/tools.py imports '{alias.name}' — "
                        "use fetch_schedule_from_outlook() instead"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                # Allow importing CREW_DATA_MARKER from calendar.models
                # (which happens to import from aladtec internally)
                if "calendar.models" in node.module:
                    continue
                assert "aladtec" not in node.module.lower(), (
                    f"schedule/tools.py imports from '{node.module}' — "
                    "use fetch_schedule_from_outlook() instead"
                )
