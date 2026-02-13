"""Tests for MCP dashboard tool."""

import os
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.mcp.auth import UserContext, set_current_user
from sjifire.mcp.dashboard import (
    _fetch_incidents,
    _fetch_recent_calls,
    _normalize_incident_number,
    get_dashboard,
)
from sjifire.mcp.dispatch.models import DispatchCallDocument
from sjifire.mcp.dispatch.store import DispatchStore
from sjifire.mcp.incidents.models import (
    CrewAssignment,
    IncidentDocument,
    Narratives,
)
from sjifire.mcp.incidents.store import IncidentStore

# Shared NERIS return value for tests that don't care about NERIS
_EMPTY_NERIS = {"lookup": {}, "reports": []}


@pytest.fixture(autouse=True)
def _env():
    """Ensure dev mode and set officer group for all tests."""
    with patch.dict(
        os.environ,
        {
            "ENTRA_MCP_API_CLIENT_ID": "",
            "COSMOS_ENDPOINT": "",
            "COSMOS_KEY": "",
            "ENTRA_MCP_OFFICER_GROUP_ID": "officer-group",
        },
        clear=False,
    ):
        set_current_user(None)
        yield
    # Clean up in-memory stores between tests
    DispatchStore._memory.clear()
    IncidentStore._memory.clear()


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
def sample_calls():
    return [
        DispatchCallDocument(
            id="call-1",
            year="2026",
            long_term_call_id="26-001678",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 12, 14, 30, tzinfo=UTC),
            is_completed=True,
        ),
        DispatchCallDocument(
            id="call-2",
            year="2026",
            long_term_call_id="26-001650",
            nature="Fire Alarm",
            address="100 Guard St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 11, 9, 15, tzinfo=UTC),
            is_completed=True,
        ),
    ]


@pytest.fixture
def sample_incident():
    return IncidentDocument(
        id="inc-uuid-1",
        station="S31",
        incident_number="26-001678",
        incident_date=date(2026, 2, 12),
        incident_type="MEDICAL",
        address="200 Spring St",
        crew=[CrewAssignment(name="John", email="ff@sjifire.org")],
        created_by="ff@sjifire.org",
        status="in_progress",
    )


@pytest.fixture
def schedule_result():
    return {
        "date": "2026-02-12",
        "platoon": "A",
        "crew": [
            {"name": "Smith", "position": "Captain", "section": "Operations"},
            {"name": "Jones", "position": "Firefighter", "section": "Operations"},
        ],
        "count": 2,
    }


# ---------------------------------------------------------------------------
# Unit tests: _normalize_incident_number
# ---------------------------------------------------------------------------


class TestNormalizeIncidentNumber:
    def test_strips_dash(self):
        assert _normalize_incident_number("26-001980") == "26001980"

    def test_no_dash(self):
        assert _normalize_incident_number("26001980") == "26001980"

    def test_neris_format(self):
        assert _normalize_incident_number("26SJ0020") == "26SJ0020"

    def test_empty_string(self):
        assert _normalize_incident_number("") == ""


# ---------------------------------------------------------------------------
# Unit tests: get_dashboard with all helpers mocked
# ---------------------------------------------------------------------------


class TestGetDashboard:
    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_happy_path(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        sample_calls,
        sample_incident,
        schedule_result,
    ):
        mock_calls.return_value = sample_calls
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {
            "26-001678": {
                "source": "local",
                "status": "in_progress",
                "completeness": sample_incident.completeness(),
                "incident_id": "inc-uuid-1",
            },
        }
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        assert result["user"]["email"] == "ff@sjifire.org"
        assert result["user"]["is_officer"] is False
        assert result["on_duty"]["platoon"] == "A"
        assert result["call_count"] == 2

        # First call has a local report
        call_1 = result["recent_calls"][0]
        assert call_1["dispatch_id"] == "26-001678"
        assert call_1["report"] is not None
        assert call_1["report"]["source"] == "local"
        assert call_1["report"]["status"] == "in_progress"
        assert call_1["report"]["completeness"]["filled"] == 3

        # Second call has no report
        call_2 = result["recent_calls"][1]
        assert call_2["dispatch_id"] == "26-001650"
        assert call_2["report"] is None

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_no_matching_report(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        sample_calls,
        schedule_result,
    ):
        mock_calls.return_value = sample_calls
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        assert all(c["report"] is None for c in result["recent_calls"])

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_schedule_failure_partial_result(
        self, mock_calls, mock_incidents, mock_schedule, mock_neris, regular_user, sample_calls
    ):
        """If schedule fails, other sections still work."""
        mock_calls.return_value = sample_calls
        mock_schedule.side_effect = RuntimeError("Aladtec down")
        mock_incidents.return_value = {}
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        assert "error" in result["on_duty"]
        assert result["call_count"] == 2
        assert len(result["recent_calls"]) == 2

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_dispatch_failure_partial_result(
        self, mock_calls, mock_incidents, mock_schedule, mock_neris, regular_user, schedule_result
    ):
        """If dispatch fails, other sections still work."""
        mock_calls.side_effect = RuntimeError("Cosmos down")
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        assert "error" in result["recent_calls"]
        assert result["call_count"] == 0
        assert result["on_duty"]["platoon"] == "A"

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_incidents_failure_calls_still_work(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        sample_calls,
        schedule_result,
    ):
        """If incidents fail, calls show with report=None."""
        mock_calls.return_value = sample_calls
        mock_schedule.return_value = schedule_result
        mock_incidents.side_effect = RuntimeError("Incidents down")
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        assert result["call_count"] == 2
        assert all(c["report"] is None for c in result["recent_calls"])

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_officer_sees_all_incidents(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        officer_user,
        sample_calls,
        schedule_result,
    ):
        mock_calls.return_value = sample_calls
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        assert result["user"]["is_officer"] is True
        mock_incidents.assert_called_once_with("chief@sjifire.org", True)

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_regular_user_limited_incidents(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        sample_calls,
        schedule_result,
    ):
        mock_calls.return_value = sample_calls
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        assert result["user"]["is_officer"] is False
        mock_incidents.assert_called_once_with("ff@sjifire.org", False)

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_completeness_in_report(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        sample_calls,
        schedule_result,
    ):
        """Completeness dict is included in the report info."""
        mock_calls.return_value = [sample_calls[0]]
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {
            "26-001678": {
                "source": "local",
                "status": "draft",
                "completeness": {"filled": 2, "total": 5, "sections": {}},
                "incident_id": "inc-1",
            },
        }
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        report = result["recent_calls"][0]["report"]
        assert report["completeness"]["filled"] == 2
        assert report["completeness"]["total"] == 5


# ---------------------------------------------------------------------------
# Unit tests: NERIS cross-referencing
# ---------------------------------------------------------------------------


class TestNerisCrossReference:
    """Unit tests for NERIS report matching in get_dashboard."""

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_neris_report_matched_by_normalized_id(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        schedule_result,
    ):
        """Dispatch 26-001980 matches NERIS 26001980 (dash stripped)."""
        mock_calls.return_value = [
            DispatchCallDocument(
                id="call-1",
                year="2026",
                long_term_call_id="26-001980",
                nature="Accident-Injury",
                address="200 Spring St",
                agency_code="SJF",
                time_reported=datetime(2026, 2, 8, 22, 0, tzinfo=UTC),
                is_completed=True,
            ),
        ]
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}  # No local incidents
        neris_summary = {
            "source": "neris",
            "neris_id": "FD53055879|26001980|123",
            "incident_number": "26001980",
            "status": "PENDING_APPROVAL",
            "incident_type": "MEDICAL||INJURY||MOTOR_VEHICLE_COLLISION",
            "call_create": "2026-02-09T06:07:17+00:00",
        }
        mock_neris.return_value = {
            "lookup": {"26001980": neris_summary},
            "reports": [neris_summary],
        }

        result = await get_dashboard()

        # NERIS matched the dispatch call — shows on the call, not duplicated
        assert result["call_count"] == 1
        report = result["recent_calls"][0]["report"]
        assert report is not None
        assert report["source"] == "neris"
        assert report["status"] == "PENDING_APPROVAL"
        assert report["neris_id"] == "FD53055879|26001980|123"

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_local_incident_takes_priority_over_neris(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        schedule_result,
    ):
        """Local draft takes priority when both local and NERIS reports exist."""
        mock_calls.return_value = [
            DispatchCallDocument(
                id="call-1",
                year="2026",
                long_term_call_id="26-001678",
                nature="Medical Aid",
                address="200 Spring St",
                agency_code="SJF",
                time_reported=datetime(2026, 2, 12, 14, 30, tzinfo=UTC),
                is_completed=True,
            ),
        ]
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {
            "26-001678": {
                "source": "local",
                "status": "in_progress",
                "completeness": {"filled": 3, "total": 5},
                "incident_id": "local-id",
            },
        }
        mock_neris.return_value = {
            "lookup": {
                "26001678": {
                    "source": "neris",
                    "status": "APPROVED",
                    "neris_id": "FD53055879|26001678|999",
                },
            },
            "reports": [],
        }

        result = await get_dashboard()

        # Local takes priority
        report = result["recent_calls"][0]["report"]
        assert report["source"] == "local"
        assert report["status"] == "in_progress"
        # NERIS entry consumed — not duplicated as a separate entry
        assert result["call_count"] == 1

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_neris_failure_does_not_block_dashboard(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        sample_calls,
        schedule_result,
    ):
        """If NERIS API fails, other sections still work."""
        mock_calls.return_value = sample_calls
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        mock_neris.side_effect = RuntimeError("NERIS API timeout")

        result = await get_dashboard()

        assert result["call_count"] == 2
        assert all(c["report"] is None for c in result["recent_calls"])

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_unmatched_neris_reports_appended_to_unified_list(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        schedule_result,
    ):
        """NERIS reports not matching any dispatch call appear as their own entries."""
        mock_calls.return_value = []
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        neris_a = {
            "source": "neris",
            "neris_id": "FD53055879|26001980|123",
            "incident_number": "26001980",
            "status": "PENDING_APPROVAL",
            "incident_type": "MEDICAL||INJURY||MVC",
            "call_create": "2026-02-09T06:07:17+00:00",
        }
        neris_b = {
            "source": "neris",
            "neris_id": "FD53055879|26SJ0020|456",
            "incident_number": "26SJ0020",
            "status": "APPROVED",
            "incident_type": "PUBSERV||ALARMS_NONMED||FIRE_ALARM",
            "call_create": "2026-02-07T09:45:54+00:00",
        }
        mock_neris.return_value = {
            "lookup": {"26001980": neris_a, "26SJ0020": neris_b},
            "reports": [neris_a, neris_b],
        }

        result = await get_dashboard()

        # Both appear as entries in the unified list
        assert result["call_count"] == 2
        ids = {c["dispatch_id"] for c in result["recent_calls"]}
        assert "26001980" in ids
        assert "26SJ0020" in ids
        # Each has a NERIS report attached
        for entry in result["recent_calls"]:
            assert entry["report"] is not None
            assert entry["report"]["source"] == "neris"

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_unmatched_neris_appended_after_dispatch_calls(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        schedule_result,
    ):
        """Unmatched NERIS reports appear after dispatch calls in the unified list."""
        mock_calls.return_value = [
            DispatchCallDocument(
                id="call-1",
                year="2026",
                long_term_call_id="26-001678",
                nature="Medical Aid",
                address="200 Spring St",
                agency_code="SJF",
                time_reported=datetime(2026, 2, 12, 14, 30, tzinfo=UTC),
                is_completed=True,
            ),
        ]
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        # NERIS report with system-assigned ID (won't match dispatch call)
        neris_entry = {
            "source": "neris",
            "neris_id": "FD53055879|1770796348|999",
            "incident_number": "1770796348",
            "status": "PENDING_APPROVAL",
            "incident_type": "MEDICAL",
            "call_create": "2026-02-10T10:00:00+00:00",
        }
        mock_neris.return_value = {
            "lookup": {"1770796348": neris_entry},
            "reports": [neris_entry],
        }

        result = await get_dashboard()

        # Dispatch call has no match
        assert result["recent_calls"][0]["dispatch_id"] == "26-001678"
        assert result["recent_calls"][0]["report"] is None
        # Unmatched NERIS report appended as its own entry
        assert result["call_count"] == 2
        neris_call = result["recent_calls"][1]
        assert neris_call["dispatch_id"] == "1770796348"
        assert neris_call["report"]["source"] == "neris"
        assert neris_call["address"] is None


# ---------------------------------------------------------------------------
# Unit tests: _list_neris_reports
# ---------------------------------------------------------------------------


class TestListNerisReports:
    """Unit tests for _list_neris_reports (blocking NERIS API call)."""

    @patch("sjifire.mcp.dashboard._list_neris_reports")
    async def test_builds_lookup_by_normalized_number(self, mock_list):
        from sjifire.mcp.dashboard import _fetch_neris_reports

        mock_list.return_value = {
            "lookup": {"26001980": {"source": "neris", "status": "APPROVED"}},
            "reports": [{"incident_number": "26001980"}],
        }

        result = await _fetch_neris_reports()

        assert "26001980" in result["lookup"]
        assert len(result["reports"]) == 1


# ---------------------------------------------------------------------------
# Unit tests: _fetch_incidents
# ---------------------------------------------------------------------------


class TestFetchIncidents:
    """Unit tests: _fetch_incidents builds correct lookup from store."""

    @patch("sjifire.mcp.dashboard.IncidentStore")
    async def test_officer_queries_all(self, mock_store_cls, officer_user, sample_incident):
        mock_store = AsyncMock()
        mock_store.list_by_status = AsyncMock(return_value=[sample_incident])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_incidents("chief@sjifire.org", is_officer=True)

        assert "26-001678" in result
        assert result["26-001678"]["status"] == "in_progress"
        assert result["26-001678"]["source"] == "local"
        assert result["26-001678"]["incident_id"] == "inc-uuid-1"
        mock_store.list_by_status.assert_called_once_with(exclude_status="submitted", max_items=50)

    @patch("sjifire.mcp.dashboard.IncidentStore")
    async def test_regular_user_queries_own(self, mock_store_cls, regular_user, sample_incident):
        mock_store = AsyncMock()
        mock_store.list_for_user = AsyncMock(return_value=[sample_incident])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_incidents("ff@sjifire.org", is_officer=False)

        assert "26-001678" in result
        mock_store.list_for_user.assert_called_once_with(
            "ff@sjifire.org", exclude_status="submitted", max_items=50
        )

    @patch("sjifire.mcp.dashboard.IncidentStore")
    async def test_completeness_included_in_lookup(
        self, mock_store_cls, regular_user, sample_incident
    ):
        mock_store = AsyncMock()
        mock_store.list_for_user = AsyncMock(return_value=[sample_incident])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_incidents("ff@sjifire.org", is_officer=False)

        comp = result["26-001678"]["completeness"]
        assert comp["filled"] == 3  # incident_type + address + crew
        assert comp["total"] == 5

    @patch("sjifire.mcp.dashboard.IncidentStore")
    async def test_empty_store_returns_empty_lookup(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.list_for_user = AsyncMock(return_value=[])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_incidents("ff@sjifire.org", is_officer=False)
        assert result == {}


# ---------------------------------------------------------------------------
# Unit tests: _fetch_recent_calls
# ---------------------------------------------------------------------------


class TestFetchRecentCalls:
    """Unit tests: _fetch_recent_calls reads from DispatchStore."""

    async def test_returns_calls_from_store(self, sample_calls):
        async with DispatchStore() as store:
            for call in sample_calls:
                await store.upsert(call)

        result = await _fetch_recent_calls()
        assert len(result) == 2
        # Most recent first
        assert result[0].long_term_call_id == "26-001678"

    async def test_empty_store_returns_empty(self):
        result = await _fetch_recent_calls()
        assert result == []


# ---------------------------------------------------------------------------
# Integration tests — real in-memory stores, schedule + NERIS mocked
# ---------------------------------------------------------------------------


class TestDashboardIntegration:
    """Integration tests using real in-memory DispatchStore and IncidentStore.

    Schedule and NERIS are mocked because they require external credentials,
    but dispatch and incident stores run against the in-memory backend
    to verify cross-referencing works end-to-end.
    """

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_calls_matched_to_incidents(
        self,
        mock_schedule,
        mock_neris,
        regular_user,
        sample_calls,
        sample_incident,
        schedule_result,
    ):
        """Dispatch calls are cross-referenced with incident reports via incident_number."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        async with DispatchStore() as store:
            for call in sample_calls:
                await store.upsert(call)
        async with IncidentStore() as store:
            await store.create(sample_incident)

        result = await get_dashboard()

        assert result["call_count"] == 2

        call_1 = result["recent_calls"][0]
        assert call_1["dispatch_id"] == "26-001678"
        assert call_1["report"] is not None
        assert call_1["report"]["source"] == "local"
        assert call_1["report"]["status"] == "in_progress"
        assert call_1["report"]["incident_id"] == "inc-uuid-1"
        assert call_1["report"]["completeness"]["filled"] == 3

        call_2 = result["recent_calls"][1]
        assert call_2["dispatch_id"] == "26-001650"
        assert call_2["report"] is None

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_calls_without_any_incidents(
        self, mock_schedule, mock_neris, regular_user, sample_calls, schedule_result
    ):
        """All calls show report=None when no incidents exist."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        async with DispatchStore() as store:
            for call in sample_calls:
                await store.upsert(call)

        result = await get_dashboard()

        assert result["call_count"] == 2
        assert all(c["report"] is None for c in result["recent_calls"])

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_submitted_incidents_excluded(
        self, mock_schedule, mock_neris, regular_user, schedule_result
    ):
        """Submitted incidents don't appear on the dashboard."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        call_doc = DispatchCallDocument(
            id="call-sub",
            year="2026",
            long_term_call_id="26-001000",
            nature="Medical Aid",
            address="300 Spring St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 10, 10, 0, tzinfo=UTC),
            is_completed=True,
        )
        incident = IncidentDocument(
            id="inc-submitted",
            station="S31",
            incident_number="26-001000",
            incident_date=date(2026, 2, 10),
            created_by="ff@sjifire.org",
            status="submitted",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(incident)

        result = await get_dashboard()

        assert result["call_count"] == 1
        assert result["recent_calls"][0]["report"] is None

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_multiple_incidents_mapped_correctly(
        self, mock_schedule, mock_neris, officer_user, schedule_result
    ):
        """Each dispatch call maps to its own incident by incident_number."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        calls = [
            DispatchCallDocument(
                id=f"call-{i}",
                year="2026",
                long_term_call_id=f"26-00{i + 1:04d}",
                nature=["Medical Aid", "Fire Alarm"][i],
                address=["200 Spring St", "100 Guard St"][i],
                agency_code="SJF",
                time_reported=datetime(2026, 2, 12 - i, 10, 0, tzinfo=UTC),
                is_completed=True,
            )
            for i in range(2)
        ]
        incidents = [
            IncidentDocument(
                id=f"inc-{i}",
                station="S31",
                incident_number=f"26-00{i + 1:04d}",
                incident_date=date(2026, 2, 12 - i),
                incident_type="MEDICAL" if i == 0 else None,
                created_by="chief@sjifire.org",
                status=["in_progress", "draft"][i],
            )
            for i in range(2)
        ]

        async with DispatchStore() as store:
            for c in calls:
                await store.upsert(c)
        async with IncidentStore() as store:
            for inc in incidents:
                await store.create(inc)

        result = await get_dashboard()

        assert result["call_count"] == 2
        for call_entry in result["recent_calls"]:
            assert call_entry["report"] is not None
            dispatch_id = call_entry["dispatch_id"]
            if dispatch_id == "26-000001":
                assert call_entry["report"]["status"] == "in_progress"
                assert call_entry["report"]["incident_id"] == "inc-0"
            elif dispatch_id == "26-000002":
                assert call_entry["report"]["status"] == "draft"
                assert call_entry["report"]["incident_id"] == "inc-1"

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_regular_user_only_sees_own_incidents(
        self, mock_schedule, mock_neris, regular_user, schedule_result
    ):
        """Regular user's dashboard only cross-references their own incidents."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        call_doc = DispatchCallDocument(
            id="call-own",
            year="2026",
            long_term_call_id="26-002000",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 12, 10, 0, tzinfo=UTC),
            is_completed=True,
        )
        other_incident = IncidentDocument(
            id="inc-other",
            station="S31",
            incident_number="26-002000",
            incident_date=date(2026, 2, 12),
            created_by="chief@sjifire.org",
            status="in_progress",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(other_incident)

        result = await get_dashboard()

        assert result["call_count"] == 1
        assert result["recent_calls"][0]["report"] is None

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_officer_sees_all_incidents_in_dashboard(
        self, mock_schedule, mock_neris, officer_user, schedule_result
    ):
        """Officer's dashboard shows report status for all incidents."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        call_doc = DispatchCallDocument(
            id="call-officer",
            year="2026",
            long_term_call_id="26-003000",
            nature="Fire Alarm",
            address="100 Guard St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 12, 10, 0, tzinfo=UTC),
            is_completed=True,
        )
        incident = IncidentDocument(
            id="inc-other-officer",
            station="S31",
            incident_number="26-003000",
            incident_date=date(2026, 2, 12),
            created_by="ff@sjifire.org",
            status="draft",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(incident)

        result = await get_dashboard()

        assert result["call_count"] == 1
        assert result["recent_calls"][0]["report"] is not None
        assert result["recent_calls"][0]["report"]["status"] == "draft"

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_completeness_reflects_actual_data(
        self, mock_schedule, mock_neris, regular_user, schedule_result
    ):
        """Completeness score accurately reflects filled fields through the full pipeline."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        call_doc = DispatchCallDocument(
            id="call-comp",
            year="2026",
            long_term_call_id="26-004000",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 12, 10, 0, tzinfo=UTC),
            is_completed=True,
        )
        incident = IncidentDocument(
            id="inc-full",
            station="S31",
            incident_number="26-004000",
            incident_date=date(2026, 2, 12),
            incident_type="MEDICAL",
            address="200 Spring St",
            crew=[CrewAssignment(name="John", email="ff@sjifire.org")],
            narratives=Narratives(outcome="Patient transported"),
            timestamps={"dispatch": "2026-02-12T10:00:00"},
            created_by="ff@sjifire.org",
            status="ready_review",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(incident)

        result = await get_dashboard()

        report = result["recent_calls"][0]["report"]
        assert report["status"] == "ready_review"
        assert report["completeness"]["filled"] == 5
        assert report["completeness"]["total"] == 5
        assert all(report["completeness"]["sections"].values())

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_empty_stores_return_valid_structure(
        self, mock_schedule, mock_neris, regular_user, schedule_result
    ):
        """Dashboard returns valid structure even with no data."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        assert "timestamp" in result
        assert result["user"]["email"] == "ff@sjifire.org"
        assert result["on_duty"]["platoon"] == "A"
        assert result["recent_calls"] == []
        assert result["call_count"] == 0

    @patch("sjifire.mcp.dashboard._fetch_neris_reports", new_callable=AsyncMock)
    @patch("sjifire.mcp.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_neris_matches_dispatch_in_integration(
        self, mock_schedule, mock_neris, regular_user, schedule_result
    ):
        """NERIS reports match dispatch calls via normalized ID through real stores."""
        mock_schedule.return_value = schedule_result
        neris_summary = {
            "source": "neris",
            "neris_id": "FD53055879|26001678|789",
            "incident_number": "26001678",
            "status": "APPROVED",
            "incident_type": "PUBSERV||ALARMS_NONMED||FIRE_ALARM",
            "call_create": "2026-02-02T18:51:21+00:00",
        }
        mock_neris.return_value = {
            "lookup": {"26001678": neris_summary},
            "reports": [neris_summary],
        }

        call_doc = DispatchCallDocument(
            id="call-neris-match",
            year="2026",
            long_term_call_id="26-001678",
            nature="Fire-Alarm",
            address="100 Guard St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 2, 18, 51, tzinfo=UTC),
            is_completed=True,
        )
        async with DispatchStore() as store:
            await store.upsert(call_doc)

        result = await get_dashboard()

        # NERIS matched the dispatch call — shows on the call, not duplicated
        assert result["call_count"] == 1
        report = result["recent_calls"][0]["report"]
        assert report is not None
        assert report["source"] == "neris"
        assert report["status"] == "APPROVED"
