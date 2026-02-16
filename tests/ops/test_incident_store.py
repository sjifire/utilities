"""Tests for IncidentStore in-memory mode."""

from datetime import UTC, datetime

import pytest

from sjifire.ops.incidents.models import IncidentDocument, PersonnelAssignment, UnitAssignment
from sjifire.ops.incidents.store import IncidentStore


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset in-memory store and ensure Cosmos env vars are unset."""
    IncidentStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.incidents.store.load_dotenv", lambda: None)
    yield
    IncidentStore._memory.clear()


def _make_doc(**overrides) -> IncidentDocument:
    """Helper to create an IncidentDocument with sensible defaults."""
    defaults = {
        "incident_number": "26-000944",
        "incident_datetime": datetime(2026, 2, 12, tzinfo=UTC),
        "created_by": "chief@sjifire.org",
    }
    defaults.update(overrides)
    return IncidentDocument(**defaults)


class TestCreate:
    async def test_creates_draft(self):
        doc = _make_doc(extras={"station": "S31"})
        async with IncidentStore() as store:
            result = await store.create(doc)
        assert result.id == doc.id
        assert result.year == "2026"
        assert result.extras.get("station") == "S31"
        assert result.status == "draft"

    async def test_create_and_get_back(self):
        doc = _make_doc()
        async with IncidentStore() as store:
            await store.create(doc)
            fetched = await store.get(doc.id, "2026")
        assert fetched is not None
        assert fetched.incident_number == "26-000944"
        assert fetched.created_by == "chief@sjifire.org"


class TestGet:
    async def test_nonexistent_returns_none(self):
        async with IncidentStore() as store:
            result = await store.get("nonexistent-id", "2026")
        assert result is None

    async def test_wrong_year_returns_none(self):
        doc = _make_doc(incident_datetime=datetime(2026, 2, 12, tzinfo=UTC))
        async with IncidentStore() as store:
            await store.create(doc)
            result = await store.get(doc.id, "2025")
        assert result is None


class TestGetById:
    async def test_finds_without_year(self):
        doc = _make_doc()
        async with IncidentStore() as store:
            await store.create(doc)
            result = await store.get_by_id(doc.id)
        assert result is not None
        assert result.incident_number == "26-000944"

    async def test_nonexistent_returns_none(self):
        async with IncidentStore() as store:
            result = await store.get_by_id("nonexistent-id")
        assert result is None


class TestGetByNerisId:
    async def test_finds_by_neris_id(self):
        doc = _make_doc(neris_incident_id="FD53055879|26-000039|1767316361")
        async with IncidentStore() as store:
            await store.create(doc)
            result = await store.get_by_neris_id("FD53055879|26-000039|1767316361")
        assert result is not None
        assert result.neris_incident_id == "FD53055879|26-000039|1767316361"
        assert result.incident_number == "26-000944"

    async def test_nonexistent_returns_none(self):
        async with IncidentStore() as store:
            result = await store.get_by_neris_id("FD|BOGUS|999")
        assert result is None

    async def test_returns_none_when_no_neris_id_set(self):
        doc = _make_doc()  # neris_incident_id defaults to None
        async with IncidentStore() as store:
            await store.create(doc)
            result = await store.get_by_neris_id("FD53055879|26-000039|1767316361")
        assert result is None

    async def test_finds_correct_among_multiple(self):
        doc1 = _make_doc(
            incident_number="26-001",
            neris_incident_id="FD53055879|26-001|AAA",
        )
        doc2 = _make_doc(
            incident_number="26-002",
            neris_incident_id="FD53055879|26-002|BBB",
        )
        doc3 = _make_doc(incident_number="26-003")  # No NERIS ID
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            await store.create(doc3)
            result = await store.get_by_neris_id("FD53055879|26-002|BBB")
        assert result is not None
        assert result.incident_number == "26-002"

    async def test_exact_match_only(self):
        doc = _make_doc(neris_incident_id="FD53055879|26-000039|1767316361")
        async with IncidentStore() as store:
            await store.create(doc)
            # Partial match should not find it
            result = await store.get_by_neris_id("FD53055879|26-000039")
        assert result is None


class TestUpdate:
    async def test_update_changes_fields(self):
        doc = _make_doc()
        async with IncidentStore() as store:
            await store.create(doc)
            doc.incident_type = "111"
            doc.status = "ready_review"
            updated = await store.update(doc)
        assert updated.incident_type == "111"
        assert updated.status == "ready_review"

    async def test_update_persists(self):
        doc = _make_doc()
        async with IncidentStore() as store:
            await store.create(doc)
            doc.incident_type = "111"
            await store.update(doc)
            fetched = await store.get(doc.id, "2026")
        assert fetched is not None
        assert fetched.incident_type == "111"


class TestDelete:
    async def test_delete_removes_item(self):
        doc = _make_doc()
        async with IncidentStore() as store:
            await store.create(doc)
            await store.delete(doc.id, "2026")
            result = await store.get(doc.id, "2026")
        assert result is None

    async def test_delete_nonexistent_no_error(self):
        async with IncidentStore() as store:
            await store.delete("nonexistent-id", "2026")


class TestListAll:
    async def test_returns_all_including_submitted(self):
        doc1 = _make_doc(incident_number="26-001")
        doc2 = _make_doc(incident_number="26-002", status="submitted")
        doc3 = _make_doc(incident_number="26-003", status="in_progress")
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            await store.create(doc3)
            results = await store.list_all()
        assert len(results) == 3
        statuses = {r.status for r in results}
        assert "submitted" in statuses

    async def test_sorted_by_incident_datetime_asc(self):
        doc_feb = _make_doc(
            incident_number="26-002", incident_datetime=datetime(2026, 2, 15, tzinfo=UTC)
        )
        doc_jan = _make_doc(
            incident_number="26-001", incident_datetime=datetime(2026, 1, 10, tzinfo=UTC)
        )
        doc_mar = _make_doc(
            incident_number="26-003", incident_datetime=datetime(2026, 3, 20, tzinfo=UTC)
        )
        async with IncidentStore() as store:
            await store.create(doc_feb)
            await store.create(doc_mar)
            await store.create(doc_jan)
            results = await store.list_all()
        assert [r.incident_number for r in results] == ["26-001", "26-002", "26-003"]

    async def test_respects_max_items(self):
        for i in range(5):
            doc = _make_doc(
                incident_number=f"26-{i:03d}",
                incident_datetime=datetime(2026, 1, 1 + i, tzinfo=UTC),
            )
            async with IncidentStore() as store:
                await store.create(doc)
        async with IncidentStore() as store:
            results = await store.list_all(max_items=3)
        assert len(results) == 3

    async def test_empty_store(self):
        async with IncidentStore() as store:
            results = await store.list_all()
        assert results == []


class TestListByStatus:
    async def test_unfiltered(self):
        doc1 = _make_doc(incident_number="26-001")
        doc2 = _make_doc(incident_number="26-002", status="submitted")
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            results = await store.list_by_status()
        assert len(results) == 2

    async def test_filtered_by_status(self):
        doc1 = _make_doc(incident_number="26-001")
        doc2 = _make_doc(incident_number="26-002", status="submitted")
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            results = await store.list_by_status(status="draft")
        assert len(results) == 1
        assert results[0].incident_number == "26-001"

    async def test_exclude_status(self):
        doc1 = _make_doc(incident_number="26-001")
        doc2 = _make_doc(incident_number="26-002", status="submitted")
        doc3 = _make_doc(incident_number="26-003", status="in_progress")
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            await store.create(doc3)
            results = await store.list_by_status(exclude_status="submitted")
        assert len(results) == 2
        assert all(r.status != "submitted" for r in results)

    async def test_sorted_by_incident_datetime_asc(self):
        doc_feb = _make_doc(
            incident_number="26-002", incident_datetime=datetime(2026, 2, 15, tzinfo=UTC)
        )
        doc_jan = _make_doc(
            incident_number="26-001", incident_datetime=datetime(2026, 1, 10, tzinfo=UTC)
        )
        doc_mar = _make_doc(
            incident_number="26-003", incident_datetime=datetime(2026, 3, 20, tzinfo=UTC)
        )
        async with IncidentStore() as store:
            # Insert out of order
            await store.create(doc_feb)
            await store.create(doc_mar)
            await store.create(doc_jan)
            results = await store.list_by_status()
        assert [r.incident_number for r in results] == ["26-001", "26-002", "26-003"]


class TestListForUser:
    async def test_as_creator(self):
        doc = _make_doc(created_by="ff@sjifire.org")
        async with IncidentStore() as store:
            await store.create(doc)
            results = await store.list_for_user("ff@sjifire.org")
        assert len(results) == 1
        assert results[0].created_by == "ff@sjifire.org"

    async def test_as_personnel_member(self):
        doc = _make_doc(
            created_by="chief@sjifire.org",
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[
                        PersonnelAssignment(name="Jane", email="jane@sjifire.org", position="FF")
                    ],
                )
            ],
        )
        async with IncidentStore() as store:
            await store.create(doc)
            results = await store.list_for_user("jane@sjifire.org")
        assert len(results) == 1

    async def test_excludes_others_incidents(self):
        doc = _make_doc(
            created_by="chief@sjifire.org",
            units=[
                UnitAssignment(
                    unit_id="E31",
                    personnel=[PersonnelAssignment(name="Jane", email="jane@sjifire.org")],
                )
            ],
        )
        async with IncidentStore() as store:
            await store.create(doc)
            results = await store.list_for_user("stranger@sjifire.org")
        assert len(results) == 0

    async def test_filtered_by_status(self):
        doc1 = _make_doc(created_by="ff@sjifire.org", incident_number="26-001")
        doc2 = _make_doc(created_by="ff@sjifire.org", incident_number="26-002", status="submitted")
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            results = await store.list_for_user("ff@sjifire.org", status="draft")
        assert len(results) == 1
        assert results[0].incident_number == "26-001"

    async def test_exclude_status(self):
        doc1 = _make_doc(created_by="ff@sjifire.org", incident_number="26-001")
        doc2 = _make_doc(created_by="ff@sjifire.org", incident_number="26-002", status="submitted")
        doc3 = _make_doc(
            created_by="ff@sjifire.org", incident_number="26-003", status="in_progress"
        )
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            await store.create(doc3)
            results = await store.list_for_user("ff@sjifire.org", exclude_status="submitted")
        assert len(results) == 2
        assert all(r.status != "submitted" for r in results)

    async def test_sorted_by_incident_datetime_asc(self):
        doc_feb = _make_doc(
            created_by="ff@sjifire.org",
            incident_number="26-002",
            incident_datetime=datetime(2026, 2, 15, tzinfo=UTC),
        )
        doc_jan = _make_doc(
            created_by="ff@sjifire.org",
            incident_number="26-001",
            incident_datetime=datetime(2026, 1, 10, tzinfo=UTC),
        )
        async with IncidentStore() as store:
            await store.create(doc_feb)
            await store.create(doc_jan)
            results = await store.list_for_user("ff@sjifire.org")
        assert [r.incident_number for r in results] == ["26-001", "26-002"]
