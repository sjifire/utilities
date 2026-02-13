"""Tests for DispatchStore (in-memory mode)."""

import os
from datetime import datetime
from unittest.mock import patch

import pytest

from sjifire.ispyfire.models import DispatchCall, UnitResponse
from sjifire.mcp.dispatch.models import DispatchCallDocument
from sjifire.mcp.dispatch.store import DispatchStore


@pytest.fixture(autouse=True)
def _no_cosmos():
    """Ensure in-memory mode by clearing COSMOS_ENDPOINT."""
    with patch.dict(os.environ, {"COSMOS_ENDPOINT": "", "COSMOS_KEY": ""}, clear=False):
        yield
    # Clean up shared in-memory state between tests
    DispatchStore._memory.clear()


def _make_call(**overrides) -> DispatchCall:
    defaults = {
        "id": "call-uuid-1",
        "long_term_call_id": "26-001678",
        "nature": "Medical Aid",
        "address": "200 Spring St",
        "agency_code": "SJF",
        "type": "EMS",
        "zone_code": "Z1",
        "time_reported": datetime(2026, 2, 12, 14, 30),
        "is_completed": True,
        "cad_comments": "Patient fall",
        "responding_units": "E31",
        "responder_details": [
            UnitResponse(
                unit_number="E31",
                agency_code="SJF",
                status="Dispatched",
                time_of_status_change=datetime(2026, 2, 12, 14, 30, 15),
            ),
        ],
        "city": "Friday Harbor",
        "state": "WA",
        "zip_code": "98250",
        "geo_location": "48.5343,-123.0170",
    }
    defaults.update(overrides)
    return DispatchCall(**defaults)


def _make_doc(**overrides) -> DispatchCallDocument:
    call = _make_call(**overrides)
    return DispatchCallDocument.from_dispatch_call(call)


class TestGet:
    async def test_get_existing(self):
        doc = _make_doc()
        async with DispatchStore() as store:
            await store.upsert(doc)
            result = await store.get("call-uuid-1", "2026")

        assert result is not None
        assert result.id == "call-uuid-1"
        assert result.nature == "Medical Aid"

    async def test_get_nonexistent(self):
        async with DispatchStore() as store:
            result = await store.get("no-such-id", "2026")
        assert result is None

    async def test_get_wrong_year(self):
        doc = _make_doc()
        async with DispatchStore() as store:
            await store.upsert(doc)
            result = await store.get("call-uuid-1", "2025")
        assert result is None


class TestGetByDispatchId:
    async def test_find_by_dispatch_id(self):
        doc = _make_doc()
        async with DispatchStore() as store:
            await store.upsert(doc)
            result = await store.get_by_dispatch_id("26-001678")

        assert result is not None
        assert result.long_term_call_id == "26-001678"

    async def test_not_found(self):
        async with DispatchStore() as store:
            result = await store.get_by_dispatch_id("26-999999")
        assert result is None

    async def test_finds_among_multiple(self):
        doc1 = _make_doc(id="uuid-1", long_term_call_id="26-000001")
        doc2 = _make_doc(id="uuid-2", long_term_call_id="26-000002")

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            result = await store.get_by_dispatch_id("26-000002")

        assert result is not None
        assert result.id == "uuid-2"


class TestUpsert:
    async def test_upsert_new(self):
        doc = _make_doc()
        async with DispatchStore() as store:
            result = await store.upsert(doc)
        assert result.id == doc.id

    async def test_upsert_overwrites(self):
        doc1 = _make_doc(cad_comments="original")
        doc2 = _make_doc(cad_comments="updated")

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            result = await store.get("call-uuid-1", "2026")

        assert result.cad_comments == "updated"


class TestListByDateRange:
    async def test_returns_calls_in_range(self):
        doc1 = _make_doc(id="uuid-1", time_reported=datetime(2026, 2, 10, 10, 0))
        doc2 = _make_doc(id="uuid-2", time_reported=datetime(2026, 2, 12, 14, 30))
        doc3 = _make_doc(id="uuid-3", time_reported=datetime(2026, 2, 15, 8, 0))

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            await store.upsert(doc3)
            results = await store.list_by_date_range("2026-02-11", "2026-02-13")

        assert len(results) == 1
        assert results[0].id == "uuid-2"

    async def test_empty_range(self):
        doc = _make_doc(time_reported=datetime(2026, 2, 12, 14, 30))

        async with DispatchStore() as store:
            await store.upsert(doc)
            results = await store.list_by_date_range("2025-01-01", "2025-01-31")

        assert len(results) == 0

    async def test_sorted_desc(self):
        doc1 = _make_doc(id="uuid-1", time_reported=datetime(2026, 2, 10, 10, 0))
        doc2 = _make_doc(id="uuid-2", time_reported=datetime(2026, 2, 12, 14, 30))

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            results = await store.list_by_date_range("2026-02-01", "2026-02-28")

        assert len(results) == 2
        assert results[0].id == "uuid-2"  # Later date first
        assert results[1].id == "uuid-1"

    async def test_max_items(self):
        for i in range(5):
            doc = _make_doc(id=f"uuid-{i}", time_reported=datetime(2026, 2, 10 + i, 10, 0))
            async with DispatchStore() as store:
                await store.upsert(doc)

        async with DispatchStore() as store:
            results = await store.list_by_date_range("2026-02-01", "2026-02-28", max_items=3)

        assert len(results) == 3


class TestListByAddress:
    async def test_returns_matching_address(self):
        doc1 = _make_doc(id="uuid-1", address="200 Spring St")
        doc2 = _make_doc(id="uuid-2", address="200 Spring St")
        doc3 = _make_doc(id="uuid-3", address="100 First St")

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            await store.upsert(doc3)
            results = await store.list_by_address("200 Spring St")

        assert len(results) == 2

    async def test_excludes_current_call(self):
        doc1 = _make_doc(id="uuid-1", address="200 Spring St")
        doc2 = _make_doc(id="uuid-2", address="200 Spring St")

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            results = await store.list_by_address("200 Spring St", exclude_id="uuid-1")

        assert len(results) == 1
        assert results[0].id == "uuid-2"

    async def test_no_matches(self):
        doc = _make_doc(address="200 Spring St")

        async with DispatchStore() as store:
            await store.upsert(doc)
            results = await store.list_by_address("999 Nowhere St")

        assert len(results) == 0

    async def test_sorted_desc(self):
        doc1 = _make_doc(
            id="uuid-1", address="200 Spring St", time_reported=datetime(2026, 1, 10, 10, 0)
        )
        doc2 = _make_doc(
            id="uuid-2", address="200 Spring St", time_reported=datetime(2026, 2, 12, 14, 30)
        )

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            results = await store.list_by_address("200 Spring St")

        assert results[0].id == "uuid-2"  # Later date first
        assert results[1].id == "uuid-1"

    async def test_max_items(self):
        for i in range(5):
            doc = _make_doc(
                id=f"uuid-{i}",
                address="200 Spring St",
                time_reported=datetime(2026, 2, 10 + i, 10, 0),
            )
            async with DispatchStore() as store:
                await store.upsert(doc)

        async with DispatchStore() as store:
            results = await store.list_by_address("200 Spring St", max_items=3)

        assert len(results) == 3


class TestGetExistingIds:
    async def test_returns_matching_ids(self):
        doc1 = _make_doc(id="uuid-1")
        doc2 = _make_doc(id="uuid-2")

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            result = await store.get_existing_ids(["uuid-1", "uuid-2", "uuid-3"])

        assert result == {"uuid-1", "uuid-2"}

    async def test_empty_input(self):
        async with DispatchStore() as store:
            result = await store.get_existing_ids([])
        assert result == set()

    async def test_none_exist(self):
        async with DispatchStore() as store:
            result = await store.get_existing_ids(["uuid-a", "uuid-b"])
        assert result == set()

    async def test_all_exist(self):
        doc1 = _make_doc(id="uuid-1")
        doc2 = _make_doc(id="uuid-2")

        async with DispatchStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            result = await store.get_existing_ids(["uuid-1", "uuid-2"])

        assert result == {"uuid-1", "uuid-2"}


class TestStoreCompleted:
    async def test_stores_completed_calls(self):
        completed_call = _make_call(id="uuid-done", is_completed=True)
        open_call = _make_call(id="uuid-open", is_completed=False)

        async with DispatchStore() as store:
            count = await store.store_completed([completed_call, open_call])
            assert count == 1

            # Completed call should be stored
            doc = await store.get("uuid-done", "2026")
            assert doc is not None
            assert doc.nature == "Medical Aid"

            # Open call should not be stored
            doc = await store.get("uuid-open", "2026")
            assert doc is None

    async def test_returns_zero_for_no_completed(self):
        open_call = _make_call(is_completed=False)

        async with DispatchStore() as store:
            count = await store.store_completed([open_call])

        assert count == 0
