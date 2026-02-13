"""Tests for MCP dispatch tools."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ispyfire.models import DispatchCall, UnitResponse
from sjifire.mcp.auth import UserContext, set_current_user
from sjifire.mcp.dispatch.models import DispatchCallDocument
from sjifire.mcp.dispatch.store import DispatchStore
from sjifire.mcp.dispatch.tools import (
    _call_to_dict,
    _lookup_in_store,
    _store_single_call,
    get_dispatch_call,
    get_dispatch_call_log,
    get_open_dispatch_calls,
    list_dispatch_calls,
    search_dispatch_calls,
)


@pytest.fixture(autouse=True)
def _dev_mode():
    """Ensure dev mode (no Entra config) so get_current_user() works."""
    with patch.dict(
        os.environ,
        {"ENTRA_MCP_API_CLIENT_ID": "", "COSMOS_ENDPOINT": "", "COSMOS_KEY": ""},
        clear=False,
    ):
        set_current_user(None)
        yield
    # Clean up in-memory store between tests
    DispatchStore._memory.clear()


@pytest.fixture
def auth_user():
    user = UserContext(email="ff@sjifire.org", name="Firefighter", user_id="user-1")
    set_current_user(user)
    return user


@pytest.fixture
def sample_call():
    return DispatchCall(
        id="call-uuid-123",
        long_term_call_id="26-001678",
        nature="Medical Aid",
        address="200 Spring St",
        agency_code="SJF",
        type="EMS",
        zone_code="Z1",
        time_reported="2026-02-12 14:30:00",
        is_completed=False,
        comments="Patient fall, possible hip injury",
        joined_responders="E31,M31",
        responder_details=[
            UnitResponse(
                unit_number="E31",
                agency_code="SJF",
                status="Dispatched",
                time_of_status_change="2026-02-12 14:30:15",
                radio_log="E31 dispatched",
            ),
        ],
        ispy_responders={"user1": "responding"},
        city="Friday Harbor",
        state="WA",
        zip_code="98250",
        geo_location="48.5343,-123.0170",
        created_timestamp=1739388600,
    )


@pytest.fixture
def completed_call():
    return DispatchCall(
        id="call-uuid-456",
        long_term_call_id="26-001679",
        nature="Fire Alarm",
        address="100 First St",
        agency_code="SJF",
        type="FIRE",
        zone_code="Z1",
        time_reported="2026-02-12 10:00:00",
        is_completed=True,
        comments="Alarm reset",
        joined_responders="E31",
        responder_details=[],
        ispy_responders={},
        city="Friday Harbor",
        state="WA",
        zip_code="98250",
        geo_location="",
    )


class TestCallToDict:
    def test_converts_dataclass_to_dict(self, sample_call):
        result = _call_to_dict(sample_call)
        assert isinstance(result, dict)
        assert result["id"] == "call-uuid-123"
        assert result["long_term_call_id"] == "26-001678"
        assert result["nature"] == "Medical Aid"
        assert result["address"] == "200 Spring St"

    def test_nested_responder_details(self, sample_call):
        result = _call_to_dict(sample_call)
        assert len(result["responder_details"]) == 1
        assert result["responder_details"][0]["unit_number"] == "E31"
        assert result["responder_details"][0]["status"] == "Dispatched"

    def test_preserves_all_fields(self, sample_call):
        result = _call_to_dict(sample_call)
        assert result["city"] == "Friday Harbor"
        assert result["state"] == "WA"
        assert result["zip_code"] == "98250"
        assert result["geo_location"] == "48.5343,-123.0170"
        assert result["ispy_responders"] == {"user1": "responding"}
        assert result["is_completed"] is False


class TestListDispatchCalls:
    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    @patch(
        "sjifire.mcp.dispatch.tools._store_completed_calls", new_callable=AsyncMock, return_value=0
    )
    async def test_returns_calls(self, _mock_store, mock_fetch, auth_user, sample_call):
        mock_fetch.return_value = [sample_call]

        result = await list_dispatch_calls(days=30)

        assert result["count"] == 1
        assert result["calls"][0]["nature"] == "Medical Aid"
        mock_fetch.assert_called_once_with(30)

    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    @patch(
        "sjifire.mcp.dispatch.tools._store_completed_calls", new_callable=AsyncMock, return_value=0
    )
    async def test_returns_empty_list(self, _mock_store, mock_fetch, auth_user):
        mock_fetch.return_value = []

        result = await list_dispatch_calls(days=7)

        assert result["count"] == 0
        assert result["calls"] == []
        mock_fetch.assert_called_once_with(7)

    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    @patch(
        "sjifire.mcp.dispatch.tools._store_completed_calls", new_callable=AsyncMock, return_value=0
    )
    async def test_default_days(self, _mock_store, mock_fetch, auth_user):
        mock_fetch.return_value = []

        await list_dispatch_calls()

        mock_fetch.assert_called_once_with(30)

    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    async def test_handles_exception(self, mock_fetch, auth_user):
        mock_fetch.side_effect = RuntimeError("connection failed")

        result = await list_dispatch_calls()

        assert "error" in result
        assert "connection failed" in result["error"]

    async def test_requires_auth(self):
        """In production mode, unauthenticated requests fail."""
        set_current_user(None)
        with (
            patch.dict(os.environ, {"ENTRA_MCP_API_CLIENT_ID": "real-client-id"}),
            pytest.raises(RuntimeError, match="No authenticated user"),
        ):
            await list_dispatch_calls()

    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    @patch("sjifire.mcp.dispatch.tools._store_completed_calls", new_callable=AsyncMock)
    async def test_stores_completed_calls(
        self, mock_store, mock_fetch, auth_user, completed_call, sample_call
    ):
        mock_fetch.return_value = [completed_call, sample_call]
        mock_store.return_value = 1

        result = await list_dispatch_calls()

        assert result["count"] == 2
        mock_store.assert_called_once()
        # The store function receives the raw DispatchCall list
        stored_calls = mock_store.call_args[0][0]
        assert len(stored_calls) == 2


class TestGetDispatchCall:
    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock, return_value=None)
    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_returns_call_from_ispyfire(
        self, mock_fetch, _mock_store, auth_user, sample_call
    ):
        mock_fetch.return_value = sample_call

        result = await get_dispatch_call("call-uuid-123")

        assert result["nature"] == "Medical Aid"
        assert result["long_term_call_id"] == "26-001678"

    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock)
    async def test_returns_call_from_store(self, mock_lookup, auth_user, completed_call):
        doc = DispatchCallDocument.from_dispatch_call(completed_call)
        mock_lookup.return_value = doc

        result = await get_dispatch_call("26-001679")

        assert result["nature"] == "Fire Alarm"
        assert result["is_completed"] is True
        # Should not have Cosmos-only fields
        assert "year" not in result
        assert "stored_at" not in result

    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock, return_value=None)
    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_not_found(self, mock_fetch, _mock_store, auth_user):
        mock_fetch.return_value = None

        result = await get_dispatch_call("nonexistent")

        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock, return_value=None)
    @patch("sjifire.mcp.dispatch.tools._store_single_call", new_callable=AsyncMock)
    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_stores_completed_on_fetch(
        self, mock_fetch, mock_store_single, _mock_lookup, auth_user, completed_call
    ):
        mock_fetch.return_value = completed_call

        result = await get_dispatch_call("call-uuid-456")

        assert result["is_completed"] is True
        mock_store_single.assert_called_once_with(completed_call)

    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock, return_value=None)
    @patch("sjifire.mcp.dispatch.tools._store_single_call", new_callable=AsyncMock)
    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_does_not_store_open_calls(
        self, mock_fetch, mock_store_single, _mock_lookup, auth_user, sample_call
    ):
        mock_fetch.return_value = sample_call  # is_completed=False

        await get_dispatch_call("call-uuid-123")

        mock_store_single.assert_not_called()

    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock, return_value=None)
    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_handles_exception(self, mock_fetch, _mock_store, auth_user):
        mock_fetch.side_effect = RuntimeError("API error")

        result = await get_dispatch_call("call-uuid-123")

        assert "error" in result
        assert "API error" in result["error"]


class TestGetOpenDispatchCalls:
    @patch("sjifire.mcp.dispatch.tools._fetch_open_calls")
    async def test_returns_open_calls(self, mock_fetch, auth_user, sample_call):
        mock_fetch.return_value = [sample_call]

        result = await get_open_dispatch_calls()

        assert result["count"] == 1
        assert result["calls"][0]["is_completed"] is False

    @patch("sjifire.mcp.dispatch.tools._fetch_open_calls")
    async def test_returns_empty_when_no_open(self, mock_fetch, auth_user):
        mock_fetch.return_value = []

        result = await get_open_dispatch_calls()

        assert result["count"] == 0
        assert result["calls"] == []

    @patch("sjifire.mcp.dispatch.tools._fetch_open_calls")
    async def test_handles_exception(self, mock_fetch, auth_user):
        mock_fetch.side_effect = RuntimeError("timeout")

        result = await get_open_dispatch_calls()

        assert "error" in result
        assert "timeout" in result["error"]


class TestGetDispatchCallLog:
    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock, return_value=None)
    @patch("sjifire.mcp.dispatch.tools._fetch_call_log")
    async def test_returns_log_from_ispyfire(self, mock_fetch, _mock_lookup, auth_user):
        mock_fetch.return_value = [
            {
                "email": "chief@sjifire.org",
                "commenttype": "viewed",
                "timestamp": "2026-02-12T15:00:00Z",
            },
        ]

        result = await get_dispatch_call_log("call-uuid-123")

        assert result["count"] == 1
        assert result["entries"][0]["email"] == "chief@sjifire.org"

    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock)
    async def test_returns_log_from_store(self, mock_lookup, auth_user):
        call = DispatchCall(
            id="call-uuid-stored",
            long_term_call_id="26-000100",
            nature="Fire Alarm",
            address="100 First St",
            agency_code="SJF",
            time_reported="2026-02-12 10:00:00",
            is_completed=True,
        )
        log_entries = [
            {
                "email": "ff@sjifire.org",
                "commenttype": "viewed",
                "timestamp": "2026-02-12T10:05:00Z",
            },
            {
                "email": "chief@sjifire.org",
                "commenttype": "viewed",
                "timestamp": "2026-02-12T10:10:00Z",
            },
        ]
        doc = DispatchCallDocument.from_dispatch_call(call, call_log=log_entries)
        mock_lookup.return_value = doc

        result = await get_dispatch_call_log("call-uuid-stored")

        assert result["count"] == 2
        assert result["entries"][0]["email"] == "ff@sjifire.org"

    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock, return_value=None)
    @patch("sjifire.mcp.dispatch.tools._fetch_call_log")
    async def test_empty_log(self, mock_fetch, _mock_lookup, auth_user):
        mock_fetch.return_value = []

        result = await get_dispatch_call_log("call-uuid-123")

        assert result["count"] == 0
        assert result["entries"] == []

    @patch("sjifire.mcp.dispatch.tools._lookup_in_store", new_callable=AsyncMock, return_value=None)
    @patch("sjifire.mcp.dispatch.tools._fetch_call_log")
    async def test_handles_exception(self, mock_fetch, _mock_lookup, auth_user):
        mock_fetch.side_effect = RuntimeError("not found")

        result = await get_dispatch_call_log("bad-id")

        assert "error" in result
        assert "not found" in result["error"]


class TestSearchDispatchCalls:
    async def test_search_by_dispatch_id(self, auth_user):
        # Pre-populate store
        call = DispatchCall(
            id="uuid-search-1",
            long_term_call_id="26-001000",
            nature="Medical Aid",
            address="300 Spring St",
            agency_code="SJF",
            time_reported="2026-01-15 08:00:00",
            is_completed=True,
        )
        doc = DispatchCallDocument.from_dispatch_call(call)
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await search_dispatch_calls(dispatch_id="26-001000")

        assert result["count"] == 1
        assert result["calls"][0]["nature"] == "Medical Aid"

    async def test_search_by_dispatch_id_not_found(self, auth_user):
        result = await search_dispatch_calls(dispatch_id="26-999999")

        assert result["count"] == 0
        assert result["calls"] == []

    async def test_search_by_date_range(self, auth_user):
        # Pre-populate store
        for i, date in enumerate(
            ["2026-01-10 10:00:00", "2026-01-20 10:00:00", "2026-02-05 10:00:00"]
        ):
            call = DispatchCall(
                id=f"uuid-range-{i}",
                long_term_call_id=f"26-00{i + 1:04d}",
                nature="Medical Aid",
                address="200 Spring St",
                agency_code="SJF",
                time_reported=date,
                is_completed=True,
            )
            doc = DispatchCallDocument.from_dispatch_call(call)
            async with DispatchStore() as store:
                await store.upsert(doc)

        result = await search_dispatch_calls(start_date="2026-01-01", end_date="2026-01-31")

        assert result["count"] == 2

    async def test_search_requires_parameters(self, auth_user):
        result = await search_dispatch_calls()

        assert "error" in result
        assert "At least one search parameter" in result["error"]

    async def test_search_date_range_requires_both_dates(self, auth_user):
        result = await search_dispatch_calls(start_date="2026-01-01")

        assert "error" in result
        assert "Both start_date and end_date" in result["error"]


class TestLookupInStore:
    """Tests for _lookup_in_store — dispatches to get_by_dispatch_id or get by UUID+year."""

    async def test_dispatch_id_found(self):
        """Dispatch ID pattern routes to get_by_dispatch_id."""
        call = DispatchCall(
            id="uuid-lookup-1",
            long_term_call_id="26-001234",
            nature="Medical Aid",
            address="100 Main St",
            agency_code="SJF",
            time_reported="2026-03-01 09:00:00",
            is_completed=True,
        )
        doc = DispatchCallDocument.from_dispatch_call(call)
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _lookup_in_store("26-001234")

        assert result is not None
        assert result.id == "uuid-lookup-1"

    async def test_dispatch_id_not_found(self):
        result = await _lookup_in_store("26-999999")
        assert result is None

    async def test_uuid_found_current_year(self):
        """UUID lookup tries current year first."""
        from datetime import UTC, datetime

        current_year = str(datetime.now(UTC).year)
        call = DispatchCall(
            id="uuid-current-year",
            long_term_call_id=f"{current_year[2:]}-000001",
            nature="Fire Alarm",
            address="200 Spring St",
            agency_code="SJF",
            time_reported=f"{current_year}-06-15 12:00:00",
            is_completed=True,
        )
        doc = DispatchCallDocument.from_dispatch_call(call)
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _lookup_in_store("uuid-current-year")

        assert result is not None
        assert result.year == current_year

    async def test_uuid_found_previous_year(self):
        """UUID lookup falls back to previous year when not in current year."""
        from datetime import UTC, datetime

        prev_year = str(int(datetime.now(UTC).year) - 1)
        call = DispatchCall(
            id="uuid-prev-year",
            long_term_call_id=f"{prev_year[2:]}-000500",
            nature="Structure Fire",
            address="300 Harbor St",
            agency_code="SJF",
            time_reported=f"{prev_year}-11-20 08:00:00",
            is_completed=True,
        )
        doc = DispatchCallDocument.from_dispatch_call(call)
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _lookup_in_store("uuid-prev-year")

        assert result is not None
        assert result.year == prev_year

    async def test_uuid_not_found_either_year(self):
        """UUID not in current or previous year returns None."""
        result = await _lookup_in_store("uuid-does-not-exist")
        assert result is None

    async def test_uuid_from_older_year_not_found(self):
        """UUID from 2+ years ago won't be found (only checks current & prev)."""
        from datetime import UTC, datetime

        old_year = str(int(datetime.now(UTC).year) - 2)
        call = DispatchCall(
            id="uuid-old-year",
            long_term_call_id=f"{old_year[2:]}-000100",
            nature="Medical Aid",
            address="100 Main St",
            agency_code="SJF",
            time_reported=f"{old_year}-01-15 10:00:00",
            is_completed=True,
        )
        doc = DispatchCallDocument.from_dispatch_call(call)
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _lookup_in_store("uuid-old-year")

        assert result is None

    async def test_exception_returns_none(self):
        """Store errors are swallowed — returns None instead of raising."""
        with patch(
            "sjifire.mcp.dispatch.tools.DispatchStore",
            side_effect=RuntimeError("Cosmos unavailable"),
        ):
            result = await _lookup_in_store("26-001234")
        assert result is None


class TestStoreSingleCall:
    """Tests for _store_single_call — fetches log, creates doc, upserts."""

    @patch("sjifire.mcp.dispatch.tools._async_fetch_call_log", new_callable=AsyncMock)
    async def test_stores_call_with_log(self, mock_fetch_log):
        mock_fetch_log.return_value = [
            {"email": "ff@sjifire.org", "commenttype": "viewed"},
        ]
        call = DispatchCall(
            id="uuid-store-single",
            long_term_call_id="26-002000",
            nature="Medical Aid",
            address="100 Main St",
            agency_code="SJF",
            time_reported="2026-05-01 14:00:00",
            is_completed=True,
        )

        await _store_single_call(call)

        # Verify it was stored in the in-memory store
        async with DispatchStore() as store:
            doc = await store.get("uuid-store-single", "2026")

        assert doc is not None
        assert doc.long_term_call_id == "26-002000"
        assert doc.call_log == [{"email": "ff@sjifire.org", "commenttype": "viewed"}]
        mock_fetch_log.assert_called_once_with("uuid-store-single")

    @patch("sjifire.mcp.dispatch.tools._async_fetch_call_log", new_callable=AsyncMock)
    async def test_log_fetch_failure_does_not_raise(self, mock_fetch_log):
        """If fetching the call log fails, the error is swallowed."""
        mock_fetch_log.side_effect = RuntimeError("network error")
        call = DispatchCall(
            id="uuid-log-fail",
            long_term_call_id="26-002001",
            nature="Fire Alarm",
            address="200 Spring St",
            agency_code="SJF",
            time_reported="2026-05-02 10:00:00",
            is_completed=True,
        )

        # Should not raise
        await _store_single_call(call)

        # Call should NOT be stored since the exception happens before upsert
        # (the entire try block catches the exception from _async_fetch_call_log)
        async with DispatchStore() as store:
            doc = await store.get("uuid-log-fail", "2026")
        assert doc is None

    @patch("sjifire.mcp.dispatch.tools._async_fetch_call_log", new_callable=AsyncMock)
    async def test_store_upsert_failure_does_not_raise(self, mock_fetch_log):
        """If the upsert itself fails, the error is swallowed."""
        mock_fetch_log.return_value = []
        call = DispatchCall(
            id="uuid-upsert-fail",
            long_term_call_id="26-002002",
            nature="Medical Aid",
            address="100 Main St",
            agency_code="SJF",
            time_reported="2026-05-03 08:00:00",
            is_completed=True,
        )

        with patch(
            "sjifire.mcp.dispatch.tools.DispatchStore",
            side_effect=RuntimeError("Cosmos unavailable"),
        ):
            # Should not raise
            await _store_single_call(call)
