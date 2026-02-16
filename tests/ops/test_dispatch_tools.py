"""Tests for dispatch tools."""

import os
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.dispatch.models import DispatchCallDocument
from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.dispatch.tools import (
    get_dispatch_call,
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


def _make_doc(**overrides) -> DispatchCallDocument:
    defaults = {
        "id": "call-uuid-123",
        "year": "2026",
        "long_term_call_id": "26-001678",
        "nature": "Medical Aid",
        "address": "200 Spring St",
        "agency_code": "SJF",
        "type": "EMS",
        "zone_code": "Z1",
        "time_reported": datetime(2026, 2, 12, 14, 30),
        "is_completed": False,
        "cad_comments": "Patient fall, possible hip injury",
        "responding_units": "E31,M31",
        "responder_details": [
            {
                "unit_number": "E31",
                "agency_code": "SJF",
                "status": "Dispatched",
                "time_of_status_change": "2026-02-12T14:30:15",
                "radio_log": "E31 dispatched",
            },
        ],
        "city": "Friday Harbor",
        "state": "WA",
        "zip_code": "98250",
        "geo_location": "48.5343,-123.0170",
    }
    defaults.update(overrides)
    return DispatchCallDocument(**defaults)


@pytest.fixture
def sample_doc():
    return _make_doc()


@pytest.fixture
def completed_doc():
    return _make_doc(
        id="call-uuid-456",
        long_term_call_id="26-001679",
        nature="Fire Alarm",
        address="100 First St",
        is_completed=True,
        cad_comments="Alarm reset",
        responding_units="E31",
        responder_details=[],
        geo_location="",
        time_reported=datetime(2026, 2, 12, 10, 0),
    )


class TestListDispatchCalls:
    @patch.object(DispatchStore, "list_recent_with_open", new_callable=AsyncMock)
    async def test_returns_calls(self, mock_list, auth_user, sample_doc):
        mock_list.return_value = [sample_doc]

        result = await list_dispatch_calls(days=30)

        assert result["count"] == 1
        assert result["calls"][0]["nature"] == "Medical Aid"
        mock_list.assert_called_once()

    @patch.object(DispatchStore, "list_recent_with_open", new_callable=AsyncMock)
    async def test_returns_empty_list(self, mock_list, auth_user):
        mock_list.return_value = []

        result = await list_dispatch_calls(days=7)

        assert result["count"] == 0
        assert result["calls"] == []

    @patch.object(DispatchStore, "list_recent_with_open", new_callable=AsyncMock)
    async def test_default_days(self, mock_list, auth_user):
        mock_list.return_value = []

        await list_dispatch_calls()

        mock_list.assert_called_once()

    @patch.object(
        DispatchStore,
        "list_recent_with_open",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection failed"),
    )
    async def test_handles_exception(self, mock_list, auth_user):
        result = await list_dispatch_calls()

        assert "error" in result
        assert "Unable to retrieve dispatch calls" in result["error"]

    async def test_requires_auth(self):
        """In production mode, unauthenticated requests fail."""
        set_current_user(None)
        with (
            patch.dict(os.environ, {"ENTRA_MCP_API_CLIENT_ID": "real-client-id"}),
            pytest.raises(RuntimeError, match="No authenticated user"),
        ):
            await list_dispatch_calls()


class TestGetDispatchCall:
    @patch.object(DispatchStore, "list_by_address", new_callable=AsyncMock, return_value=[])
    @patch.object(DispatchStore, "get_or_fetch", new_callable=AsyncMock)
    async def test_returns_call(self, mock_get, _mock_history, auth_user, sample_doc):
        mock_get.return_value = sample_doc

        result = await get_dispatch_call("call-uuid-123")

        assert result["nature"] == "Medical Aid"
        assert result["long_term_call_id"] == "26-001678"

    @patch.object(DispatchStore, "list_by_address", new_callable=AsyncMock, return_value=[])
    @patch.object(DispatchStore, "get_or_fetch", new_callable=AsyncMock)
    async def test_returns_call_from_store(self, mock_get, _mock_history, auth_user, completed_doc):
        mock_get.return_value = completed_doc

        result = await get_dispatch_call("26-001679")

        assert result["nature"] == "Fire Alarm"
        assert result["is_completed"] is True
        # Should not have Cosmos-only fields
        assert "year" not in result
        assert "stored_at" not in result

    @patch.object(DispatchStore, "get_or_fetch", new_callable=AsyncMock, return_value=None)
    async def test_not_found(self, mock_get, auth_user):
        result = await get_dispatch_call("nonexistent")

        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch.object(DispatchStore, "list_by_address", new_callable=AsyncMock, return_value=[])
    @patch.object(DispatchStore, "get_or_fetch", new_callable=AsyncMock)
    async def test_completed_call_stored(self, mock_get, _mock_history, auth_user, completed_doc):
        """get_or_fetch handles enrichment+storage; tools layer just uses the result."""
        mock_get.return_value = completed_doc

        result = await get_dispatch_call("call-uuid-456")

        assert result["is_completed"] is True
        mock_get.assert_called_once_with("call-uuid-456")

    @patch.object(DispatchStore, "list_by_address", new_callable=AsyncMock, return_value=[])
    @patch.object(DispatchStore, "get_or_fetch", new_callable=AsyncMock)
    async def test_open_call_not_stored(self, mock_get, _mock_history, auth_user, sample_doc):
        """Open calls returned as-is (get_or_fetch doesn't store them)."""
        mock_get.return_value = sample_doc

        result = await get_dispatch_call("call-uuid-123")

        assert result["is_completed"] is False

    @patch.object(
        DispatchStore,
        "get_or_fetch",
        new_callable=AsyncMock,
        side_effect=RuntimeError("API error"),
    )
    async def test_handles_exception(self, mock_get, auth_user):
        result = await get_dispatch_call("call-uuid-123")

        assert "error" in result
        assert "Unable to retrieve call details" in result["error"]

    @patch.object(DispatchStore, "list_by_address", new_callable=AsyncMock)
    @patch.object(DispatchStore, "get_or_fetch", new_callable=AsyncMock)
    async def test_includes_site_history(self, mock_get, mock_history, auth_user, sample_doc):
        mock_get.return_value = sample_doc
        history_doc = _make_doc(
            id="uuid-hist-1",
            long_term_call_id="26-001000",
            nature="Fire Alarm",
            time_reported=datetime(2026, 1, 15, 8, 0),
        )
        mock_history.return_value = [history_doc]

        result = await get_dispatch_call("call-uuid-123")

        assert "site_history" in result
        assert len(result["site_history"]) == 1
        assert result["site_history"][0]["dispatch_id"] == "26-001000"


class TestGetOpenDispatchCalls:
    @patch.object(DispatchStore, "fetch_open", new_callable=AsyncMock)
    async def test_returns_open_calls(self, mock_fetch, auth_user, sample_doc):
        mock_fetch.return_value = [sample_doc]

        result = await get_open_dispatch_calls()

        assert result["count"] == 1
        assert result["calls"][0]["is_completed"] is False

    @patch.object(DispatchStore, "fetch_open", new_callable=AsyncMock)
    async def test_returns_empty_when_no_open(self, mock_fetch, auth_user):
        mock_fetch.return_value = []

        result = await get_open_dispatch_calls()

        assert result["count"] == 0
        assert result["calls"] == []

    @patch.object(
        DispatchStore,
        "fetch_open",
        new_callable=AsyncMock,
        side_effect=RuntimeError("timeout"),
    )
    async def test_handles_exception(self, mock_fetch, auth_user):
        result = await get_open_dispatch_calls()

        assert "error" in result
        assert "Unable to retrieve open calls" in result["error"]


class TestSearchDispatchCalls:
    async def test_search_by_dispatch_id(self, auth_user):
        # Pre-populate store
        doc = _make_doc(
            id="uuid-search-1",
            long_term_call_id="26-001000",
            time_reported=datetime(2026, 1, 15, 8, 0),
            is_completed=True,
        )
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
        for i, dt in enumerate(
            [
                datetime(2026, 1, 10, 10, 0),
                datetime(2026, 1, 20, 10, 0),
                datetime(2026, 2, 5, 10, 0),
            ]
        ):
            doc = _make_doc(
                id=f"uuid-range-{i}",
                long_term_call_id=f"26-00{i + 1:04d}",
                time_reported=dt,
                is_completed=True,
            )
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


class TestLookup:
    """Tests for DispatchStore.lookup — dispatches to get_by_dispatch_id or get by UUID+year."""

    async def test_dispatch_id_found(self):
        """Dispatch ID pattern routes to get_by_dispatch_id."""
        doc = _make_doc(
            id="uuid-lookup-1",
            long_term_call_id="26-001234",
            time_reported=datetime(2026, 3, 1, 9, 0),
            is_completed=True,
        )
        async with DispatchStore() as store:
            await store.upsert(doc)
            result = await store.lookup("26-001234")

        assert result is not None
        assert result.id == "uuid-lookup-1"

    async def test_dispatch_id_not_found(self):
        async with DispatchStore() as store:
            result = await store.lookup("26-999999")
        assert result is None

    async def test_uuid_found_current_year(self):
        """UUID lookup tries current year first."""
        from datetime import UTC
        from datetime import datetime as dt_cls

        current_year = str(dt_cls.now(UTC).year)
        doc = _make_doc(
            id="uuid-current-year",
            year=current_year,
            long_term_call_id=f"{current_year[2:]}-000001",
            time_reported=datetime(int(current_year), 6, 15, 12, 0),
            is_completed=True,
        )
        async with DispatchStore() as store:
            await store.upsert(doc)
            result = await store.lookup("uuid-current-year")

        assert result is not None
        assert result.year == current_year

    async def test_uuid_found_previous_year(self):
        """UUID lookup falls back to previous year when not in current year."""
        from datetime import UTC
        from datetime import datetime as dt_cls

        prev_year = str(int(dt_cls.now(UTC).year) - 1)
        doc = _make_doc(
            id="uuid-prev-year",
            year=prev_year,
            long_term_call_id=f"{prev_year[2:]}-000500",
            time_reported=datetime(int(prev_year), 11, 20, 8, 0),
            is_completed=True,
        )
        async with DispatchStore() as store:
            await store.upsert(doc)
            result = await store.lookup("uuid-prev-year")

        assert result is not None
        assert result.year == prev_year

    async def test_uuid_not_found_either_year(self):
        """UUID not in current or previous year returns None."""
        async with DispatchStore() as store:
            result = await store.lookup("uuid-does-not-exist")
        assert result is None

    async def test_uuid_from_older_year_not_found(self):
        """UUID from 2+ years ago won't be found (only checks current & prev)."""
        from datetime import UTC
        from datetime import datetime as dt_cls

        old_year = str(int(dt_cls.now(UTC).year) - 2)
        doc = _make_doc(
            id="uuid-old-year",
            year=old_year,
            long_term_call_id=f"{old_year[2:]}-000100",
            time_reported=datetime(int(old_year), 1, 15, 10, 0),
            is_completed=True,
        )
        async with DispatchStore() as store:
            await store.upsert(doc)
            result = await store.lookup("uuid-old-year")

        assert result is None
