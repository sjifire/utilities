"""Tests for operations dashboard."""

import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import sjifire.ops.dashboard as dashboard_mod
from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.dashboard import (
    _build_template_context,
    _call_first_seen,
    _fetch_incidents,
    _fetch_kiosk_data,
    _fetch_open_docs_cached,
    _fetch_recent_calls,
    _normalize_incident_number,
    _open_calls_ttl,
    get_dashboard,
    get_open_calls_cached,
)
from sjifire.ops.dispatch.models import DispatchCallDocument
from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.incidents.models import (
    IncidentDocument,
    PersonnelAssignment,
    UnitAssignment,
)
from sjifire.ops.incidents.store import IncidentStore

# Shared NERIS return value for tests that don't care about NERIS
_EMPTY_NERIS = {"lookup": {}, "reports": []}


@pytest.fixture(autouse=True)
def _env():
    """Ensure dev mode and set editor group for all tests."""
    import sjifire.ops.auth

    sjifire.ops.auth._EDITOR_GROUP_ID = None
    with patch.dict(
        os.environ,
        {
            "ENTRA_MCP_API_CLIENT_ID": "",
            "COSMOS_ENDPOINT": "",
            "COSMOS_KEY": "",
            "ENTRA_REPORT_EDITORS_GROUP_ID": "officer-group",
        },
        clear=False,
    ):
        set_current_user(None)
        yield
    sjifire.ops.auth._EDITOR_GROUP_ID = None
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
        incident_number="26-001678",
        incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
        incident_type="MEDICAL",
        address="200 Spring St",
        units=[
            UnitAssignment(
                unit_id="E31",
                personnel=[PersonnelAssignment(name="John", email="ff@sjifire.org")],
            )
        ],
        created_by="ff@sjifire.org",
        status="in_progress",
        station="S31",
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
# Unit tests: _build_template_context
# ---------------------------------------------------------------------------


class TestBuildTemplateContext:
    """Verify _build_template_context exposes is_editor and user_email."""

    def test_editor_fields_present(self, schedule_result):
        dashboard_data = {
            "timestamp": datetime(2026, 2, 20, 12, 0, tzinfo=UTC).isoformat(),
            "user": {"email": "chief@sjifire.org", "name": "Chief", "is_editor": True},
            "on_duty": schedule_result,
            "recent_calls": [],
        }
        ctx = _build_template_context(dashboard_data, {"incidents": []})

        assert ctx["is_editor"] is True
        assert ctx["user_email"] == "chief@sjifire.org"

    def test_non_editor_fields(self, schedule_result):
        dashboard_data = {
            "timestamp": datetime(2026, 2, 20, 12, 0, tzinfo=UTC).isoformat(),
            "user": {"email": "ff@sjifire.org", "name": "FF", "is_editor": False},
            "on_duty": schedule_result,
            "recent_calls": [],
        }
        ctx = _build_template_context(dashboard_data, {"incidents": []})

        assert ctx["is_editor"] is False
        assert ctx["user_email"] == "ff@sjifire.org"


# ---------------------------------------------------------------------------
# Unit tests: /reports redirect
# ---------------------------------------------------------------------------


class TestReportsRedirect:
    """The /reports route should redirect to /dashboard#reports."""

    async def test_redirects_to_dashboard_reports_tab(self):
        from sjifire.ops.server import _reports_redirect

        resp = await _reports_redirect(None)

        assert resp.status_code == 307
        assert resp.headers["location"] == "/dashboard#reports"


# ---------------------------------------------------------------------------
# Unit tests: get_dashboard with all helpers mocked
# ---------------------------------------------------------------------------


class TestGetDashboard:
    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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
        assert result["user"]["is_editor"] is False
        assert result["on_duty"]["platoon"] == "A"
        assert result["call_count"] == 2

        # First call has a local report
        call_1 = result["recent_calls"][0]
        assert call_1["dispatch_id"] == "26-001678"
        assert call_1["report"] is not None
        assert call_1["report"]["source"] == "local"
        assert call_1["report"]["status"] == "in_progress"
        assert call_1["report"]["completeness"]["filled"] == 5

        # Second call has no report
        call_2 = result["recent_calls"][1]
        assert call_2["dispatch_id"] == "26-001650"
        assert call_2["report"] is None

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

        assert result["user"]["is_editor"] is True
        mock_incidents.assert_called_once_with("chief@sjifire.org", True)

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

        assert result["user"]["is_editor"] is False
        mock_incidents.assert_called_once_with("ff@sjifire.org", False)

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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
                "completeness": {"filled": 2, "total": 7, "sections": {}},
                "incident_id": "inc-1",
            },
        }
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()

        report = result["recent_calls"][0]["report"]
        assert report["completeness"]["filled"] == 2
        assert report["completeness"]["total"] == 7


# ---------------------------------------------------------------------------
# Unit tests: NERIS cross-referencing
# ---------------------------------------------------------------------------


class TestNerisCrossReference:
    """Unit tests for NERIS report matching in get_dashboard."""

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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
                "completeness": {"filled": 3, "total": 7},
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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

        # NERIS-only entries appear in recent_calls but call_count
        # only reflects actual dispatch calls (source of truth).
        assert result["call_count"] == 0
        ids = {c["dispatch_id"] for c in result["recent_calls"]}
        assert "26001980" in ids
        assert "26SJ0020" in ids
        # Each has a NERIS report attached
        for entry in result["recent_calls"]:
            assert entry["report"] is not None
            assert entry["report"]["source"] == "neris"

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
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
        # call_count reflects dispatch calls only (source of truth)
        assert result["call_count"] == 1
        neris_call = result["recent_calls"][1]
        assert neris_call["dispatch_id"] == "1770796348"
        assert neris_call["report"]["source"] == "neris"
        assert neris_call["address"] is None


# ---------------------------------------------------------------------------
# Unit tests: _fetch_incidents
# ---------------------------------------------------------------------------


class TestFetchIncidents:
    """Unit tests: _fetch_incidents builds correct lookup from store."""

    @patch("sjifire.ops.dashboard.IncidentStore")
    async def test_officer_queries_all(self, mock_store_cls, officer_user, sample_incident):
        mock_store = AsyncMock()
        mock_store.list_by_status = AsyncMock(return_value=[sample_incident])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_incidents("chief@sjifire.org", is_editor=True)

        assert "26-001678" in result
        assert result["26-001678"]["status"] == "in_progress"
        assert result["26-001678"]["source"] == "local"
        assert result["26-001678"]["incident_id"] == "inc-uuid-1"
        mock_store.list_by_status.assert_called_once_with(max_items=50)

    @patch("sjifire.ops.dashboard.IncidentStore")
    async def test_regular_user_queries_own(self, mock_store_cls, regular_user, sample_incident):
        mock_store = AsyncMock()
        mock_store.list_for_user = AsyncMock(return_value=[sample_incident])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_incidents("ff@sjifire.org", is_editor=False)

        assert "26-001678" in result
        mock_store.list_for_user.assert_called_once_with("ff@sjifire.org", max_items=50)

    @patch("sjifire.ops.dashboard.IncidentStore")
    async def test_completeness_included_in_lookup(
        self, mock_store_cls, regular_user, sample_incident
    ):
        mock_store = AsyncMock()
        mock_store.list_for_user = AsyncMock(return_value=[sample_incident])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_incidents("ff@sjifire.org", is_editor=False)

        comp = result["26-001678"]["completeness"]
        assert comp["filled"] == 5  # station + incident_type + address + units + personnel
        assert comp["total"] == 8

    @patch("sjifire.ops.dashboard.IncidentStore")
    async def test_empty_store_returns_empty_lookup(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.list_for_user = AsyncMock(return_value=[])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_incidents("ff@sjifire.org", is_editor=False)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
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
        assert call_1["report"]["completeness"]["filled"] == 5

        call_2 = result["recent_calls"][1]
        assert call_2["dispatch_id"] == "26-001650"
        assert call_2["report"] is None

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_submitted_incidents_visible_on_dashboard(
        self, mock_schedule, mock_neris, regular_user, schedule_result
    ):
        """Submitted incidents appear on the dashboard with locked status."""
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
            incident_number="26-001000",
            incident_datetime=datetime(2026, 2, 10, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="submitted",
            station="S31",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(incident)

        result = await get_dashboard()

        assert result["call_count"] == 1
        report = result["recent_calls"][0]["report"]
        assert report is not None
        assert report["source"] == "local"
        assert report["status"] == "submitted"

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
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
                incident_number=f"26-00{i + 1:04d}",
                incident_datetime=datetime(2026, 2, 12 - i, tzinfo=UTC),
                incident_type="MEDICAL" if i == 0 else None,
                created_by="chief@sjifire.org",
                status=["in_progress", "draft"][i],
                station="S31",
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
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
            incident_number="26-002000",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="chief@sjifire.org",
            status="in_progress",
            station="S31",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(other_incident)

        result = await get_dashboard()

        assert result["call_count"] == 1
        assert result["recent_calls"][0]["report"] is None

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
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
            incident_number="26-003000",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="draft",
            station="S31",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(incident)

        result = await get_dashboard()

        assert result["call_count"] == 1
        assert result["recent_calls"][0]["report"] is not None
        assert result["recent_calls"][0]["report"]["status"] == "draft"

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
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
            incident_number="26-004000",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            incident_type="MEDICAL",
            address="200 Spring St",
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[PersonnelAssignment(name="John", email="ff@sjifire.org")],
                )
            ],
            narrative="Patient transported",
            timestamps={"dispatch": "2026-02-12T10:00:00"},
            created_by="ff@sjifire.org",
            status="ready_review",
            station="S31",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(incident)

        result = await get_dashboard()

        report = result["recent_calls"][0]["report"]
        assert report["status"] == "ready_review"
        assert (
            report["completeness"]["filled"] == 7
        )  # station + type + address + units + personnel + narrative + timestamps
        assert report["completeness"]["total"] == 8

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
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

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
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


# ---------------------------------------------------------------------------
# Unit tests: shared open-calls cache
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_open_calls_cache():
    """Reset the shared open-calls cache between tests."""
    dashboard_mod._open_docs_cache = None
    dashboard_mod._open_docs_ts = 0
    dashboard_mod._kiosk_cache = None
    dashboard_mod._kiosk_cache_ts = 0
    _call_first_seen.clear()
    yield
    dashboard_mod._open_docs_cache = None
    dashboard_mod._open_docs_ts = 0
    dashboard_mod._kiosk_cache = None
    dashboard_mod._kiosk_cache_ts = 0
    _call_first_seen.clear()


class TestOpenCallsTTL:
    """Unit tests for _open_calls_ttl adaptive TTL logic."""

    def test_no_active_calls_returns_5s(self):
        _call_first_seen.clear()
        assert _open_calls_ttl() == 5.0

    def test_recent_call_returns_2s(self):
        _call_first_seen["26-001234"] = time.monotonic()
        assert _open_calls_ttl() == 2.0

    def test_old_call_returns_5s(self):
        _call_first_seen["26-001234"] = time.monotonic() - 301  # > 5 min
        assert _open_calls_ttl() == 5.0

    def test_mix_of_old_and_new_returns_2s(self):
        _call_first_seen["26-001234"] = time.monotonic() - 400  # old
        _call_first_seen["26-005678"] = time.monotonic()  # new
        assert _open_calls_ttl() == 2.0


class TestFetchOpenDocsCached:
    """Unit tests for the shared open-calls cache."""

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_fetches_and_caches_docs(self, mock_store_cls):
        doc = DispatchCallDocument(
            id="call-1",
            year="2026",
            long_term_call_id="26-001678",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 12, 14, 30, tzinfo=UTC),
        )
        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(return_value=[doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_open_docs_cached()

        assert len(result) == 1
        assert result[0].long_term_call_id == "26-001678"
        mock_store.fetch_open.assert_called_once()

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_cache_hit_within_ttl(self, mock_store_cls):
        doc = DispatchCallDocument(
            id="call-1",
            year="2026",
            long_term_call_id="26-001678",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
        )
        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(return_value=[doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # First call populates cache
        await _fetch_open_docs_cached()
        # Second call should hit cache
        await _fetch_open_docs_cached()

        mock_store.fetch_open.assert_called_once()

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_tracks_first_seen(self, mock_store_cls):
        doc = DispatchCallDocument(
            id="call-1",
            year="2026",
            long_term_call_id="26-001678",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
        )
        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(return_value=[doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        await _fetch_open_docs_cached()

        assert "26-001678" in _call_first_seen

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_clears_old_calls_from_tracking(self, mock_store_cls):
        _call_first_seen["26-OLD"] = time.monotonic() - 600

        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(return_value=[])  # no open calls
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        await _fetch_open_docs_cached()

        assert "26-OLD" not in _call_first_seen

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_returns_stale_cache_on_error(self, mock_store_cls):
        doc = DispatchCallDocument(
            id="call-1",
            year="2026",
            long_term_call_id="26-001678",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
        )
        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(return_value=[doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # Populate cache
        await _fetch_open_docs_cached()

        # Expire cache, make next fetch fail
        dashboard_mod._open_docs_ts = 0
        mock_store.fetch_open = AsyncMock(side_effect=RuntimeError("iSpyFire down"))

        result = await _fetch_open_docs_cached()

        assert len(result) == 1
        assert result[0].long_term_call_id == "26-001678"

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_returns_empty_list_on_first_error(self, mock_store_cls):
        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(side_effect=RuntimeError("iSpyFire down"))
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await _fetch_open_docs_cached()

        assert result == []


class TestGetOpenCallsCached:
    """Unit tests for the nav bar open-calls endpoint."""

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_formats_docs_correctly(self, mock_store_cls):
        doc = DispatchCallDocument(
            id="call-1",
            year="2026",
            long_term_call_id="26-001678",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
        )
        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(return_value=[doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_open_calls_cached()

        assert result["open_calls"] == 1
        assert result["updated_time"]  # non-empty string
        assert len(result["calls"]) == 1
        assert result["calls"][0]["dispatch_id"] == "26-001678"
        assert result["calls"][0]["nature"] == "Medical Aid"
        assert result["calls"][0]["address"] == "200 Spring St"

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_updated_time_is_utc_iso(self, mock_store_cls):
        """updated_time must be a UTC ISO 8601 timestamp, not display-formatted."""
        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(return_value=[])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_open_calls_cached()

        ts = result["updated_time"]
        # Must parse as ISO 8601
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None or "+" in ts or ts.endswith("Z")

    @patch("sjifire.ops.dashboard.DispatchStore")
    async def test_no_calls_returns_zero(self, mock_store_cls):
        mock_store = AsyncMock()
        mock_store.fetch_open = AsyncMock(return_value=[])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_open_calls_cached()

        assert result["open_calls"] == 0
        assert result["calls"] == []


# ---------------------------------------------------------------------------
# Unit tests: kiosk recently completed (Cosmos DB)
# ---------------------------------------------------------------------------


class TestKioskRecentlyCompleted:
    """Unit tests for kiosk archived calls sourced from Cosmos DB."""

    @patch("sjifire.ops.dashboard._fetch_schedule_for_kiosk", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_open_calls_enriched", new_callable=AsyncMock)
    async def test_completed_call_appears_as_archived(self, mock_open, mock_schedule):
        """A completed call from Cosmos appears with archived=True when no active calls."""
        mock_schedule.return_value = {"crew": [], "platoon": "A"}
        mock_open.return_value = []

        now = datetime.now(UTC)

        # Store a recently completed call with CAD comments, responders, and analysis
        doc = DispatchCallDocument(
            id="call-archived",
            year="2026",
            long_term_call_id="26-002000",
            nature="Fire Alarm",
            address="100 Guard St",
            agency_code="SJF",
            cad_comments="Alarm sounding, keyholder notified",
            responding_units="E31,BN31",
            responder_details=[
                {
                    "unit_number": "E31",
                    "agency_code": "SJF",
                    "status": "Enroute",
                    "time_of_status_change": "2026-02-17T09:05:00",
                    "radio_log": "",
                },
            ],
            time_reported=now - timedelta(hours=3),
            is_completed=True,
            stored_at=now - timedelta(hours=2),
        )
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _fetch_kiosk_data()

        archived = [c for c in result["calls"] if c.get("archived")]
        assert len(archived) == 1
        assert archived[0]["dispatch_id"] == "26-002000"
        assert archived[0]["nature"] == "Fire Alarm"
        assert archived[0]["archived"] is True
        assert "completed_at" in archived[0]
        # Full call data available (CAD notes, responders, analysis)
        assert archived[0]["cad_comments"] == "Alarm sounding, keyholder notified"
        assert archived[0]["responding_units"] == "E31,BN31"
        assert "analysis" in archived[0]
        # Responder details normalized with unit_call_sign for kiosk template
        assert len(archived[0]["responder_details"]) == 1
        assert archived[0]["responder_details"][0]["unit_call_sign"] == "E31"

    @patch("sjifire.ops.dashboard._fetch_schedule_for_kiosk", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_open_calls_enriched", new_callable=AsyncMock)
    async def test_active_call_excludes_archived(self, mock_open, mock_schedule):
        """When an active call is present, archived calls are not shown."""
        mock_schedule.return_value = {"crew": [], "platoon": "A"}
        mock_open.return_value = [
            {
                "dispatch_id": "26-ACTIVE",
                "nature": "Medical Aid",
                "address": "200 Spring St",
                "severity": "low",
                "icon": "\U0001f4df",
            }
        ]

        now = datetime.now(UTC)

        # Store a recently completed call
        doc = DispatchCallDocument(
            id="call-archived",
            year="2026",
            long_term_call_id="26-002000",
            nature="Fire Alarm",
            address="100 Guard St",
            agency_code="SJF",
            time_reported=now - timedelta(hours=3),
            is_completed=True,
            stored_at=now - timedelta(hours=2),
        )
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _fetch_kiosk_data()

        # Only the active call, no archived
        assert len(result["calls"]) == 1
        assert result["calls"][0]["dispatch_id"] == "26-ACTIVE"

    @patch("sjifire.ops.dashboard._fetch_schedule_for_kiosk", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_open_calls_enriched", new_callable=AsyncMock)
    async def test_old_completed_call_excluded(self, mock_open, mock_schedule):
        """A call completed more than 12 hours ago is not shown."""
        mock_schedule.return_value = {"crew": [], "platoon": "A"}
        mock_open.return_value = []

        # Store a call with stored_at >12h ago
        doc = DispatchCallDocument(
            id="call-old",
            year="2026",
            long_term_call_id="26-001500",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 16, 10, 0, tzinfo=UTC),
            is_completed=True,
            stored_at=datetime(2026, 2, 16, 10, 30, tzinfo=UTC),
        )
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _fetch_kiosk_data()

        assert len(result["calls"]) == 0

    @patch("sjifire.ops.dashboard._fetch_schedule_for_kiosk", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_open_calls_enriched", new_callable=AsyncMock)
    async def test_open_call_in_cosmos_not_duplicated(self, mock_open, mock_schedule):
        """A call that's both open and in Cosmos is not duplicated as archived."""
        mock_schedule.return_value = {"crew": [], "platoon": "A"}
        mock_open.return_value = []

        # Store an open (not completed) call in Cosmos
        doc = DispatchCallDocument(
            id="call-open",
            year="2026",
            long_term_call_id="26-003000",
            nature="Fire Alarm",
            address="100 Guard St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 17, 15, 0, tzinfo=UTC),
            is_completed=False,
            stored_at=datetime(2026, 2, 17, 15, 0, tzinfo=UTC),
        )
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _fetch_kiosk_data()

        # Open (not completed) calls are excluded from archived
        assert len(result["calls"]) == 0


# ---------------------------------------------------------------------------
# Unit tests: locked status + finalize label (Phase 2)
# ---------------------------------------------------------------------------


class TestLockedStatusDashboard:
    """Verify submitted/approved incidents appear on dashboard with correct labels."""

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    async def test_approved_incident_visible_on_dashboard(
        self, mock_schedule, mock_neris, regular_user, schedule_result
    ):
        """Approved incidents appear on the dashboard with locked status."""
        mock_schedule.return_value = schedule_result
        mock_neris.return_value = _EMPTY_NERIS

        call_doc = DispatchCallDocument(
            id="call-appr",
            year="2026",
            long_term_call_id="26-005000",
            nature="Fire Alarm",
            address="100 Guard St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 10, 10, 0, tzinfo=UTC),
            is_completed=True,
        )
        incident = IncidentDocument(
            id="inc-approved",
            incident_number="26-005000",
            incident_datetime=datetime(2026, 2, 10, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="approved",
            station="S31",
        )

        async with DispatchStore() as store:
            await store.upsert(call_doc)
        async with IncidentStore() as store:
            await store.create(incident)

        result = await get_dashboard()

        assert result["call_count"] == 1
        report = result["recent_calls"][0]["report"]
        assert report is not None
        assert report["source"] == "local"
        assert report["status"] == "approved"

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_neris_status_propagated_to_template(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        schedule_result,
    ):
        """neris_status and is_locked are propagated to template context."""
        # Use a dispatch call that matches the NERIS report so it has an address
        call = DispatchCallDocument(
            id="call-neris-match",
            year="2026",
            long_term_call_id="26-001500",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 9, 6, 7, tzinfo=UTC),
            is_completed=True,
        )
        mock_calls.return_value = [call]
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        neris_summary = {
            "source": "neris",
            "neris_id": "FD53055879|26001500|100",
            "incident_number": "26001500",
            "status": "APPROVED",
            "incident_type": "MEDICAL",
            "call_create": "2026-02-09T06:07:17+00:00",
        }
        mock_neris.return_value = {
            "lookup": {"26001500": neris_summary},
            "reports": [neris_summary],
        }

        result = await get_dashboard()
        ctx = _build_template_context(result, {"incidents": []})

        # Find the entry by dispatch ID
        entry = [c for c in ctx["recent_calls"] if c["id"] == "26-001500"]
        assert len(entry) == 1
        assert entry[0]["neris_status"] == "APPROVED"
        assert entry[0]["report_label"] == "Finalize from NERIS"

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_pending_neris_shows_import_label(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        schedule_result,
    ):
        """Non-APPROVED NERIS records show 'Import from NERIS' label."""
        call = DispatchCallDocument(
            id="call-neris-pend",
            year="2026",
            long_term_call_id="26-001600",
            nature="Fire Alarm",
            address="100 Guard St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 10, 10, 0, tzinfo=UTC),
            is_completed=True,
        )
        mock_calls.return_value = [call]
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {}
        neris_summary = {
            "source": "neris",
            "neris_id": "FD53055879|26001600|200",
            "incident_number": "26001600",
            "status": "PENDING_APPROVAL",
            "incident_type": "FIRE",
            "call_create": "2026-02-10T10:00:00+00:00",
        }
        mock_neris.return_value = {
            "lookup": {"26001600": neris_summary},
            "reports": [neris_summary],
        }

        result = await get_dashboard()
        ctx = _build_template_context(result, {"incidents": []})

        entry = [c for c in ctx["recent_calls"] if c["id"] == "26-001600"]
        assert len(entry) == 1
        assert entry[0]["report_label"] == "Import from NERIS"

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_locked_local_shows_view_label(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        schedule_result,
    ):
        """Locked local (submitted/approved) reports show 'View Report' label."""
        call = DispatchCallDocument(
            id="call-locked",
            year="2026",
            long_term_call_id="26-006000",
            nature="Medical Aid",
            address="200 Spring St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 12, 14, 30, tzinfo=UTC),
            is_completed=True,
        )
        mock_calls.return_value = [call]
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {
            "26-006000": {
                "source": "local",
                "status": "submitted",
                "completeness": {"filled": 5, "total": 7, "sections": {}},
                "incident_id": "inc-locked",
            },
        }
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()
        ctx = _build_template_context(result, {"incidents": []})

        entry = [c for c in ctx["recent_calls"] if c["id"] == "26-006000"]
        assert len(entry) == 1
        assert entry[0]["is_locked"] is True
        assert entry[0]["report_label"] == "View Report"

    @patch("sjifire.ops.dashboard._read_neris_cache", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_schedule", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_incidents", new_callable=AsyncMock)
    @patch("sjifire.ops.dashboard._fetch_recent_calls", new_callable=AsyncMock)
    async def test_editable_local_shows_edit_label(
        self,
        mock_calls,
        mock_incidents,
        mock_schedule,
        mock_neris,
        regular_user,
        schedule_result,
    ):
        """Editable local reports show 'Edit Report' label."""
        call = DispatchCallDocument(
            id="call-edit",
            year="2026",
            long_term_call_id="26-007000",
            nature="Fire Alarm",
            address="100 Guard St",
            agency_code="SJF",
            time_reported=datetime(2026, 2, 12, 10, 0, tzinfo=UTC),
            is_completed=True,
        )
        mock_calls.return_value = [call]
        mock_schedule.return_value = schedule_result
        mock_incidents.return_value = {
            "26-007000": {
                "source": "local",
                "status": "in_progress",
                "completeness": {"filled": 3, "total": 7, "sections": {}},
                "incident_id": "inc-edit",
            },
        }
        mock_neris.return_value = _EMPTY_NERIS

        result = await get_dashboard()
        ctx = _build_template_context(result, {"incidents": []})

        entry = [c for c in ctx["recent_calls"] if c["id"] == "26-007000"]
        assert len(entry) == 1
        assert entry[0]["is_locked"] is False
        assert entry[0]["report_label"] == "Edit Report"
