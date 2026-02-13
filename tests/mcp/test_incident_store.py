"""Tests for IncidentStore in-memory mode."""

from datetime import date

import pytest

from sjifire.mcp.incidents.models import CrewAssignment, IncidentDocument
from sjifire.mcp.incidents.store import IncidentStore


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset in-memory store and ensure Cosmos env vars are unset."""
    IncidentStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.mcp.incidents.store.load_dotenv", lambda: None)
    yield
    IncidentStore._memory.clear()


def _make_doc(**overrides) -> IncidentDocument:
    """Helper to create an IncidentDocument with sensible defaults."""
    defaults = {
        "station": "S31",
        "incident_number": "26-000944",
        "incident_date": date(2026, 2, 12),
        "created_by": "chief@sjifire.org",
    }
    defaults.update(overrides)
    return IncidentDocument(**defaults)


class TestCreate:
    async def test_creates_draft(self):
        doc = _make_doc()
        async with IncidentStore() as store:
            result = await store.create(doc)
        assert result.id == doc.id
        assert result.station == "S31"
        assert result.status == "draft"

    async def test_create_and_get_back(self):
        doc = _make_doc()
        async with IncidentStore() as store:
            await store.create(doc)
            fetched = await store.get(doc.id, "S31")
        assert fetched is not None
        assert fetched.incident_number == "26-000944"
        assert fetched.created_by == "chief@sjifire.org"


class TestGet:
    async def test_nonexistent_returns_none(self):
        async with IncidentStore() as store:
            result = await store.get("nonexistent-id", "S31")
        assert result is None

    async def test_wrong_station_returns_none(self):
        doc = _make_doc(station="S31")
        async with IncidentStore() as store:
            await store.create(doc)
            result = await store.get(doc.id, "S32")
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
            fetched = await store.get(doc.id, "S31")
        assert fetched is not None
        assert fetched.incident_type == "111"


class TestDelete:
    async def test_delete_removes_item(self):
        doc = _make_doc()
        async with IncidentStore() as store:
            await store.create(doc)
            await store.delete(doc.id, "S31")
            result = await store.get(doc.id, "S31")
        assert result is None

    async def test_delete_nonexistent_no_error(self):
        async with IncidentStore() as store:
            await store.delete("nonexistent-id", "S31")


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

    async def test_filtered_by_station(self):
        doc1 = _make_doc(station="S31", incident_number="26-001")
        doc2 = _make_doc(station="S32", incident_number="26-002")
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            results = await store.list_by_status(station="S31")
        assert len(results) == 1
        assert results[0].station == "S31"

    async def test_filtered_by_status_and_station(self):
        doc1 = _make_doc(station="S31", incident_number="26-001")
        doc2 = _make_doc(station="S31", incident_number="26-002", status="submitted")
        doc3 = _make_doc(station="S32", incident_number="26-003")
        async with IncidentStore() as store:
            await store.create(doc1)
            await store.create(doc2)
            await store.create(doc3)
            results = await store.list_by_status(status="draft", station="S31")
        assert len(results) == 1
        assert results[0].incident_number == "26-001"


class TestListForUser:
    async def test_as_creator(self):
        doc = _make_doc(created_by="ff@sjifire.org")
        async with IncidentStore() as store:
            await store.create(doc)
            results = await store.list_for_user("ff@sjifire.org")
        assert len(results) == 1
        assert results[0].created_by == "ff@sjifire.org"

    async def test_as_crew_member(self):
        doc = _make_doc(
            created_by="chief@sjifire.org",
            crew=[CrewAssignment(name="Jane", email="jane@sjifire.org", position="FF")],
        )
        async with IncidentStore() as store:
            await store.create(doc)
            results = await store.list_for_user("jane@sjifire.org")
        assert len(results) == 1

    async def test_excludes_others_incidents(self):
        doc = _make_doc(
            created_by="chief@sjifire.org",
            crew=[CrewAssignment(name="Jane", email="jane@sjifire.org")],
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
