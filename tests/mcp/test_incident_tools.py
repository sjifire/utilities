"""Tests for MCP incident tools with access control."""

import os
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.mcp.auth import UserContext, set_current_user
from sjifire.mcp.incidents.models import CrewAssignment, IncidentDocument, Narratives
from sjifire.mcp.incidents.tools import (
    _check_edit_access,
    _check_view_access,
    _extract_timestamps,
    _prefill_from_dispatch,
    create_incident,
    get_incident,
    get_neris_incident,
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
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_creator_gets_own(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_incident("doc-123")
        assert result["incident_number"] == "26-000944"

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_not_found(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_incident("nonexistent")
        assert "error" in result


class TestListIncidents:
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_creator_can_update(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident("doc-123", address="200 Spring St")
        assert result["address"] == "200 Spring St"

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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
    @patch("sjifire.mcp.incidents.tools._list_neris_incidents")
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

    @patch("sjifire.mcp.incidents.tools._list_neris_incidents")
    async def test_handles_api_error(self, mock_list, officer_user):
        mock_list.side_effect = RuntimeError("Connection failed")

        result = await list_neris_incidents()
        assert "error" in result
        assert "Connection failed" in result["error"]


class TestGetNerisIncident:
    @patch("sjifire.mcp.incidents.tools._get_neris_incident")
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

    @patch("sjifire.mcp.incidents.tools._get_neris_incident")
    async def test_not_found(self, mock_get, officer_user):
        mock_get.return_value = None

        result = await get_neris_incident("FD53055879|BOGUS|999")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.mcp.incidents.tools._get_neris_incident")
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
    @patch("sjifire.mcp.dispatch.store.DispatchStore")
    async def test_extracts_fields_from_dispatch(self, mock_store_cls):
        from sjifire.mcp.dispatch.models import DispatchCallDocument

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

    @patch("sjifire.mcp.dispatch.store.DispatchStore")
    async def test_returns_empty_when_not_found(self, mock_store_cls):
        mock_store = AsyncMock()
        mock_store.get_by_dispatch_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _prefill_from_dispatch("26-999999")
        assert result == {}

    @patch("sjifire.mcp.dispatch.store.DispatchStore")
    async def test_handles_store_error(self, mock_store_cls):
        mock_store_cls.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))

        result = await _prefill_from_dispatch("26-000944")
        assert result == {}


class TestResetIncident:
    @pytest.fixture(autouse=True)
    def _mock_cooldown_store(self):
        """Mock TokenStore for cooldown checks (in-memory dict per test)."""
        cooldown_store: dict[str, dict] = {}

        async def mock_get(token_type, token_id):
            return cooldown_store.get(f"{token_type}:{token_id}")

        async def mock_set(token_type, token_id, data, ttl):
            cooldown_store[f"{token_type}:{token_id}"] = data

        mock_store = AsyncMock()
        mock_store.get = AsyncMock(side_effect=mock_get)
        mock_store.set = AsyncMock(side_effect=mock_set)

        async def mock_get_token_store():
            return mock_store

        with patch("sjifire.mcp.incidents.tools.get_token_store", mock_get_token_store):
            yield

    @patch("sjifire.mcp.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_cannot_reset_ready_review(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "ready_review"
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-123")
        assert "error" in result
        assert "ready_review" in result["error"]

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_cannot_reset_submitted(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "submitted"
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("doc-123")
        assert "error" in result
        assert "submitted" in result["error"]

    @patch("sjifire.mcp.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools._prefill_from_dispatch")
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
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

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_not_found(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await reset_incident("nonexistent")
        assert "error" in result
        assert "not found" in result["error"].lower()
