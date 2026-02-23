"""Tests for incident tools with access control."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.incidents.models import IncidentDocument, PersonnelAssignment, UnitAssignment
from sjifire.ops.incidents.tools import (
    _address_from_neris_location,
    _build_import_comparison,
    _check_edit_access,
    _check_view_access,
    _extract_timestamps,
    _parse_neris_record,
    _prefill_from_dispatch,
    _prefill_from_neris,
    create_incident,
    finalize_incident,
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
def _editor_group_env():
    """Set the editor group ID for all tests."""
    import sjifire.ops.auth

    sjifire.ops.auth._EDITOR_GROUP_ID = None
    with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "officer-group"}):
        yield
    sjifire.ops.auth._EDITOR_GROUP_ID = None


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
        incident_number="26-000944",
        incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
        created_by="ff@sjifire.org",
        extras={"station": "S31"},
        units=[
            UnitAssignment(
                unit_id="E31",
                personnel=[
                    PersonnelAssignment(name="Crew 1", email="crew1@sjifire.org", position="FF")
                ],
            ),
        ],
    )


# Access control tests
class TestViewAccess:
    def test_creator_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "ff@sjifire.org", is_editor=False)

    def test_crew_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "crew1@sjifire.org", is_editor=False)

    def test_officer_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "random@sjifire.org", is_editor=True)

    def test_stranger_cannot_view(self, sample_doc):
        assert not _check_view_access(sample_doc, "stranger@sjifire.org", is_editor=False)


class TestEditAccess:
    def test_creator_can_edit(self, sample_doc):
        assert _check_edit_access(sample_doc, "ff@sjifire.org", is_editor=False)

    def test_officer_can_edit(self, sample_doc):
        assert _check_edit_access(sample_doc, "random@sjifire.org", is_editor=True)

    def test_crew_cannot_edit(self, sample_doc):
        assert not _check_edit_access(sample_doc, "crew1@sjifire.org", is_editor=False)

    def test_stranger_cannot_edit(self, sample_doc):
        assert not _check_edit_access(sample_doc, "stranger@sjifire.org", is_editor=False)


# Tool tests with mocked store
class TestCreateIncident:
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_creates_draft(self, mock_store_cls, mock_prefill, regular_user):
        mock_prefill.return_value = {}
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

        assert result["extras"]["station"] == "S31"
        assert result["year"] == "2026"
        assert result["incident_number"] == "26-000944"
        assert result["status"] == "draft"
        assert result["created_by"] == "ff@sjifire.org"
        assert len(result["units"]) >= 1

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
        mock_store.list_by_status.assert_called_once_with(None, exclude_status="submitted")

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_explicit_status_no_exclusion(self, mock_store_cls, officer_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.list_by_status = AsyncMock(return_value=[sample_doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await list_incidents(status="submitted")
        assert result["count"] == 1
        mock_store.list_by_status.assert_called_once_with("submitted", exclude_status=None)


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

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_action_fields(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident(
            "doc-123",
            action_taken="ACTION",
            action_codes=["EMERGENCY_MEDICAL_CARE||PATIENT_ASSESSMENT"],
        )
        assert result["action_taken"] == "ACTION"
        assert result["action_codes"] == ["EMERGENCY_MEDICAL_CARE||PATIENT_ASSESSMENT"]

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_noaction_fields(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident(
            "doc-123",
            action_taken="NOACTION",
            noaction_reason="CANCELLED",
        )
        assert result["action_taken"] == "NOACTION"
        assert result["noaction_reason"] == "CANCELLED"

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_fire_specific_fields(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident(
            "doc-123",
            arrival_conditions="SMOKE_SHOWING",
            outside_fire_cause="NATURAL",
            outside_fire_acres=2.5,
        )
        assert result["arrival_conditions"] == "SMOKE_SHOWING"
        assert result["outside_fire_cause"] == "NATURAL"
        assert result["outside_fire_acres"] == 2.5

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_incident_detail_fields(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident(
            "doc-123",
            additional_incident_types=["PUBSERV||ALARMS_NONMED||FIRE_ALARM"],
            automatic_alarm=True,
        )
        assert result["additional_incident_types"] == ["PUBSERV||ALARMS_NONMED||FIRE_ALARM"]
        assert result["automatic_alarm"] is True

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_location_fields(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident(
            "doc-123",
            apt_suite="Unit 4B",
            zip_code="98250",
            county="San Juan",
        )
        assert result["apt_suite"] == "Unit 4B"
        assert result["zip_code"] == "98250"
        assert result["county"] == "San Juan"

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_people_fields(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident(
            "doc-123",
            people_present=True,
            displaced_count=3,
        )
        assert result["people_present"] is True
        assert result["displaced_count"] == 3

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_extras_merge(self, mock_store_cls, regular_user, sample_doc):
        """Extras should merge into existing, not replace."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # sample_doc already has extras={"station": "S31"}
        result = await update_incident(
            "doc-123",
            extras={"water_supply": "HYDRANT_LESS_500", "smoke_alarm_presence": "NOT_APPLICABLE"},
        )
        assert result["extras"]["station"] == "S31"  # preserved
        assert result["extras"]["water_supply"] == "HYDRANT_LESS_500"  # added
        assert result["extras"]["smoke_alarm_presence"] == "NOT_APPLICABLE"  # added

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_narrative_direct_param(self, mock_store_cls, regular_user, sample_doc):
        """Direct narrative param takes precedence over compat params."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident(
            "doc-123",
            narrative="Full narrative text here.",
        )
        assert result["narrative"] == "Full narrative text here."

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_narrative_direct_overrides_compat(
        self, mock_store_cls, regular_user, sample_doc
    ):
        """When both narrative and outcome_narrative provided, narrative wins."""
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident(
            "doc-123",
            narrative="Direct narrative wins.",
            outcome_narrative="This should be ignored.",
        )
        assert result["narrative"] == "Direct narrative wins."


class TestSubmitIncident:
    async def test_regular_user_cannot_submit(self, regular_user):
        result = await submit_incident("doc-123")
        assert "error" in result
        assert "not authorized" in result["error"].lower()

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
        assert "not authorized" in result["error"].lower()

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
        assert "not authorized" in result["error"].lower()

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
        assert result["first_unit_enroute"] == "2026-02-12T14:32:00"

    def test_dispatch_without_agency_unit_ignored(self):
        """Dispatch status without SJF3/SJF2 unit is not captured as alarm_time."""
        details = [
            {"status": "Dispatch", "time_of_status_change": "2026-02-12T14:30:15"},
        ]
        assert _extract_timestamps(details) == {}

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
    async def test_snapshots_cad_comments_string(self, mock_store_cls):
        """cad_comments is a plain string (JoinedComments from iSpyFire)."""
        from sjifire.ops.dispatch.models import DispatchCallDocument

        dispatch = DispatchCallDocument(
            id="uuid-comments",
            year="2026",
            long_term_call_id="26-002210",
            nature="Medical Aid",
            address="100 Spring St",
            agency_code="SJF",
            cad_comments="18:51:21 Dispatched\n18:55:00 Enroute to scene",
        )

        mock_store = AsyncMock()
        mock_store.get_by_dispatch_id = AsyncMock(return_value=dispatch)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _prefill_from_dispatch("26-002210")

        assert result["dispatch_comments"] == "18:51:21 Dispatched\n18:55:00 Enroute to scene"

    @patch("sjifire.ops.dispatch.store.DispatchStore")
    async def test_skips_empty_cad_comments(self, mock_store_cls):
        from sjifire.ops.dispatch.models import DispatchCallDocument

        dispatch = DispatchCallDocument(
            id="uuid-no-comments",
            year="2026",
            long_term_call_id="26-002211",
            nature="Fire Alarm",
            address="200 Spring St",
            agency_code="SJF",
            cad_comments="",
        )

        mock_store = AsyncMock()
        mock_store.get_by_dispatch_id = AsyncMock(return_value=dispatch)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _prefill_from_dispatch("26-002211")

        assert "dispatch_comments" not in result

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
        "people_present": True,
        "displacement_count": 0,
        "impediment_narrative": "Narrow driveway limited access.",
        "location": {
            "complete_number": "94",
            "street": "Zepher",
            "street_prefix_direction": None,
            "street_postfix": None,
            "incorporated_municipality": "Friday Harbor",
            "state": "WA",
        },
        "location_use": {"use_type": "OUTDOOR||WATERFRONT"},
    },
    "incident_types": [
        {"primary": True, "type": "FIRE||OUTSIDE_FIRE||CONSTRUCTION_WASTE"},
        {"primary": False, "type": "PUBSERV||ALARMS_NONMED"},
    ],
    "actions_tactics": {
        "action_noaction": {
            "type": "ACTION",
            "actions": [
                "FIRE_SUPPRESSION||EXTINGUISHMENT",
                "INVESTIGATION||CAUSE_DETERMINATION",
            ],
        },
    },
    "fire_detail": {
        "location_detail": {
            "type": "STRUCTURE",
            "arrival_condition": "SMOKE_SHOWING",
            "damage_type": "MINOR_DAMAGE",
            "room_of_origin_type": "LIVING_SPACE",
            "floor_of_origin": 1,
            "cause": "ELECTRICAL",
        },
        "water_supply": "HYDRANT_LESS_500",
        "investigation_needed": "NO_CAUSE_OBVIOUS",
        "investigation_types": [],
    },
    "smoke_alarm": {"presence": {"type": "PRESENT"}},
    "fire_alarm": {"presence": {"type": "NOT_APPLICABLE"}},
    "fire_suppression": {"presence": {"type": "NOT_PRESENT"}},
    "tactic_timestamps": {
        "water_on_fire": "2026-01-02T01:42:00+00:00",
        "fire_under_control": "2026-01-02T01:55:00+00:00",
    },
    "medical_details": [
        {
            "patient_care_evaluation": "PATIENT_EVALUATED_NO_CARE_REQUIRED",
            "transport_disposition": "NO_TRANSPORT",
            "patient_status": "UNCHANGED",
        },
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
        assert result["narrative"] == "Campfire extinguished at 94 Zepher."
        assert result["address"] == "94 Zepher"
        assert result["city"] == "Friday Harbor"
        assert result["state"] == "WA"
        assert result["location_use"] == "OUTDOOR||WATERFRONT"
        assert len(result["units"]) == 2
        assert result["units"][0].unit_id == "FD53055879S001U005"
        assert result["timestamps"]["psap_answer"] == "2026-01-02T01:12:41+00:00"
        assert result["timestamps"]["incident_clear"] == "2026-01-02T02:16:31+00:00"
        # New fields from expanded extraction
        assert result["action_taken"] == "ACTION"
        assert len(result["action_codes"]) == 2
        assert result["arrival_conditions"] == "SMOKE_SHOWING"
        assert result["people_present"] is True
        assert result["displaced_count"] == 0
        assert result["additional_incident_types"] == ["PUBSERV||ALARMS_NONMED"]
        assert result["extras"]["water_supply"] == "HYDRANT_LESS_500"
        assert result["extras"]["patient_count"] == 1
        assert result["timestamps"]["water_on_fire"] == "2026-01-02T01:42:00+00:00"

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_picks_earliest_unit_timestamps(self, mock_get):
        mock_get.return_value = _SAMPLE_NERIS_RECORD

        result = await _prefill_from_neris("FD53055879|26-000039|1767316361")

        # Unit U000 had earlier enroute (01:15:00 vs 01:15:52)
        assert result["timestamps"]["first_unit_enroute"] == "2026-01-02T01:15:00+00:00"
        # Unit U005 had earlier on_scene (01:38:49 vs 01:41:03)
        assert result["timestamps"]["first_unit_arrived"] == "2026-01-02T01:38:49+00:00"
        # first_unit_dispatched is not extracted (only enroute and on_scene)
        assert "first_unit_dispatched" not in result["timestamps"]

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

        assert "units" not in result
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

        assert "narrative" not in result

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
    async def test_handles_missing_dispatch_section(self, mock_get):
        record = {
            "base": {"outcome_narrative": "Test"},
            "incident_types": [{"type": "MEDICAL"}],
        }
        mock_get.return_value = record

        result = await _prefill_from_neris("FD|X|Y")

        assert result["incident_type"] == "MEDICAL"
        assert result["narrative"] == "Test"
        assert "units" not in result
        assert "timestamps" not in result


# ── Create incident with NERIS import ──
_CREATE_NERIS_RECORD = {
    "neris_id": "FD53055879|26-000039|1767316361",
    "base": {
        "outcome_narrative": "Campfire extinguished.",
        "location": {
            "complete_number": "94",
            "street": "Zepher",
            "incorporated_municipality": "Friday Harbor",
            "state": "WA",
        },
    },
    "incident_types": [{"primary": True, "type": "FIRE||OUTSIDE_FIRE||CONSTRUCTION_WASTE"}],
    "dispatch": {
        "incident_number": "26-000039",
        "call_create": "2026-01-02T01:12:41+00:00",
        "unit_responses": [
            {
                "unit_neris_id": "FD53055879S001U005",
                "response_mode": "EMERGENT",
                "dispatch": "2026-01-02T01:12:41+00:00",
                "enroute_to_scene": "2026-01-02T01:14:00+00:00",
                "on_scene": "2026-01-02T01:20:00+00:00",
                "unit_clear": "2026-01-02T02:00:00+00:00",
            },
        ],
    },
    "incident_status": {"status": "APPROVED"},
}


class TestCreateIncidentWithNeris:
    """Tests for create_incident with neris_id.

    create_incident now calls _get_neris_incident directly (via asyncio.to_thread)
    and _get_crew_for_incident for schedule data.
    """

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_neris_data_populates_draft(
        self, mock_store_cls, mock_dispatch, mock_get_neris, mock_crew, regular_user
    ):
        mock_dispatch.return_value = {
            "address": "165 San Juan Rd",
            "city": "Friday Harbor",
            "state": "WA",
            "latitude": 48.5,
            "longitude": -123.0,
            "timestamps": {"psap_answer": "2026-01-02T01:12:00"},
        }
        mock_get_neris.return_value = _CREATE_NERIS_RECORD
        mock_crew.return_value = []

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
        assert result["narrative"] == "Campfire extinguished."
        # NERIS address wins over dispatch
        assert result["address"] == "94 Zepher"
        assert len(result["units"]) >= 1
        # Dispatch latitude preserved (NERIS didn't supply one)
        assert result["latitude"] == 48.5
        # Comparison included
        assert "import_comparison" in result

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_dispatch_timestamps_win_over_neris(
        self, mock_store_cls, mock_dispatch, mock_get_neris, mock_crew, regular_user
    ):
        mock_dispatch.return_value = {
            "address": "165 San Juan Rd",
            "city": "Friday Harbor",
            "timestamps": {"psap_answer": "2026-01-02T01:10:00"},
        }
        mock_get_neris.return_value = _CREATE_NERIS_RECORD
        mock_crew.return_value = []

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

        # NERIS address wins (corrected)
        assert result["address"] == "94 Zepher"
        # Dispatch timestamp wins over NERIS (ground truth)
        assert result["timestamps"]["psap_answer"] == "2026-01-02T01:10:00"

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_dispatch_fills_gaps_neris_doesnt_cover(
        self, mock_store_cls, mock_dispatch, mock_get_neris, mock_crew, regular_user
    ):
        mock_dispatch.return_value = {
            "address": "165 San Juan Rd",
            "latitude": 48.5,
            "longitude": -123.0,
        }
        mock_get_neris.return_value = {
            "neris_id": "FD|X|Y",
            "base": {},
            "incident_types": [{"type": "FIRE||CHIMNEY"}],
            "dispatch": {"incident_number": "26-000039", "unit_responses": []},
            "incident_status": {},
        }
        mock_crew.return_value = []

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

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_neris_failure_falls_back_to_dispatch(
        self, mock_store_cls, mock_dispatch, mock_get_neris, regular_user
    ):
        mock_dispatch.return_value = {
            "address": "165 San Juan Rd",
            "city": "Friday Harbor",
            "state": "WA",
        }
        # NERIS fetch failed
        mock_get_neris.side_effect = RuntimeError("Connection refused")

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

        with patch("sjifire.ops.incidents.tools._get_neris_incident") as mock_neris:
            result = await create_incident(
                incident_number="26-000944",
                incident_date="2026-02-12",
                station="S31",
            )
            mock_neris.assert_not_called()

        assert result["address"] == "200 Spring St"
        assert result["neris_incident_id"] is None

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_explicit_incident_type_overrides_neris(
        self, mock_store_cls, mock_dispatch, mock_get_neris, mock_crew, regular_user
    ):
        mock_dispatch.return_value = {}
        mock_get_neris.return_value = {
            "neris_id": "FD|X|Y",
            "base": {},
            "incident_types": [{"type": "FIRE||OUTSIDE_FIRE"}],
            "dispatch": {"incident_number": "26-000039", "unit_responses": []},
            "incident_status": {},
        }
        mock_crew.return_value = []

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
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_reset_clears_action_fields(self, mock_store_cls, mock_prefill, regular_user):
        doc = IncidentDocument(
            id="doc-reset-action",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            extras={"station": "S31"},
            action_taken="NOACTION",
            noaction_reason="CANCELLED",
            action_codes=[],
        )

        mock_prefill.return_value = {}
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-reset-action")

        assert result["action_taken"] is None
        assert result["noaction_reason"] is None
        assert result["action_codes"] == []

    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_creator_resets_draft(self, mock_store_cls, mock_prefill, regular_user):
        doc = IncidentDocument(
            id="doc-reset-1",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            incident_type="FIRE||STRUCTURE_FIRE",
            address="100 Main St",
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[
                        PersonnelAssignment(name="John", email="john@sjifire.org", position="FF")
                    ],
                )
            ],
            narrative="Fire extinguished. Pulled hose.",
            internal_notes="Test notes",
            created_by="ff@sjifire.org",
            extras={"station": "S31"},
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
        assert result["extras"]["station"] == "S31"
        assert result["created_by"] == "ff@sjifire.org"

        # Content cleared
        assert result["incident_type"] is None
        assert result["units"] == []  # cleared (only dispatch prefill units)
        assert result["narrative"] == ""
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
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",  # Different user
            extras={"station": "S31"},
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
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            extras={"station": "S31"},
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
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            extras={"station": "S31"},
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
            incident_number="26-000945",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="chief@sjifire.org",
            extras={"station": "S31"},
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


_IMPORT_NERIS_RECORD = {
    "neris_id": "FD53055879|26-000944|123",
    "base": {
        "outcome_narrative": "Fire extinguished.",
        "location": {
            "complete_number": "94",
            "street": "Zepher Ln",
            "incorporated_municipality": "Friday Harbor",
            "state": "WA",
        },
    },
    "incident_types": [{"primary": True, "type": "FIRE||STRUCTURE_FIRE"}],
    "dispatch": {
        "incident_number": "26-000944",
        "call_create": "2026-02-12T14:30:15+00:00",
        "incident_clear": "2026-02-12T16:00:00+00:00",
        "unit_responses": [
            {
                "unit_neris_id": "FD53055879S001U005",
                "response_mode": "EMERGENT",
                "dispatch": "2026-02-12T14:30:15+00:00",
                "enroute_to_scene": "2026-02-12T14:32:00+00:00",
                "on_scene": "2026-02-12T14:40:00+00:00",
                "unit_clear": "2026-02-12T16:00:00+00:00",
            },
        ],
    },
    "incident_status": {"status": "APPROVED"},
}


class TestImportFromNeris:
    """Tests for import_from_neris with new signature: (neris_id, *, incident_id, station)."""

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_imports_neris_into_existing_draft(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-1",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            extras={"station": "S31"},
            address="200 Spring St",
            timestamps={"psap_answer": "2026-02-12T14:30:00"},
        )

        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {"address": "200 Spring St", "city": "Friday Harbor"}
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123", incident_id="doc-import-1")

        assert result["neris_incident_id"] == "FD53055879|26-000944|123"
        assert result["incident_type"] == "FIRE||STRUCTURE_FIRE"
        assert result["narrative"] == "Fire extinguished."
        assert result["address"] == "94 Zepher Ln"  # NERIS corrected address
        assert result["city"] == "Friday Harbor"
        assert "import_comparison" in result

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_creates_new_incident_from_neris(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        """When no incident_id is provided, creates a new incident."""
        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {
            "address": "200 Spring St",
            "city": "Friday Harbor",
            "state": "WA",
            "latitude": 48.5343,
            "longitude": -123.017,
            "timestamps": {"psap_answer": "2026-02-12T14:30:00"},
            "dispatch_comments": "18:30 Dispatched\n18:32 Enroute",
        }
        mock_crew.return_value = [
            {
                "name": "Capt Smith",
                "position": "Captain",
                "section": "S31",
                "start_time": "18:00",
                "end_time": "18:00",
            },
        ]

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)  # No duplicate
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123")

        assert result["incident_number"] == "26-000944"
        assert result["neris_incident_id"] == "FD53055879|26-000944|123"
        assert result["incident_type"] == "FIRE||STRUCTURE_FIRE"
        assert result["narrative"] == "Fire extinguished."
        assert result["latitude"] == pytest.approx(48.5343)
        assert result["dispatch_comments"] == "18:30 Dispatched\n18:32 Enroute"
        assert result["status"] == "draft"
        assert result["created_by"] == "ff@sjifire.org"
        assert "import_comparison" in result

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_returns_comparison_with_discrepancies(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        """Comparison detects timestamp and address differences."""
        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {
            "address": "200 Spring St",
            "timestamps": {
                "psap_answer": "2026-02-12T14:25:00+00:00",  # 5min before NERIS (14:30:15)
                "alarm_time": "2026-02-12T14:30:30+00:00",  # Only in dispatch
            },
        }
        mock_crew.return_value = [
            {
                "name": "FF Jones",
                "position": "Firefighter",
                "section": "S31",
                "start_time": "18:00",
                "end_time": "18:00",
            },
        ]

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123")

        comp = result["import_comparison"]
        assert comp["sources"]["neris"] is True
        assert comp["sources"]["dispatch"] is True
        assert comp["sources"]["schedule"] is True

        # Should have timestamp discrepancy for psap_answer
        ts_disc = [d for d in comp["discrepancies"] if d["field"] == "psap_answer"]
        assert len(ts_disc) == 1
        assert ts_disc[0]["used"] == "dispatch"

        # Should have address discrepancy
        addr_disc = [d for d in comp["discrepancies"] if d["field"] == "address"]
        assert len(addr_disc) == 1
        assert addr_disc[0]["neris"] == "94 Zepher Ln"
        assert addr_disc[0]["dispatch"] == "200 Spring St"

        # alarm_time only in dispatch → gap filled
        alarm_gaps = [g for g in comp["gaps_filled"] if g["field"] == "alarm_time"]
        assert len(alarm_gaps) == 1
        assert alarm_gaps[0]["source"] == "dispatch"

        # Crew on duty included
        assert len(comp["crew_on_duty"]) == 1
        assert comp["crew_on_duty"][0]["name"] == "FF Jones"

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_dispatch_timestamps_are_ground_truth(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        """Dispatch timestamps overwrite NERIS; NERIS fills gaps only."""
        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {
            "timestamps": {
                "psap_answer": "2026-02-12T14:29:55",  # Dispatch wins
                "alarm_time": "2026-02-12T14:30:30",  # Only dispatch
            },
        }
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123")

        # Dispatch timestamp wins over NERIS
        assert result["timestamps"]["psap_answer"] == "2026-02-12T14:29:55"
        # Dispatch-only timestamp present
        assert result["timestamps"]["alarm_time"] == "2026-02-12T14:30:30"
        # NERIS-only timestamp fills gap
        assert result["timestamps"]["incident_clear"] == "2026-02-12T16:00:00+00:00"

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_error_neris_not_found(self, mock_get_neris, regular_user):
        mock_get_neris.return_value = None

        result = await import_from_neris("FD|BOGUS|999")

        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    async def test_error_neris_api_unavailable(self, mock_get_neris, regular_user):
        mock_get_neris.side_effect = RuntimeError("Connection refused")

        result = await import_from_neris("FD|X|Y")

        assert "error" in result
        assert "Failed to fetch" in result["error"]

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_error_submitted_incident(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-5",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="submitted",
        )

        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {}
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123", incident_id="doc-import-5")

        assert "error" in result
        assert "submitted" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_access_denied_non_creator_non_officer(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew
    ):
        stranger = UserContext(email="stranger@sjifire.org", name="X", user_id="x")
        set_current_user(stranger)

        doc = IncidentDocument(
            id="doc-import-7",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
        )

        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {}
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123", incident_id="doc-import-7")

        assert "error" in result
        assert "permission" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_timestamps_merge_dispatch_wins(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        """Dispatch timestamps overwrite NERIS; local-only keys preserved."""
        doc = IncidentDocument(
            id="doc-import-8",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            timestamps={
                "psap_answer": "2026-02-12T14:30:00",
                "local_custom_key": "2026-02-12T15:00:00",
            },
        )

        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {
            "timestamps": {"psap_answer": "2026-02-12T14:29:55"},
        }
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123", incident_id="doc-import-8")

        # Dispatch timestamp wins over NERIS
        assert result["timestamps"]["psap_answer"] == "2026-02-12T14:29:55"
        # NERIS-only timestamps fill gaps
        assert "incident_clear" in result["timestamps"]
        # Local-only key preserved
        assert result["timestamps"]["local_custom_key"] == "2026-02-12T15:00:00"

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_edit_history_records_neris_import(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-9",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
        )

        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {}
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123", incident_id="doc-import-9")

        history = result["edit_history"]
        assert len(history) == 1
        assert history[0]["fields_changed"] == ["neris_import"]
        assert history[0]["editor_email"] == "ff@sjifire.org"

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_duplicate_incident_number_returns_error(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        """Creating from NERIS when incident_number already exists returns error."""
        existing = IncidentDocument(
            id="existing-doc",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="chief@sjifire.org",
        )

        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {}
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=existing)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123")

        assert "error" in result
        assert "already exists" in result["error"]
        assert result["existing_id"] == "existing-doc"

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_neris_narrative_overwrites_existing(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        doc = IncidentDocument(
            id="doc-import-10",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            narrative="Old narrative. Pulled hose line.",
        )

        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {}
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123", incident_id="doc-import-10")

        # Narrative overwritten by NERIS
        assert result["narrative"] == "Fire extinguished."

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_comparison_includes_neris_data_section(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        """The comparison includes NERIS metadata for later update proposals."""
        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {}
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_number = AsyncMock(return_value=None)
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123")

        comp = result["import_comparison"]
        assert "neris_data" in comp
        assert comp["neris_data"]["neris_id"] == "FD53055879|26-000944|123"
        assert comp["neris_data"]["status"] == "APPROVED"


# ── parse_neris_record (the pure-parsing half) ──
class TestParseNerisRecord:
    def test_extracts_core_fields(self):
        result = _parse_neris_record(_SAMPLE_NERIS_RECORD, "FD53055879|26-000039|1767316361")

        assert result["neris_incident_id"] == "FD53055879|26-000039|1767316361"
        assert result["incident_type"] == "FIRE||OUTSIDE_FIRE||CONSTRUCTION_WASTE"
        assert result["narrative"] == "Campfire extinguished at 94 Zepher."
        assert result["address"] == "94 Zepher"
        assert result["city"] == "Friday Harbor"
        assert result["state"] == "WA"
        assert result["location_use"] == "OUTDOOR||WATERFRONT"
        assert len(result["units"]) == 2
        assert result["timestamps"]["psap_answer"] == "2026-01-02T01:12:41+00:00"
        assert result["timestamps"]["incident_clear"] == "2026-01-02T02:16:31+00:00"

    def test_extracts_actions(self):
        result = _parse_neris_record(_SAMPLE_NERIS_RECORD, "FD|X|Y")

        assert result["action_taken"] == "ACTION"
        assert "FIRE_SUPPRESSION||EXTINGUISHMENT" in result["action_codes"]
        assert "INVESTIGATION||CAUSE_DETERMINATION" in result["action_codes"]

    def test_extracts_noaction(self):
        record = {
            "base": {},
            "incident_types": [{"type": "NOEMERG||CANCELLED"}],
            "actions_tactics": {
                "action_noaction": {"type": "NOACTION", "noaction_type": "CANCELLED"},
            },
        }
        result = _parse_neris_record(record, "FD|X|Y")

        assert result["action_taken"] == "NOACTION"
        assert result["noaction_reason"] == "CANCELLED"
        assert "action_codes" not in result

    def test_extracts_additional_incident_types(self):
        result = _parse_neris_record(_SAMPLE_NERIS_RECORD, "FD|X|Y")

        assert result["additional_incident_types"] == ["PUBSERV||ALARMS_NONMED"]

    def test_extracts_fire_detail(self):
        result = _parse_neris_record(_SAMPLE_NERIS_RECORD, "FD|X|Y")

        assert result["arrival_conditions"] == "SMOKE_SHOWING"
        extras = result["extras"]
        assert extras["fire_bldg_damage"] == "MINOR_DAMAGE"
        assert extras["room_of_origin"] == "LIVING_SPACE"
        assert extras["floor_of_origin"] == 1
        assert extras["fire_cause_in"] == "ELECTRICAL"
        assert extras["water_supply"] == "HYDRANT_LESS_500"
        assert extras["fire_investigation"] == "NO_CAUSE_OBVIOUS"

    def test_extracts_alarms(self):
        result = _parse_neris_record(_SAMPLE_NERIS_RECORD, "FD|X|Y")

        extras = result["extras"]
        assert extras["smoke_alarm_presence"] == "PRESENT"
        assert extras["fire_alarm_presence"] == "NOT_APPLICABLE"
        assert extras["sprinkler_presence"] == "NOT_PRESENT"

    def test_extracts_tactic_timestamps(self):
        result = _parse_neris_record(_SAMPLE_NERIS_RECORD, "FD|X|Y")

        assert result["timestamps"]["water_on_fire"] == "2026-01-02T01:42:00+00:00"
        assert result["timestamps"]["fire_under_control"] == "2026-01-02T01:55:00+00:00"

    def test_extracts_medical_details(self):
        result = _parse_neris_record(_SAMPLE_NERIS_RECORD, "FD|X|Y")

        extras = result["extras"]
        assert extras["patient_count"] == 1
        assert extras["care_disposition"] == "PATIENT_EVALUATED_NO_CARE_REQUIRED"
        assert extras["transport_disposition"] == "NO_TRANSPORT"
        assert extras["patient_status"] == "UNCHANGED"

    def test_extracts_medical_multiple_patients(self):
        record = {
            "base": {},
            "incident_types": [{"type": "MEDICAL||ILLNESS"}],
            "medical_details": [
                {
                    "patient_care_evaluation": "PATIENT_EVALUATED_CARE_PROVIDED",
                    "transport_disposition": "TRANSPORT_BY_EMS_UNIT",
                    "patient_status": "IMPROVED",
                },
                {
                    "patient_care_evaluation": "PATIENT_EVALUATED_REFUSED_CARE",
                    "transport_disposition": "NO_TRANSPORT",
                    "patient_status": "UNCHANGED",
                },
            ],
        }
        result = _parse_neris_record(record, "FD|X|Y")

        extras = result["extras"]
        assert extras["patient_count"] == 2
        assert extras["patient_1_care_disposition"] == "PATIENT_EVALUATED_CARE_PROVIDED"
        assert extras["patient_1_transport_disposition"] == "TRANSPORT_BY_EMS_UNIT"
        assert extras["patient_2_care_disposition"] == "PATIENT_EVALUATED_REFUSED_CARE"
        assert extras["patient_2_transport_disposition"] == "NO_TRANSPORT"

    def test_extracts_people_and_impediment(self):
        result = _parse_neris_record(_SAMPLE_NERIS_RECORD, "FD|X|Y")

        assert result["people_present"] is True
        assert result["displaced_count"] == 0
        assert result["extras"]["impediment_narrative"] == "Narrow driveway limited access."

    def test_handles_empty_record(self):
        result = _parse_neris_record(
            {"base": {}, "incident_types": [], "dispatch": {"unit_responses": []}},
            "FD|X|Y",
        )
        assert result["neris_incident_id"] == "FD|X|Y"
        assert "incident_type" not in result
        assert "narrative" not in result
        assert "action_taken" not in result
        assert "extras" not in result

    def test_handles_missing_dispatch(self):
        result = _parse_neris_record(
            {"base": {"outcome_narrative": "Test"}, "incident_types": [{"type": "MEDICAL"}]},
            "FD|X|Y",
        )
        assert result["incident_type"] == "MEDICAL"
        assert result["narrative"] == "Test"
        assert "units" not in result

    def test_handles_null_fire_detail(self):
        record = {
            "base": {},
            "incident_types": [{"type": "MEDICAL||ILLNESS"}],
            "fire_detail": None,
            "smoke_alarm": None,
            "actions_tactics": None,
            "tactic_timestamps": None,
        }
        result = _parse_neris_record(record, "FD|X|Y")
        assert "arrival_conditions" not in result
        assert "action_taken" not in result


# ── build_import_comparison ──
class TestBuildImportComparison:
    def test_detects_timestamp_discrepancy(self):
        neris = {"timestamps": {"psap_answer": "2026-02-12T14:30:15"}}
        dispatch = {"timestamps": {"psap_answer": "2026-02-12T14:25:00"}}

        comp = _build_import_comparison(neris, dispatch, [])

        ts_disc = [d for d in comp["discrepancies"] if d["field"] == "psap_answer"]
        assert len(ts_disc) == 1
        assert ts_disc[0]["used"] == "dispatch"

    def test_detects_address_discrepancy(self):
        neris = {"address": "94 Zepher Ln"}
        dispatch = {"address": "200 Spring St"}

        comp = _build_import_comparison(neris, dispatch, [])

        addr_disc = [d for d in comp["discrepancies"] if d["field"] == "address"]
        assert len(addr_disc) == 1
        assert addr_disc[0]["used"] == "neris"

    def test_no_discrepancy_when_matching(self):
        neris = {"address": "200 Spring St", "timestamps": {"psap_answer": "2026-02-12T14:30:00"}}
        dispatch = {
            "address": "200 Spring St",
            "timestamps": {"psap_answer": "2026-02-12T14:30:00"},
        }

        comp = _build_import_comparison(neris, dispatch, [])

        assert len(comp["discrepancies"]) == 0

    def test_tracks_gaps_filled(self):
        neris = {"incident_type": "FIRE||CHIMNEY", "narrative": "Fire out."}
        dispatch = {
            "timestamps": {"alarm_time": "2026-02-12T14:30:30"},
            "dispatch_comments": "18:30 Dispatched",
        }
        crew = [{"name": "Smith", "position": "Captain", "section": "S31"}]

        comp = _build_import_comparison(neris, dispatch, crew)

        gap_fields = {g["field"] for g in comp["gaps_filled"]}
        assert "alarm_time" in gap_fields  # dispatch-only timestamp
        assert "incident_type" in gap_fields  # from NERIS
        assert "narrative" in gap_fields  # from NERIS
        assert "crew" in gap_fields  # from schedule
        assert "dispatch_comments" in gap_fields  # from dispatch

    def test_sources_flags(self):
        comp = _build_import_comparison({}, {}, [])
        assert comp["sources"] == {"neris": False, "dispatch": False, "schedule": False}

        comp2 = _build_import_comparison({"incident_type": "X"}, {"address": "Y"}, [{"name": "Z"}])
        assert comp2["sources"] == {"neris": True, "dispatch": True, "schedule": True}

    def test_includes_neris_metadata(self):
        record = {
            "neris_id": "FD|X|Y",
            "dispatch": {"incident_number": "26-001234", "call_create": "2026-02-12T14:30:00"},
            "incident_status": {"status": "SUBMITTED"},
        }

        comp = _build_import_comparison({"incident_type": "FIRE"}, {}, [], record)

        assert comp["neris_data"]["neris_id"] == "FD|X|Y"
        assert comp["neris_data"]["status"] == "SUBMITTED"

    def test_unit_discrepancy_with_different_ids(self):
        neris = {"units": [UnitAssignment(unit_id="FD53055879S001U005")]}
        dispatch = {"units": [UnitAssignment(unit_id="E31")]}

        comp = _build_import_comparison(neris, dispatch, [])

        unit_disc = [d for d in comp["discrepancies"] if d["field"] == "units"]
        assert len(unit_disc) == 1
        assert "NERIS" in unit_disc[0]["note"]


# ── Locked status guards ──
class TestLockedStatusGuards:
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_cannot_update_approved_incident(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "approved"
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident("doc-123", address="Too late")
        assert "error" in result
        assert "approved" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_cannot_reset_approved_incident(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "approved"
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-123")
        assert "error" in result
        assert "approved" in result["error"]

    @patch("sjifire.ops.incidents.tools._get_crew_for_incident", new_callable=AsyncMock)
    @patch("sjifire.ops.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_cannot_import_into_approved_incident(
        self, mock_store_cls, mock_get_neris, mock_dispatch, mock_crew, regular_user
    ):
        doc = IncidentDocument(
            id="doc-approved-1",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="approved",
        )

        mock_get_neris.return_value = _IMPORT_NERIS_RECORD
        mock_dispatch.return_value = {}
        mock_crew.return_value = []

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await import_from_neris("FD53055879|26-000944|123", incident_id="doc-approved-1")
        assert "error" in result
        assert "approved" in result["error"].lower()


# ── Finalize incident ──
class TestFinalizeIncident:
    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_finalize_sets_approved(self, mock_store_cls, mock_get_neris, officer_user):
        doc = IncidentDocument(
            id="doc-finalize-1",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            neris_incident_id="FD53055879|26-000944|123",
            status="ready_review",
        )

        mock_get_neris.return_value = {
            "incident_status": {"status": "APPROVED"},
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await finalize_incident("doc-finalize-1")

        assert result["status"] == "approved"
        assert result["edit_history"][-1]["fields_changed"] == ["finalized"]

    @patch("sjifire.ops.incidents.tools._get_neris_incident")
    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_finalize_sets_submitted_when_pending(
        self, mock_store_cls, mock_get_neris, officer_user
    ):
        doc = IncidentDocument(
            id="doc-finalize-2",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            neris_incident_id="FD53055879|26-000944|123",
            status="ready_review",
        )

        mock_get_neris.return_value = {
            "incident_status": {"status": "SUBMITTED"},
        }

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store.update = AsyncMock(side_effect=lambda d: d)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await finalize_incident("doc-finalize-2")

        assert result["status"] == "submitted"

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_finalize_requires_neris_id(self, mock_store_cls, officer_user):
        doc = IncidentDocument(
            id="doc-finalize-3",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            neris_incident_id=None,
        )

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await finalize_incident("doc-finalize-3")

        assert "error" in result
        assert "no NERIS ID" in result["error"]

    async def test_finalize_requires_editor(self, regular_user):
        result = await finalize_incident("doc-123")

        assert "error" in result
        assert "not authorized" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_finalize_rejects_already_locked(self, mock_store_cls, officer_user):
        doc = IncidentDocument(
            id="doc-finalize-4",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            neris_incident_id="FD53055879|26-000944|123",
            status="submitted",
        )

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await finalize_incident("doc-finalize-4")

        assert "error" in result
        assert "already submitted" in result["error"].lower()

    @patch("sjifire.ops.incidents.tools.IncidentStore")
    async def test_finalize_rejects_approved(self, mock_store_cls, officer_user):
        doc = IncidentDocument(
            id="doc-finalize-5",
            incident_number="26-000944",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            neris_incident_id="FD53055879|26-000944|123",
            status="approved",
        )

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await finalize_incident("doc-finalize-5")

        assert "error" in result
        assert "already approved" in result["error"].lower()
