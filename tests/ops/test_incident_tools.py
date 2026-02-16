"""Tests for incident tools with access control."""

import os
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.incidents.models import CrewAssignment, IncidentDocument, Narratives
from sjifire.ops.incidents.tools import (
    _address_from_neris_location,
    _check_edit_access,
    _check_view_access,
    _extract_timestamps,
    _prefill_from_dispatch,
    _prefill_from_neris,
    create_incident,
    get_incident,
    get_neris_incident,
    import_from_neris,
    list_incidents,
    list_neris_incidents,
    reset_incident,
    submit_incident,
    update_incident,
)


# Fixtures
@pytest.fixture(autouse=True)
def _officer_group_env():
    """Set the officer group ID for all tests."""
    with patch.dict(os.environ, {"ENTRA_MCP_OFFICER_GROUP_ID": "officer-group"}):
        yield


@pytest.fixture
def regular_user():
    user = UserContext(
        email="ff@sjifire.org", name="Firefighter", user_id="user-1", groups=frozenset()
    )
    set_current_user(user)
    return user


@pytest.fixture
def officer_user():
    user = UserContext(
        email="chief@sjifire.org",
        name="Chief",
        user_id="user-2",
        groups=frozenset(["officer-group"]),
    )
    set_current_user(user)
    return user


@pytest.fixture
def sample_doc():
    return IncidentDocument(
        id="doc-123",
        station="S31",
        incident_number="26-000944",
        incident_date=date(2026, 2, 12),
        created_by="ff@sjifire.org",
        crew=[
            CrewAssignment(name="Crew 1", email="crew1@sjifire.org", position="FF", unit="E31"),
        ],
    )


# Access control tests
class TestViewAccess:
    def test_creator_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "ff@sjifire.org", is_officer=False)

    def test_crew_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "crew1@sjifire.org", is_officer=False)

    def test_officer_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "random@sjifire.org", is_officer=True)

    def test_stranger_cannot_view(self, sample_doc):
        assert not _check_view_access(sample_doc, "stranger@sjifire.org", is_officer=False)


class TestEditAccess:
    def test_creator_can_edit(self, sample_doc):
        assert _check_edit_access(sample_doc, "ff@sjifire.org", is_officer=False)

    def test_officer_can_edit(self, sample_doc):
        assert _check_edit_access(sample_doc, "random@sjifire.org", is_officer=True)

    def test_crew_cannot_edit(self, sample_doc):
        assert not _check_edit_access(sample_doc, "crew1@sjifire.org", is_officer=False)

    def test_stranger_cannot_edit(self, sample_doc):
        assert not _check_edit_access(sample_doc, "stranger@sjifire.org", is_officer=False)


# Tool tests with mocked store
class TestCreateIncident:
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_creates_draft(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await create_incident(
            incident_number="26-000944",
            incident_date="2026-02-12",
            station="S31",
            crew=[{"name": "John", "email": "john@sjifire.org", "position": "FF", "unit": "E31"}],
        )

        assert result["station"] == "S31"
        assert result["year"] == "2026"
        assert result["incident_number"] == "26-000944"
        assert result["status"] == "draft"
        assert result["created_by"] == "ff@sjifire.org"
        assert len(result["crew"]) == 1

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_rejects_duplicate_number(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await create_incident(
            incident_number="26-000944",
            incident_date="2026-02-12",
            station="S31",
        )

        assert "error" in result
        assert "already exists" in result["error"]
        assert result["existing_id"] == sample_doc.id


class TestGetIncident:
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_creator_gets_own(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_incident("doc-123")
        assert result["incident_number"] == "26-000944"

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_stranger_denied(self, mock_store_cls, sample_doc):
        stranger = UserContext(email="stranger@sjifire.org", name="X", user_id="x")
        set_current_user(stranger)

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_incident("doc-123")
        assert "error" in result
        assert "access" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_not_found(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_incident("nonexistent")
        assert "error" in result


class TestListIncidents:
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_regular_user_sees_own(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.list_for_user = AsyncMock(return_value=[sample_doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await list_incidents()
        assert result["count"] == 1
        mock_store.list_for_user.assert_called_once_with(
            "ff@sjifire.org", status=None, exclude_status="submitted"
        )

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_officer_sees_all(self, mock_store_cls, officer_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.list_by_status = AsyncMock(return_value=[sample_doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await list_incidents()
        assert result["count"] == 1
        mock_store.list_by_status.assert_called_once_with(
            None, station=None, exclude_status="submitted"
        )

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_explicit_status_no_exclusion(self, mock_store_cls, officer_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.list_by_status = AsyncMock(return_value=[sample_doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await list_incidents(status="submitted")
        assert result["count"] == 1
        mock_store.list_by_status.assert_called_once_with(
            "submitted", station=None, exclude_status=None
        )


class TestUpdateIncident:
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_creator_can_update(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident("doc-123", address="200 Spring St")
        assert result["address"] == "200 Spring St"

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_crew_cannot_update(self, mock_store_cls, sample_doc):
        crew_user = UserContext(email="crew1@sjifire.org", name="Crew", user_id="c1")
        set_current_user(crew_user)

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident("doc-123", address="Hacked")
        assert "error" in result
        assert "permission" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_cannot_update_submitted(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "submitted"
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident("doc-123", address="Too late")
        assert "error" in result
        assert "submitted" in result["error"].lower()


class TestSubmitIncident:
    async def test_regular_user_cannot_submit(self, regular_user):
        result = await submit_incident("doc-123")
        assert "error" in result
        assert "officer" in result["error"].lower()

    async def test_officer_gets_not_available(self, officer_user):
        result = await submit_incident("doc-123")
        assert result["status"] == "not_available"
        assert "not yet enabled" in result["message"]
        assert result["incident_id"] == "doc-123"


class TestListNerisIncidents:
    @patch("sjifire.ops.incidents.tools._list_neris_incidents")
    async def test_officer_can_list(self, mock_list, officer_user):
        mock_list.return_value = {
            "incidents": [
                {
                    "neris_id": "FD53055879|26SJ0001|123",
                    "incident_number": "26SJ0001",
                    "call_create": "2026-01-15T10:30:00",
                    "status": "APPROVED",
                    "incident_type": "111",
                }
            ],
            "count": 1,
        }

        result = await list_neris_incidents()
        assert result["count"] == 1
        assert result["incidents"][0]["incident_number"] == "26SJ0001"
        mock_list.assert_called_once()

    async def test_regular_user_denied(self, regular_user):
        result = await list_neris_incidents()
        assert "error" in result
        assert "officer" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._list_neris_incidents")
    async def test_handles_api_error(self, mock_list, officer_user):
        mock_list.side_effect = RuntimeError("Connection failed")

        result = await list_neris_incidents()
        assert "error" in result
        assert "Connection failed" in result["error"]


class TestGetNerisIncident:
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_officer_can_get(self, mock_get, officer_user):
        mock_get.return_value = {
            "neris_id": "FD53055879|26SJ0001|123",
            "dispatch": {"incident_number": "26SJ0001"},
            "incident_types": [{"type": "111"}],
        }

        result = await get_neris_incident("FD53055879|26SJ0001|123")
        assert result["neris_id"] == "FD53055879|26SJ0001|123"
        mock_get.assert_called_once_with("FD53055879|26SJ0001|123")

    async def test_regular_user_denied(self, regular_user):
        result = await get_neris_incident("FD53055879|26SJ0001|123")
        assert "error" in result
        assert "officer" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_not_found(self, mock_get, officer_user):
        mock_get.return_value = None

        result = await get_neris_incident("FD53055879|BOGUS|999")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_handles_api_error(self, mock_get, officer_user):
        mock_get.side_effect = RuntimeError("Connection failed")

        result = await get_neris_incident("FD53055879|26SJ0001|123")
        assert "error" in result
        assert "Connection failed" in result["error"]


# Pre-fill tests
class TestExtractTimestamps:
    def test_extracts_dispatch_enroute_onscene(self):
        details = [
            {"status": "Dispatched", "time_of_status_change": "2026-02-12T14:30:15"},
            {"status": "Enroute", "time_of_status_change": "2026-02-12T14:32:00"},
            {"status": "On Scene", "time_of_status_change": "2026-02-12T14:40:00"},
        ]
        result = _extract_timestamps(details)
        assert result["first_unit_dispatched"] == "2026-02-12T14:30:15"
        assert result["first_unit_enroute"] == "2026-02-12T14:32:00"
        assert result["first_unit_arrived"] == "2026-02-12T14:40:00"

    def test_uses_first_of_each_status(self):
        details = [
            {"status": "Dispatched", "time_of_status_change": "2026-02-12T14:30:15"},
            {"status": "Dispatched", "time_of_status_change": "2026-02-12T14:31:00"},
            {"status": "Enroute", "time_of_status_change": "2026-02-12T14:32:00"},
            {"status": "Enroute", "time_of_status_change": "2026-02-12T14:33:00"},
        ]
        result = _extract_timestamps(details)
        assert result["first_unit_dispatched"] == "2026-02-12T14:30:15"
        assert result["first_unit_enroute"] == "2026-02-12T14:32:00"

    def test_handles_dispatch_variant(self):
        details = [
            {"status": "Dispatch", "time_of_status_change": "2026-02-12T14:30:15"},
        ]
        result = _extract_timestamps(details)
        assert result["first_unit_dispatched"] == "2026-02-12T14:30:15"

    def test_empty_details(self):
        assert _extract_timestamps([]) == {}

    def test_skips_unknown_statuses(self):
        details = [
            {"status": "Available", "time_of_status_change": "2026-02-12T15:00:00"},
        ]
        assert _extract_timestamps(details) == {}

    def test_skips_missing_fields(self):
        details = [
            {"status": "Dispatched"},
            {"time_of_status_change": "2026-02-12T14:30:15"},
            {"status": "", "time_of_status_change": "2026-02-12T14:30:15"},
        ]
        assert _extract_timestamps(details) == {}


class TestPrefillFromDispatch:
    @patch("sjifire.ops.dispatch.store.DispatchStore")
    async def test_extracts_fields_from_dispatch(self, mock_store_cls):
        from sjifire.ops.dispatch.models import DispatchCallDocument

        dispatch = DispatchCallDocument(
            id="uuid-1",
            year="2026",
            long_term_call_id="26-000944",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
            city="Friday Harbor",
            state="WA",
            geo_location="48.5343,-123.0170",
            responder_details=[
                {"status": "Dispatched", "time_of_status_change": "2026-02-12T14:30:15"},
                {"status": "Enroute", "time_of_status_change": "2026-02-12T14:32:00"},
            ],
        )

        mock_store = AsyncMock()
        mock_store.get_by_dispatch_id = AsyncMock(return_value=dispatch)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _prefill_from_dispatch("26-000944")

        assert result["address"] == "200 Spring St"
        assert result["city"] == "Friday Harbor"
        assert result["state"] == "WA"
        assert result["latitude"] == pytest.approx(48.5343)
        assert result["longitude"] == pytest.approx(-123.0170)
        assert "first_unit_dispatched" in result["timestamps"]
        assert "first_unit_enroute" in result["timestamps"]

    @patch("sjifire.ops.dispatch.store.DispatchStore")
    async def test_returns_empty_when_not_found(self, mock_store_cls):
        mock_store = AsyncMock()
        mock_store.get_by_dispatch_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _prefill_from_dispatch("26-999999")
        assert result == {}

    @patch("sjifire.ops.dispatch.store.DispatchStore")
    async def test_handles_store_error(self, mock_store_cls):
        mock_store_cls.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))

        result = await _prefill_from_dispatch("26-000944")
        assert result == {}


# ── NERIS address helper ──
class TestAddressFromNerisLocation:
    def test_full_address(self):
        loc = {
            "complete_number": "94",
            "street_prefix_direction": "N",
            "street": "Zepher",
            "street_postfix": "Ln",
        }
        assert _address_from_neris_location(loc) == "94 N Zepher Ln"

    def test_number_and_street_only(self):
        loc = {"complete_number": "1632", "street": "San Juan"}
        assert _address_from_neris_location(loc) == "1632 San Juan"

    def test_falls_back_to_number_field(self):
        loc = {"number": "200", "street": "Spring"}
        assert _address_from_neris_location(loc) == "200 Spring"

    def test_complete_number_preferred_over_number(self):
        loc = {"complete_number": "94", "number": "90", "street": "Main"}
        assert _address_from_neris_location(loc) == "94 Main"

    def test_street_only(self):
        loc = {"street": "Mullis"}
        assert _address_from_neris_location(loc) == "Mullis"

    def test_empty_location(self):
        assert _address_from_neris_location({}) == ""

    def test_all_none_values(self):
        loc = {
            "complete_number": None,
            "number": None,
            "street_prefix_direction": None,
            "street": None,
            "street_postfix": None,
        }
        assert _address_from_neris_location(loc) == ""

    def test_whitespace_stripped(self):
        loc = {"complete_number": " 94 ", "street": " Zepher "}
        assert _address_from_neris_location(loc) == "94 Zepher"

    def test_with_street_postfix_direction(self):
        """street_postfix_direction is not used (only prefix_direction, street, postfix)."""
        loc = {
            "complete_number": "100",
            "street": "Main",
            "street_postfix": "St",
            "street_postfix_direction": "NW",  # not in the assembled fields
        }
        assert _address_from_neris_location(loc) == "100 Main St"


# ── NERIS prefill ──
# Full NERIS record fixture matching real API shape
_SAMPLE_NERIS_RECORD = {
    "neris_id": "FD53055879|26-000039|1767316361",
    "base": {
        "outcome_narrative": "Campfire extinguished at 94 Zepher.",
        "location": {
            "complete_number": "94",
            "street": "Zepher",
            "street_prefix_direction": None,
            "street_postfix": None,
            "incorporated_municipality": "Friday Harbor",
            "state": "WA",
        },
    },
    "incident_types": [
        {"primary": True, "type": "FIRE||OUTSIDE_FIRE||CONSTRUCTION_WASTE"},
        {"primary": False, "type": "PUBSERV||ALARMS_NONMED"},
    ],
    "dispatch": {
        "incident_number": "26-000039",
        "call_create": "2026-01-02T01:12:41+00:00",
        "incident_clear": "2026-01-02T02:16:31+00:00",
        "location": {
            "complete_number": "1632",
            "street": "San Juan",
            "incorporated_municipality": "Friday Harbor",
            "state": "WA",
        },
        "unit_responses": [
            {
                "unit_neris_id": "FD53055879S001U005",
                "staffing": 1,
                "dispatch": "2026-01-02T01:12:41+00:00",
                "enroute_to_scene": "2026-01-02T01:15:52+00:00",
                "on_scene": "2026-01-02T01:38:49+00:00",
                "unit_clear": "2026-01-02T02:16:31+00:00",
                "response_mode": "NON_EMERGENT",
            },
            {
                "unit_neris_id": "FD53055879S001U000",
                "staffing": 4,
                "dispatch": "2026-01-02T01:12:41+00:00",
                "enroute_to_scene": "2026-01-02T01:15:00+00:00",
                "on_scene": "2026-01-02T01:41:03+00:00",
                "unit_clear": "2026-01-02T02:16:31+00:00",
                "response_mode": "NON_EMERGENT",
            },
        ],
    },
}


class TestPrefillFromNeris:
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_extracts_all_fields(self, mock_get):
        mock_get.return_value = _SAMPLE_NERIS_RECORD

        result = await _prefill_from_neris("FD53055879|26-000039|1767316361")

        assert result["neris_incident_id"] == "FD53055879|26-000039|1767316361"
        assert result["incident_type"] == "FIRE||OUTSIDE_FIRE||CONSTRUCTION_WASTE"
        assert result["outcome_narrative"] == "Campfire extinguished at 94 Zepher."
        assert result["address"] == "94 Zepher"
        assert result["city"] == "Friday Harbor"
        assert result["state"] == "WA"
        assert len(result["unit_responses"]) == 2
        assert result["unit_responses"][0]["unit_neris_id"] == "FD53055879S001U005"
        assert result["timestamps"]["psap_answer"] == "2026-01-02T01:12:41+00:00"
        assert result["timestamps"]["incident_clear"] == "2026-01-02T02:16:31+00:00"

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_picks_earliest_unit_timestamps(self, mock_get):
        mock_get.return_value = _SAMPLE_NERIS_RECORD

        result = await _prefill_from_neris("FD53055879|26-000039|1767316361")

        # Unit U000 had earlier enroute (01:15:00 vs 01:15:52)
        assert result["timestamps"]["first_unit_enroute"] == "2026-01-02T01:15:00+00:00"
        # Both units dispatched at the same time
        assert result["timestamps"]["first_unit_dispatched"] == "2026-01-02T01:12:41+00:00"
        # Unit U005 had earlier on_scene (01:38:49 vs 01:41:03)
        assert result["timestamps"]["first_unit_arrived"] == "2026-01-02T01:38:49+00:00"

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_prefers_base_location_over_dispatch(self, mock_get):
        """base.location is the corrected address; dispatch.location is the original."""
        mock_get.return_value = _SAMPLE_NERIS_RECORD

        result = await _prefill_from_neris("FD53055879|26-000039|1767316361")

        # base.location has "94 Zepher", dispatch has "1632 San Juan"
        assert result["address"] == "94 Zepher"

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_falls_back_to_dispatch_location(self, mock_get):
        record = {
            "base": {"location": None},
            "incident_types": [{"type": "MEDICAL||ILLNESS"}],
            "dispatch": {
                "location": {
                    "complete_number": "200",
                    "street": "Spring",
                    "incorporated_municipality": "Friday Harbor",
                    "state": "WA",
                },
                "unit_responses": [],
            },
        }
        mock_get.return_value = record

        result = await _prefill_from_neris("FD|X|Y")

        assert result["address"] == "200 Spring"
        assert result["city"] == "Friday Harbor"

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_handles_empty_location(self, mock_get):
        record = {
            "base": {"location": {}},
            "incident_types": [],
            "dispatch": {"location": {}, "unit_responses": []},
        }
        mock_get.return_value = record

        result = await _prefill_from_neris("FD|X|Y")

        assert "address" not in result
        assert "city" not in result

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_handles_no_unit_responses(self, mock_get):
        record = {
            "base": {},
            "incident_types": [{"type": "FIRE||CHIMNEY"}],
            "dispatch": {"unit_responses": []},
        }
        mock_get.return_value = record

        result = await _prefill_from_neris("FD|X|Y")

        assert "unit_responses" not in result
        assert result["incident_type"] == "FIRE||CHIMNEY"

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_handles_no_incident_types(self, mock_get):
        record = {
            "base": {},
            "incident_types": [],
            "dispatch": {"unit_responses": []},
        }
        mock_get.return_value = record

        result = await _prefill_from_neris("FD|X|Y")

        assert "incident_type" not in result

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_handles_no_narrative(self, mock_get):
        record = {
            "base": {"outcome_narrative": None},
            "incident_types": [],
            "dispatch": {"unit_responses": []},
        }
        mock_get.return_value = record

        result = await _prefill_from_neris("FD|X|Y")

        assert "outcome_narrative" not in result

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_returns_empty_on_not_found(self, mock_get):
        mock_get.return_value = None

        result = await _prefill_from_neris("FD|BOGUS|999")

        assert result == {}

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_returns_empty_on_api_error(self, mock_get):
        mock_get.side_effect = RuntimeError("Connection refused")

        result = await _prefill_from_neris("FD|X|Y")

        assert result == {}

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_strips_none_from_unit_responses(self, mock_get):
        record = {
            "base": {},
            "incident_types": [],
            "dispatch": {
                "unit_responses": [
                    {
                        "unit_neris_id": "FD123",
                        "staffing": 2,
                        "dispatch": "2026-01-02T01:00:00+00:00",
                        "enroute_to_scene": None,
                        "on_scene": None,
                        "unit_clear": None,
                        "response_mode": "EMERGENT",
                    }
                ],
            },
        }
        mock_get.return_value = record

        result = await _prefill_from_neris("FD|X|Y")

        unit = result["unit_responses"][0]
        assert "enroute_to_scene" not in unit
        assert "on_scene" not in unit
        assert "unit_clear" not in unit
        assert unit["unit_neris_id"] == "FD123"
        assert unit["dispatch"] == "2026-01-02T01:00:00+00:00"

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_handles_missing_dispatch_section(self, mock_get):
        record = {
            "base": {"outcome_narrative": "Test"},
            "incident_types": [{"type": "MEDICAL"}],
        }
        mock_get.return_value = record

        result = await _prefill_from_neris("FD|X|Y")

        assert result["incident_type"] == "MEDICAL"
        assert result["outcome_narrative"] == "Test"
        assert "unit_responses" not in result
        assert "timestamps" not in result


# ── Create incident with NERIS import ──
class TestCreateIncidentWithNeris:
    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_neris_data_populates_draft(
        self, mock_store_cls, mock_dispatch, mock_neris, regular_user
    ):
        mock_dispatch.return_value = {
            "address": "165 San Juan Rd",
            "city": "Friday Harbor",
            "state": "WA",
            "latitude": 48.5,
            "longitude": -123.0,
            "timestamps": {"psap_answer": "2026-01-02T01:12:00"},
        }
        mock_neris.return_value = {
            "neris_incident_id": "FD53055879|26-000039|1767316361",
            "incident_type": "FIRE||OUTSIDE_FIRE||CONSTRUCTION_WASTE",
            "outcome_narrative": "Campfire extinguished.",
            "address": "94 Zepher",
            "city": "Friday Harbor",
            "state": "WA",
            "unit_responses": [{"unit_neris_id": "FD53055879S001U005", "staffing": 1}],
            "timestamps": {
                "psap_answer": "2026-01-02T01:12:41+00:00",
                "first_unit_dispatched": "2026-01-02T01:12:41+00:00",
            },
        }

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await create_incident(
            incident_number="26-000039",
            incident_date="2026-01-02",
            station="S31",
            neris_id="FD53055879|26-000039|1767316361",
        )

        assert result["neris_incident_id"] == "FD53055879|26-000039|1767316361"
        assert result["incident_type"] == "FIRE||OUTSIDE_FIRE||CONSTRUCTION_WASTE"
        assert result["narratives"]["outcome"] == "Campfire extinguished."
        # NERIS address wins over dispatch
        assert result["address"] == "94 Zepher"
        assert len(result["unit_responses"]) == 1
        assert result["timestamps"]["first_unit_dispatched"] == "2026-01-02T01:12:41+00:00"
        # Dispatch latitude preserved (NERIS didn't supply one)
        assert result["latitude"] == 48.5

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_neris_overrides_dispatch_for_shared_keys(
        self, mock_store_cls, mock_dispatch, mock_neris, regular_user
    ):
        mock_dispatch.return_value = {
            "address": "165 San Juan Rd",
            "city": "Friday Harbor",
            "timestamps": {"psap_answer": "2026-01-02T01:10:00"},
        }
        mock_neris.return_value = {
            "neris_incident_id": "FD|X|Y",
            "address": "94 Zepher Ln",
            "city": "Friday Harbor",
            "timestamps": {"psap_answer": "2026-01-02T01:12:41+00:00"},
        }

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await create_incident(
            incident_number="26-000039",
            incident_date="2026-01-02",
            station="S31",
            neris_id="FD|X|Y",
        )

        # NERIS values win
        assert result["address"] == "94 Zepher Ln"
        assert result["timestamps"]["psap_answer"] == "2026-01-02T01:12:41+00:00"

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_dispatch_fills_gaps_neris_doesnt_cover(
        self, mock_store_cls, mock_dispatch, mock_neris, regular_user
    ):
        mock_dispatch.return_value = {
            "address": "165 San Juan Rd",
            "latitude": 48.5,
            "longitude": -123.0,
        }
        mock_neris.return_value = {
            "neris_incident_id": "FD|X|Y",
            "incident_type": "FIRE||CHIMNEY",
        }

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await create_incident(
            incident_number="26-000039",
            incident_date="2026-01-02",
            station="S31",
            neris_id="FD|X|Y",
        )

        # Dispatch values fill the gap
        assert result["latitude"] == 48.5
        assert result["longitude"] == -123.0
        # NERIS values applied
        assert result["incident_type"] == "FIRE||CHIMNEY"

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_neris_failure_falls_back_to_dispatch(
        self, mock_store_cls, mock_dispatch, mock_neris, regular_user
    ):
        mock_dispatch.return_value = {
            "address": "165 San Juan Rd",
            "city": "Friday Harbor",
            "state": "WA",
        }
        # NERIS fetch failed — returns empty
        mock_neris.return_value = {}

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await create_incident(
            incident_number="26-000039",
            incident_date="2026-01-02",
            station="S31",
            neris_id="FD|X|Y",
        )

        # Dispatch data still applied
        assert result["address"] == "165 San Juan Rd"
        assert result["neris_incident_id"] is None

    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_no_neris_id_skips_neris_fetch(self, mock_store_cls, mock_dispatch, regular_user):
        mock_dispatch.return_value = {"address": "200 Spring St"}

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("sjifire.ops.incidents.tools._prefill_from_neris") as mock_neris:
            result = await create_incident(
                incident_number="26-000944",
                incident_date="2026-02-12",
                station="S31",
            )
            mock_neris.assert_not_called()

        assert result["address"] == "200 Spring St"
        assert result["neris_incident_id"] is None

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_explicit_incident_type_overrides_neris(
        self, mock_store_cls, mock_dispatch, mock_neris, regular_user
    ):
        mock_dispatch.return_value = {}
        mock_neris.return_value = {
            "neris_incident_id": "FD|X|Y",
            "incident_type": "FIRE||OUTSIDE_FIRE",
        }

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await create_incident(
            incident_number="26-000039",
            incident_date="2026-01-02",
            station="S31",
            incident_type="MEDICAL||ILLNESS",
            neris_id="FD|X|Y",
        )

        # Explicit arg wins
        assert result["incident_type"] == "MEDICAL||ILLNESS"


class TestResetIncident:
    # TODO: Re-enable _mock_cooldown_store fixture when cooldown is restored.
    # @pytest.fixture(autouse=True)
    # def _mock_cooldown_store(self):
    #     """Mock TokenStore for cooldown checks (in-memory dict per test)."""
    #     cooldown_store: dict[str, dict] = {}
    #
    #     async def mock_get(token_type, token_id):
    #         return cooldown_store.get(f"{token_type}:{token_id}")
    #
    #     async def mock_set(token_type, token_id, data, ttl):
    #         cooldown_store[f"{token_type}:{token_id}"] = data
    #
    #     mock_store = AsyncMock()
    #     mock_store.get = AsyncMock(side_effect=mock_get)
    #     mock_store.set = AsyncMock(side_effect=mock_set)
    #
    #     async def mock_get_token_store():
    #         return mock_store
    #
    #     with patch("sjifire.ops.incidents.tools.get_token_store", mock_get_token_store):
    #         yield

    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_creator_resets_draft(self, mock_store_cls, mock_prefill, regular_user):
        doc = IncidentDocument(
            id="doc-reset-1",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            incident_type="FIRE||STRUCTURE_FIRE",
            address="100 Main St",
            crew=[CrewAssignment(name="John", email="john@sjifire.org", position="FF")],
            narratives=Narratives(outcome="Fire extinguished", actions_taken="Pulled hose"),
            internal_notes="Test notes",
            created_by="ff@sjifire.org",
        )

        mock_prefill.return_value = {
            "address": "200 Spring St",
            "city": "Friday Harbor",
            "state": "WA",
            "latitude": 48.5343,
            "longitude": -123.017,
            "timestamps": {"psap_answer": "2026-02-12T14:30:00"},
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-reset-1")

        # Identity preserved
        assert result["id"] == "doc-reset-1"
        assert result["incident_number"] == "26-000944"
        assert result["station"] == "S31"
        assert result["created_by"] == "ff@sjifire.org"

        # Content cleared
        assert result["incident_type"] is None
        assert result["crew"] == []
        assert result["unit_responses"] == []
        assert result["narratives"]["outcome"] == ""
        assert result["narratives"]["actions_taken"] == ""
        assert result["internal_notes"] == ""

        # Dispatch pre-fill applied
        assert result["address"] == "200 Spring St"
        assert result["city"] == "Friday Harbor"
        assert result["latitude"] == pytest.approx(48.5343)
        assert result["timestamps"]["psap_answer"] == "2026-02-12T14:30:00"

        # Status reset to draft
        assert result["status"] == "draft"

    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_officer_can_reset_others(self, mock_store_cls, mock_prefill, officer_user):
        doc = IncidentDocument(
            id="doc-reset-2",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",  # Different user
        )

        mock_prefill.return_value = {}
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-reset-2")
        assert "error" not in result
        assert result["status"] == "draft"

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_crew_cannot_reset(self, mock_store_cls, sample_doc):
        crew_user = UserContext(email="crew1@sjifire.org", name="Crew", user_id="c1")
        set_current_user(crew_user)

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-123")
        assert "error" in result
        assert "permission" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_cannot_reset_ready_review(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "ready_review"
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-123")
        assert "error" in result
        assert "ready_review" in result["error"]

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_cannot_reset_submitted(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "submitted"
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-123")
        assert "error" in result
        assert "submitted" in result["error"]

    # TODO: Re-enable these tests when 24hr cooldown is re-enabled.
    @pytest.mark.skip(reason="Reset cooldown temporarily disabled for testing")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_cooldown_blocks_second_reset(self, mock_store_cls, mock_prefill, regular_user):
        doc = IncidentDocument(
            id="doc-reset-cd",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
        )

        mock_prefill.return_value = {}
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # First reset succeeds
        result1 = await reset_incident("doc-reset-cd")
        assert "error" not in result1

        # Second reset blocked
        result2 = await reset_incident("doc-reset-cd")
        assert "error" in result2
        assert "24 hours" in result2["error"]

    @pytest.mark.skip(reason="Reset cooldown temporarily disabled for testing")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_cooldown_is_per_user(self, mock_store_cls, mock_prefill, regular_user):
        doc = IncidentDocument(
            id="doc-reset-pu",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
        )

        mock_prefill.return_value = {}
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # First user resets
        result1 = await reset_incident("doc-reset-pu")
        assert "error" not in result1

        # Different user can still reset (they're the officer, doc created by ff)
        doc2 = IncidentDocument(
            id="doc-reset-pu2",
            station="S31",
            incident_number="26-000945",
            incident_date=date(2026, 2, 12),
            created_by="chief@sjifire.org",
        )
        mock_store.get_by_id = AsyncMock(return_value=doc2)
        officer = UserContext(
            email="chief@sjifire.org",
            name="Chief",
            user_id="u2",
            groups=frozenset(["officer-group"]),
        )
        set_current_user(officer)

        result2 = await reset_incident("doc-reset-pu2")
        assert "error" not in result2

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_not_found(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("nonexistent")
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestImportFromNeris:
    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_imports_neris_fields_into_draft(self, mock_store_cls, mock_neris, regular_user):
        doc = IncidentDocument(
            id="doc-import-1",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
            address="200 Spring St",
            timestamps={"psap_answer": "2026-02-12T14:30:00"},
        )

        mock_neris.return_value = {
            "neris_incident_id": "FD53055879|26SJ0001|123",
            "incident_type": "FIRE||STRUCTURE_FIRE",
            "outcome_narrative": "Fire extinguished.",
            "address": "94 Zepher Ln",
            "city": "Friday Harbor",
            "state": "WA",
            "unit_responses": [{"unit_neris_id": "FD53055879S001U005", "staffing": 1}],
            "timestamps": {
                "psap_answer": "2026-02-12T14:30:15+00:00",
                "first_unit_dispatched": "2026-02-12T14:32:00+00:00",
            },
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-1", neris_id="FD53055879|26SJ0001|123")

        assert result["neris_incident_id"] == "FD53055879|26SJ0001|123"
        assert result["incident_type"] == "FIRE||STRUCTURE_FIRE"
        assert result["narratives"]["outcome"] == "Fire extinguished."
        assert result["address"] == "94 Zepher Ln"
        assert result["city"] == "Friday Harbor"
        assert result["state"] == "WA"
        assert len(result["unit_responses"]) == 1
        assert result["timestamps"]["first_unit_dispatched"] == "2026-02-12T14:32:00+00:00"

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_uses_existing_neris_id_when_param_omitted(
        self, mock_store_cls, mock_neris, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-2",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
            neris_incident_id="FD53055879|26SJ0001|123",
        )

        mock_neris.return_value = {
            "neris_incident_id": "FD53055879|26SJ0001|123",
            "incident_type": "MEDICAL||ILLNESS",
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-2")

        assert result["incident_type"] == "MEDICAL||ILLNESS"
        mock_neris.assert_called_once_with("FD53055879|26SJ0001|123")

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_param_neris_id_overrides_existing(
        self, mock_store_cls, mock_neris, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-3",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
            neris_incident_id="FD|OLD|111",
        )

        mock_neris.return_value = {
            "neris_incident_id": "FD|NEW|222",
            "incident_type": "FIRE||CHIMNEY",
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-3", neris_id="FD|NEW|222")

        assert result["neris_incident_id"] == "FD|NEW|222"
        mock_neris.assert_called_once_with("FD|NEW|222")

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_error_no_neris_id_available(self, mock_store_cls, regular_user):
        doc = IncidentDocument(
            id="doc-import-4",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
        )

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-4")

        assert "error" in result
        assert "No NERIS ID" in result["error"]

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_error_not_found(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("nonexistent")

        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_error_submitted_incident(self, mock_store_cls, regular_user):
        doc = IncidentDocument(
            id="doc-import-5",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
            status="submitted",
        )

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-5", neris_id="FD|X|Y")

        assert "error" in result
        assert "submitted" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_error_neris_fetch_fails(self, mock_store_cls, mock_neris, regular_user):
        doc = IncidentDocument(
            id="doc-import-6",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
        )

        mock_neris.return_value = {}  # Fetch failed, returns empty

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-6", neris_id="FD|BAD|999")

        assert "error" in result
        assert "Failed to fetch" in result["error"]
        # Verify store.update was NOT called (incident not corrupted)
        mock_store.update.assert_not_called()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_access_denied_non_creator_non_officer(self, mock_store_cls):
        stranger = UserContext(email="stranger@sjifire.org", name="X", user_id="x")
        set_current_user(stranger)

        doc = IncidentDocument(
            id="doc-import-7",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
        )

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-7", neris_id="FD|X|Y")

        assert "error" in result
        assert "permission" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_timestamps_merge_preserves_local_keys(
        self, mock_store_cls, mock_neris, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-8",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
            timestamps={
                "psap_answer": "2026-02-12T14:30:00",
                "local_custom_key": "2026-02-12T15:00:00",
            },
        )

        mock_neris.return_value = {
            "neris_incident_id": "FD|X|Y",
            "timestamps": {
                "psap_answer": "2026-02-12T14:30:15+00:00",
                "first_unit_dispatched": "2026-02-12T14:32:00+00:00",
            },
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-8", neris_id="FD|X|Y")

        # NERIS overwrites matching key
        assert result["timestamps"]["psap_answer"] == "2026-02-12T14:30:15+00:00"
        # NERIS adds new key
        assert result["timestamps"]["first_unit_dispatched"] == "2026-02-12T14:32:00+00:00"
        # Local-only key preserved
        assert result["timestamps"]["local_custom_key"] == "2026-02-12T15:00:00"

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_edit_history_records_neris_import(
        self, mock_store_cls, mock_neris, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-9",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
        )

        mock_neris.return_value = {
            "neris_incident_id": "FD|X|Y",
            "incident_type": "MEDICAL||ILLNESS",
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-9", neris_id="FD|X|Y")

        history = result["edit_history"]
        assert len(history) == 1
        assert history[0]["fields_changed"] == ["neris_import"]
        assert history[0]["editor_email"] == "ff@sjifire.org"

    @patch("sjifire.ops.incidents.tools._prefill_from_neris")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_preserves_actions_taken_narrative(
        self, mock_store_cls, mock_neris, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-10",
            station="S31",
            incident_number="26-000944",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
            narratives=Narratives(
                outcome="Old narrative",
                actions_taken="Pulled hose line, applied water",
            ),
        )

        mock_neris.return_value = {
            "neris_incident_id": "FD|X|Y",
            "outcome_narrative": "Updated NERIS narrative",
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("doc-import-10", neris_id="FD|X|Y")

        # Outcome overwritten by NERIS
        assert result["narratives"]["outcome"] == "Updated NERIS narrative"
        # Actions taken preserved (local-only)
        assert result["narratives"]["actions_taken"] == "Pulled hose line, applied water"
