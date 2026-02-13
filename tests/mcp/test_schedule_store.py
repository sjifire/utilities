"""Tests for ScheduleStore in-memory mode."""

from datetime import UTC, datetime

import pytest

from sjifire.mcp.schedule.models import DayScheduleCache, ScheduleEntryCache
from sjifire.mcp.schedule.store import ScheduleStore


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset in-memory store and ensure Cosmos env vars are unset."""
    ScheduleStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.mcp.schedule.store.load_dotenv", lambda: None)
    yield
    ScheduleStore._memory.clear()


def _make_day_cache(date_str: str = "2026-02-12", **overrides) -> DayScheduleCache:
    """Helper to create a DayScheduleCache with sensible defaults."""
    defaults = {
        "id": date_str,
        "date": date_str,
        "platoon": "A",
        "entries": [
            ScheduleEntryCache(
                name="John Doe",
                position="Firefighter",
                section="Station 31",
                start_time="08:00",
                end_time="08:00",
                platoon="A",
            ),
        ],
        "fetched_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return DayScheduleCache(**defaults)


class TestGet:
    async def test_nonexistent_returns_none(self):
        async with ScheduleStore() as store:
            result = await store.get("2026-02-12")
        assert result is None

    async def test_upsert_then_get(self):
        doc = _make_day_cache("2026-02-12")
        async with ScheduleStore() as store:
            await store.upsert(doc)
            result = await store.get("2026-02-12")
        assert result is not None
        assert result.date == "2026-02-12"
        assert result.platoon == "A"
        assert len(result.entries) == 1
        assert result.entries[0].name == "John Doe"


class TestUpsert:
    async def test_overwrites_existing(self):
        doc1 = _make_day_cache("2026-02-12", platoon="A")
        doc2 = _make_day_cache("2026-02-12", platoon="B")
        async with ScheduleStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            result = await store.get("2026-02-12")
        assert result is not None
        assert result.platoon == "B"


class TestGetRange:
    async def test_returns_found_dates_only(self):
        doc1 = _make_day_cache("2026-02-12")
        doc2 = _make_day_cache("2026-02-13")
        async with ScheduleStore() as store:
            await store.upsert(doc1)
            await store.upsert(doc2)
            results = await store.get_range(["2026-02-12", "2026-02-13", "2026-02-14"])
        assert len(results) == 2
        assert "2026-02-12" in results
        assert "2026-02-13" in results
        assert "2026-02-14" not in results

    async def test_empty_list(self):
        async with ScheduleStore() as store:
            results = await store.get_range([])
        assert results == {}

    async def test_no_matches(self):
        async with ScheduleStore() as store:
            results = await store.get_range(["2026-02-12"])
        assert results == {}
