"""Tests for the kiosk fixture-replay data generator."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import sjifire.ops.kiosk.replay_data as mod
from sjifire.ops.kiosk.replay_data import (
    CYCLE_SECONDS,
    _build_calls,
    _build_crew,
    _build_sections,
    _build_upcoming_crew,
    _cycle_base,
    _fire_alarm_call,
    _ts,
    get_replay_kiosk_data,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset module-level fixture caches between tests."""
    mod._fixture_detail = None
    mod._fixture_events = None
    yield
    mod._fixture_detail = None
    mod._fixture_events = None


def _at(elapsed: float):
    """Patch time.time so the replay generator sees ``elapsed`` seconds into the cycle."""
    mock_time = CYCLE_SECONDS * 1000 + elapsed
    return patch("sjifire.ops.kiosk.replay_data.time.time", return_value=mock_time)


# ---------------------------------------------------------------------------
# _build_calls — boundary tests
# ---------------------------------------------------------------------------


class TestBuildCalls:
    def test_before_call_appears(self):
        assert _build_calls(3) == []

    def test_at_zero(self):
        assert _build_calls(0) == []

    def test_call_active_at_midpoint(self):
        result = _build_calls(50)
        assert len(result) == 1

    def test_call_active_at_boundary_start(self):
        result = _build_calls(5)
        assert len(result) == 1

    def test_call_clears_at_100(self):
        assert _build_calls(100) == []

    def test_call_active_at_99(self):
        result = _build_calls(99)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _fire_alarm_call — progressive timeline
# ---------------------------------------------------------------------------


class TestFireAlarmCall:
    def test_initial_state_has_address_and_geo(self):
        call = _fire_alarm_call(5)
        assert call["dispatch_id"] == "26-002358"
        assert call["address"] != ""
        assert call["latitude"] is not None
        assert call["longitude"] is not None
        assert call["nature"] == ""

    def test_nature_arrives_at_t8(self):
        call = _fire_alarm_call(8)
        assert call["nature"] == "Fire-Alarm"
        assert call["type"] == "FIRE"
        assert call["zone_code"] == "SJF"
        assert call["severity"] == "high"

    def test_no_nature_before_t8(self):
        call = _fire_alarm_call(7)
        assert call["nature"] == ""
        assert call["severity"] == "low"

    def test_cad_comments_at_t10(self):
        call = _fire_alarm_call(10)
        assert call["cad_comments"] != ""

    def test_no_cad_comments_before_t10(self):
        call = _fire_alarm_call(9)
        assert call["cad_comments"] == ""

    def test_responders_at_t12(self):
        call = _fire_alarm_call(12)
        assert len(call["responder_details"]) >= 1
        first = call["responder_details"][0]
        assert first["unit_call_sign"] == "SJF3"
        assert first["status"] == "PAGED"

    def test_no_responders_before_t12(self):
        call = _fire_alarm_call(11)
        assert len(call["responder_details"]) == 0

    def test_multiple_units_at_t20(self):
        call = _fire_alarm_call(20)
        units = {r["unit_call_sign"] for r in call["responder_details"]}
        assert "BN31" in units
        assert "E31" in units
        assert "M12" in units

    def test_responding_units_csv(self):
        call = _fire_alarm_call(20)
        assert "SJF3" in call["responding_units"]
        assert "BN31" in call["responding_units"]

    def test_incident_commander_at_t55(self):
        call = _fire_alarm_call(55)
        assert call["analysis"]["incident_commander"] == "BN31"

    def test_no_incident_commander_before_t55(self):
        call = _fire_alarm_call(54)
        assert call["analysis"]["incident_commander"] == ""

    def test_alarm_time_at_t12(self):
        call = _fire_alarm_call(12)
        assert call["analysis"]["alarm_time"] != ""

    def test_first_enroute_at_t16(self):
        call = _fire_alarm_call(16)
        assert call["analysis"]["first_enroute"] != ""

    def test_responder_note_has_radio_log(self):
        call = _fire_alarm_call(40)
        notes = [r for r in call["responder_details"] if r["status"] == "NOTE"]
        assert len(notes) > 0
        assert any("radio_log" in n for n in notes)

    def test_all_events_at_t92(self):
        call = _fire_alarm_call(92)
        assert len(call["responder_details"]) == 41

    def test_call_structure(self):
        call = _fire_alarm_call(50)
        expected_keys = {
            "dispatch_id",
            "long_term_call_id",
            "nature",
            "address",
            "city",
            "state",
            "zip_code",
            "agency_code",
            "type",
            "zone_code",
            "time_reported",
            "is_completed",
            "cad_comments",
            "responding_units",
            "responder_details",
            "geo_location",
            "latitude",
            "longitude",
            "severity",
            "icon",
            "site_history",
            "analysis",
        }
        assert expected_keys.issubset(call.keys())

    def test_analysis_structure(self):
        call = _fire_alarm_call(50)
        expected_keys = {
            "incident_commander",
            "incident_commander_name",
            "alarm_time",
            "first_enroute",
            "unit_times",
            "on_duty_crew",
            "summary",
            "actions_taken",
            "patient_count",
            "escalated",
            "outcome",
            "short_dsc",
            "key_events",
        }
        assert expected_keys.issubset(call["analysis"].keys())


# ---------------------------------------------------------------------------
# _build_crew / _build_upcoming_crew
# ---------------------------------------------------------------------------


class TestBuildCrew:
    def test_crew_count(self):
        assert len(_build_crew()) == 6

    def test_crew_has_expected_positions(self):
        positions = {c["position"] for c in _build_crew()}
        assert "Captain" in positions
        assert "Lieutenant" in positions
        assert "Firefighter" in positions
        assert "AO" in positions
        assert "EMT" in positions

    def test_crew_member_structure(self):
        for member in _build_crew():
            assert "name" in member
            assert "position" in member
            assert "section" in member
            assert "_sort_key" in member
            assert "shift" in member

    def test_upcoming_crew_count(self):
        assert len(_build_upcoming_crew()) == 6

    def test_upcoming_crew_different_names(self):
        current = {c["name"] for c in _build_crew()}
        upcoming = {c["name"] for c in _build_upcoming_crew()}
        assert current != upcoming


# ---------------------------------------------------------------------------
# _build_sections
# ---------------------------------------------------------------------------


class TestBuildSections:
    def test_groups_by_section(self):
        sections = _build_sections(_build_crew())
        keys = [s["key"] for s in sections]
        assert "Chief Officer" in keys
        assert "Operations" in keys
        assert "Volunteers" in keys

    def test_sorted_by_sort_key(self):
        sections = _build_sections(_build_crew())
        keys = [s["key"] for s in sections]
        assert keys.index("Chief Officer") < keys.index("Operations")
        assert keys.index("Operations") < keys.index("Volunteers")

    def test_section_structure(self):
        for section in _build_sections(_build_crew()):
            assert "key" in section
            assert "label" in section
            assert "members" in section
            assert section["key"] == section["label"]
            assert len(section["members"]) > 0

    def test_chief_officer_has_captain(self):
        sections = _build_sections(_build_crew())
        chief = next(s for s in sections if s["key"] == "Chief Officer")
        positions = {m["position"] for m in chief["members"]}
        assert "Captain" in positions

    def test_all_members_accounted_for(self):
        crew = _build_crew()
        sections = _build_sections(crew)
        total = sum(len(s["members"]) for s in sections)
        assert total == len(crew)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


class TestTimestampHelpers:
    def test_cycle_base_at_t5(self):
        now = datetime.now(UTC)
        base = _cycle_base(5)
        assert abs((now - base).total_seconds()) < 1

    def test_cycle_base_at_t0(self):
        now = datetime.now(UTC)
        base = _cycle_base(0)
        assert abs((now - base).total_seconds()) < 1

    def test_cycle_base_at_t50(self):
        now = datetime.now(UTC)
        base = _cycle_base(50)
        offset = (now - base).total_seconds()
        assert 44 < offset < 46

    def test_ts_returns_iso_format(self):
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        result = _ts(base, 60)
        expected = (base + timedelta(seconds=60)).isoformat()
        assert result == expected

    def test_ts_zero_offset(self):
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        assert _ts(base, 0) == base.isoformat()


# ---------------------------------------------------------------------------
# get_replay_kiosk_data — full integration
# ---------------------------------------------------------------------------


class TestGetReplayKioskData:
    def test_returns_expected_shape(self):
        data = get_replay_kiosk_data()
        assert "timestamp" in data
        assert "calls" in data
        assert "crew" in data
        assert "sections" in data
        assert "platoon" in data
        assert "upcoming_crew" in data
        assert "upcoming_sections" in data
        assert "upcoming_platoon" in data

    def test_platoon_values(self):
        data = get_replay_kiosk_data()
        assert data["platoon"] == "B Platoon"
        assert data["upcoming_platoon"] == "A Platoon"

    def test_timestamp_is_iso(self):
        data = get_replay_kiosk_data()
        datetime.fromisoformat(data["timestamp"])

    def test_idle_state_at_t1(self):
        with _at(1):
            data = get_replay_kiosk_data()
        assert len(data["calls"]) == 0

    def test_active_call_at_t50(self):
        with _at(50):
            data = get_replay_kiosk_data()
        assert len(data["calls"]) == 1
        assert data["calls"][0]["nature"] == "Fire-Alarm"

    def test_call_cleared_at_t100(self):
        with _at(100):
            data = get_replay_kiosk_data()
        assert len(data["calls"]) == 0

    def test_crew_always_present(self):
        with _at(0):
            data = get_replay_kiosk_data()
        assert len(data["crew"]) == 6
        assert len(data["upcoming_crew"]) == 6


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


class TestFixtureLoading:
    def test_fixture_detail_loads(self):
        from sjifire.ops.kiosk.replay_data import _load_fixture_detail

        detail = _load_fixture_detail()
        assert detail["LongTermCallID"] == "26-002358"
        assert detail["Nature"] == "Fire-Alarm"

    def test_fixture_detail_cached(self):
        from sjifire.ops.kiosk.replay_data import _load_fixture_detail

        d1 = _load_fixture_detail()
        d2 = _load_fixture_detail()
        assert d1 is d2

    def test_fixture_events_loads(self):
        from sjifire.ops.kiosk.replay_data import _load_fixture_events

        events = _load_fixture_events()
        assert len(events) == 41

    def test_fixture_events_chronological(self):
        from sjifire.ops.kiosk.replay_data import _load_fixture_detail, _load_fixture_events

        detail = _load_fixture_detail()
        raw = detail["JoinedRespondersDetail"]
        events = _load_fixture_events()
        assert events[0] == raw[-1]
        assert events[-1] == raw[0]
