"""Tests for kiosk test data generator."""

from unittest.mock import patch

from sjifire.ops.kiosk.test_data import CYCLE_SECONDS, get_test_kiosk_data


def _at(elapsed: float):
    """Patch time.time so the test data generator sees ``elapsed`` seconds into the cycle.

    Uses a base time that's a clean multiple of CYCLE_SECONDS so that
    ``time.time() % CYCLE_SECONDS == elapsed``.
    """
    mock_time = CYCLE_SECONDS * 1000 + elapsed
    return patch("sjifire.ops.kiosk.test_data.time.time", return_value=mock_time)


class TestKioskTestData:
    def test_returns_expected_shape(self):
        data = get_test_kiosk_data()
        assert "timestamp" in data
        assert "calls" in data
        assert "crew" in data
        assert "sections" in data
        assert "platoon" in data
        assert "upcoming_crew" in data
        assert "upcoming_sections" in data
        assert "upcoming_platoon" in data
        assert isinstance(data["calls"], list)
        assert isinstance(data["crew"], list)
        assert len(data["crew"]) > 0
        assert len(data["upcoming_crew"]) > 0

    def test_sections_include_chief_officer(self):
        """Sections should include Chief Officer for highlighting."""
        data = get_test_kiosk_data()
        section_keys = [s["key"] for s in data["sections"]]
        assert "Chief Officer" in section_keys
        chief_section = next(s for s in data["sections"] if s["key"] == "Chief Officer")
        assert any(c["position"] == "Captain" for c in chief_section["members"])

    def test_upcoming_crew_different_from_current(self):
        """Upcoming crew should be a different platoon."""
        data = get_test_kiosk_data()
        assert data["upcoming_platoon"] != data["platoon"]
        current_names = {c["name"] for c in data["crew"]}
        upcoming_names = {c["name"] for c in data["upcoming_crew"]}
        assert current_names != upcoming_names

    def test_initial_state_is_idle(self):
        """At T+1, there should be no active calls (3s idle period)."""
        with _at(1):
            data = get_test_kiosk_data()
        assert len(data["calls"]) == 0

    def test_incoming_call_has_no_nature_or_address(self):
        """At T+4, call appears with dispatch ID but no nature or address."""
        with _at(4):
            data = get_test_kiosk_data()

        assert len(data["calls"]) == 1
        call = data["calls"][0]
        assert call["dispatch_id"] == "26-001999"
        assert call["nature"] == ""
        assert call["address"] == ""
        assert call["latitude"] is None

    def test_address_and_geo_arrive_together(self):
        """At T+13, address and geo should both be populated (map loads immediately)."""
        with _at(13):
            data = get_test_kiosk_data()

        call = data["calls"][0]
        assert call["address"] == "589 Old Farm Road"
        assert call["latitude"] == 48.46401
        assert call["longitude"] == -123.03788
        assert call["nature"] == ""  # Nature at T+18

    def test_nature_arrives_after_address(self):
        """At T+19, both address and nature should be populated."""
        with _at(19):
            data = get_test_kiosk_data()

        call = data["calls"][0]
        assert call["address"] == "589 Old Farm Road"
        assert call["nature"] == "Fire-Structure"
        assert call["latitude"] == 48.46401

    def test_real_equipment_names(self):
        """At T+45, responders should use real SJI Fire equipment names."""
        with _at(45):
            data = get_test_kiosk_data()

        call = data["calls"][0]
        units = {r["unit_call_sign"] for r in call["responder_details"]}
        assert "SJF3" in units
        assert "BN31" in units
        assert "E31" in units
        assert "OPS31" in units
        assert "W31" not in units

    def test_single_call_only(self):
        """There should only ever be one call in the scenario."""
        with _at(100):
            data = get_test_kiosk_data()

        assert len(data["calls"]) == 1
        assert data["calls"][0]["nature"] == "Fire-Structure"

    def test_calls_clear_eventually(self):
        """At T+175, call should be cleared."""
        with _at(175):
            data = get_test_kiosk_data()

        assert len(data["calls"]) == 0

    def test_cycle_restarts(self):
        """At T+1, should be in idle state (before T+5)."""
        with _at(1):
            data = get_test_kiosk_data()

        assert len(data["calls"]) == 0

    def test_responders_progress(self):
        """At T+45, SJF3 should be paged and E31 enroute."""
        with _at(45):
            data = get_test_kiosk_data()

        call = data["calls"][0]
        statuses = [(r["unit_call_sign"], r["status"]) for r in call["responder_details"]]
        assert ("SJF3", "Paged") in statuses
        assert ("E31", "Enroute") in statuses

    def test_dispatch_id_present(self):
        """At T+10 (active call), dispatch_id should be present."""
        with _at(10):
            data = get_test_kiosk_data()
        for call in data["calls"]:
            assert "dispatch_id" in call
            assert call["dispatch_id"]

    def test_severity_and_icon_present(self):
        """At T+10 (active call), severity and icon should be present."""
        with _at(10):
            data = get_test_kiosk_data()
        for call in data["calls"]:
            assert call["severity"] in ("high", "medium", "low")
            assert call["icon"]

    def test_command_established(self):
        """At T+70, BN31 should be incident commander."""
        with _at(70):
            data = get_test_kiosk_data()

        call = data["calls"][0]
        assert call["analysis"]["incident_commander"] == "BN31"

    def test_overhaul_complete(self):
        """At T+140, overhaul should be complete with units returning."""
        with _at(140):
            data = get_test_kiosk_data()

        call = data["calls"][0]
        statuses = [(r["unit_call_sign"], r["status"]) for r in call["responder_details"]]
        assert ("BN31", "Returning") in statuses
        assert ("E31", "Returning") in statuses
        assert "command terminated" in call["cad_comments"]
