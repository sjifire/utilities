"""Tests for ScheduleStore in-memory mode."""

from datetime import UTC, date, datetime

import pytest

from sjifire.mcp.schedule.models import DayScheduleCache, ScheduleEntryCache
from sjifire.mcp.schedule.store import ScheduleStore, _entry_covers_time


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


# ---------------------------------------------------------------------------
# _entry_covers_time — unit tests for shift time coverage
# ---------------------------------------------------------------------------


def _entry(start: str, end: str, name: str = "Test") -> ScheduleEntryCache:
    """Helper to create a schedule entry with given times."""
    return ScheduleEntryCache(
        name=name,
        position="Firefighter",
        section="S31",
        start_time=start,
        end_time=end,
    )


class TestEntryCoverTime:
    """Test _entry_covers_time with various shift patterns."""

    def test_full_24h_shift_covers_morning(self):
        # 18:00-18:00 on Jan 19 covers 08:00 on Jan 20
        e = _entry("18:00", "18:00")
        dt = datetime(2026, 1, 20, 8, 0)
        assert _entry_covers_time(e, date(2026, 1, 19), dt) is True

    def test_full_24h_shift_covers_evening(self):
        # 18:00-18:00 on Jan 19 covers 22:00 on Jan 19
        e = _entry("18:00", "18:00")
        dt = datetime(2026, 1, 19, 22, 0)
        assert _entry_covers_time(e, date(2026, 1, 19), dt) is True

    def test_full_24h_shift_excludes_before_start(self):
        # 18:00-18:00 on Jan 19 does NOT cover 17:59 on Jan 19
        e = _entry("18:00", "18:00")
        dt = datetime(2026, 1, 19, 17, 59)
        assert _entry_covers_time(e, date(2026, 1, 19), dt) is False

    def test_full_24h_shift_excludes_at_end(self):
        # 18:00-18:00 on Jan 19 does NOT cover exactly 18:00 on Jan 20
        e = _entry("18:00", "18:00")
        dt = datetime(2026, 1, 20, 18, 0)
        assert _entry_covers_time(e, date(2026, 1, 19), dt) is False

    def test_partial_shift_1800_to_1200(self):
        # Captain works 18:00-12:00 on Jan 19 → covers 08:00 Jan 20
        e = _entry("18:00", "12:00")
        dt = datetime(2026, 1, 20, 8, 0)
        assert _entry_covers_time(e, date(2026, 1, 19), dt) is True

    def test_partial_shift_1800_to_1200_excludes_afternoon(self):
        # Captain works 18:00-12:00 on Jan 19 → does NOT cover 13:00 Jan 20
        e = _entry("18:00", "12:00")
        dt = datetime(2026, 1, 20, 13, 0)
        assert _entry_covers_time(e, date(2026, 1, 19), dt) is False

    def test_partial_shift_1930_to_0830(self):
        # FF works 19:30-08:30 on Jan 19 → covers 08:00 Jan 20
        e = _entry("19:30", "08:30")
        dt = datetime(2026, 1, 20, 8, 0)
        assert _entry_covers_time(e, date(2026, 1, 19), dt) is True

    def test_partial_shift_1930_to_0830_excludes_after(self):
        # FF works 19:30-08:30 on Jan 19 → does NOT cover 08:41 Jan 20
        e = _entry("19:30", "08:30")
        dt = datetime(2026, 1, 20, 8, 41)
        assert _entry_covers_time(e, date(2026, 1, 19), dt) is False

    def test_daytime_admin_shift(self):
        # Admin works 11:30-15:00 on Jan 20 → covers 12:00 Jan 20
        e = _entry("11:30", "15:00")
        dt = datetime(2026, 1, 20, 12, 0)
        assert _entry_covers_time(e, date(2026, 1, 20), dt) is True

    def test_daytime_admin_excludes_before(self):
        # Admin works 11:30-15:00 on Jan 20 → does NOT cover 08:41 Jan 20
        e = _entry("11:30", "15:00")
        dt = datetime(2026, 1, 20, 8, 41)
        assert _entry_covers_time(e, date(2026, 1, 20), dt) is False

    def test_daytime_admin_excludes_after(self):
        # Admin works 11:30-15:00 on Jan 20 → does NOT cover 16:00 Jan 20
        e = _entry("11:30", "15:00")
        dt = datetime(2026, 1, 20, 16, 0)
        assert _entry_covers_time(e, date(2026, 1, 20), dt) is False

    def test_empty_start_time(self):
        e = _entry("", "18:00")
        assert _entry_covers_time(e, date(2026, 1, 19), datetime(2026, 1, 19, 20, 0)) is False

    def test_empty_end_time(self):
        e = _entry("18:00", "")
        assert _entry_covers_time(e, date(2026, 1, 19), datetime(2026, 1, 19, 20, 0)) is False

    def test_new_shift_at_exact_start(self):
        # 18:00-18:00 on Jan 20 covers exactly 18:00 Jan 20
        e = _entry("18:00", "18:00")
        dt = datetime(2026, 1, 20, 18, 0)
        assert _entry_covers_time(e, date(2026, 1, 20), dt) is True


# ---------------------------------------------------------------------------
# get_for_time — integration tests with in-memory store
# ---------------------------------------------------------------------------


def _make_schedule(date_str: str, entries: list[ScheduleEntryCache]) -> DayScheduleCache:
    return DayScheduleCache(
        id=date_str,
        date=date_str,
        platoon="A",
        entries=entries,
        fetched_at=datetime.now(UTC),
    )


class TestGetForTime:
    """Test get_for_time with realistic fire department schedules."""

    async def test_morning_call_gets_previous_day_crew(self):
        """08:41 call on Jan 20 should get crew from Jan 19 schedule."""
        jan19 = _make_schedule(
            "2026-01-19",
            [
                _entry("18:00", "18:00", "Pollack"),  # chief, full shift
                _entry("18:00", "12:00", "Dodd"),  # captain, until noon
            ],
        )
        jan20 = _make_schedule(
            "2026-01-20",
            [
                _entry("18:00", "18:00", "English"),  # next shift, starts 18:00
            ],
        )
        async with ScheduleStore() as store:
            await store.upsert(jan19)
            await store.upsert(jan20)
            results = await store.get_for_time(datetime(2026, 1, 20, 8, 41))

        names = [e.name for e in results]
        assert "Pollack" in names
        assert "Dodd" in names
        assert "English" not in names

    async def test_evening_call_gets_current_day_crew(self):
        """20:00 call on Jan 20 should get crew from Jan 20 schedule."""
        jan19 = _make_schedule(
            "2026-01-19",
            [
                _entry("18:00", "18:00", "Pollack"),
            ],
        )
        jan20 = _make_schedule(
            "2026-01-20",
            [
                _entry("18:00", "18:00", "English"),
            ],
        )
        async with ScheduleStore() as store:
            await store.upsert(jan19)
            await store.upsert(jan20)
            results = await store.get_for_time(datetime(2026, 1, 20, 20, 0))

        names = [e.name for e in results]
        assert "English" in names
        assert "Pollack" not in names

    async def test_shift_boundary_new_crew_at_1800(self):
        """At exactly 18:00, new crew starts and old crew ends."""
        jan19 = _make_schedule(
            "2026-01-19",
            [
                _entry("18:00", "18:00", "Old Crew"),
            ],
        )
        jan20 = _make_schedule(
            "2026-01-20",
            [
                _entry("18:00", "18:00", "New Crew"),
            ],
        )
        async with ScheduleStore() as store:
            await store.upsert(jan19)
            await store.upsert(jan20)
            results = await store.get_for_time(datetime(2026, 1, 20, 18, 0))

        names = [e.name for e in results]
        assert "New Crew" in names
        assert "Old Crew" not in names

    async def test_partial_shift_ends_before_call(self):
        """FF on 19:30-08:30 shift should not show for 08:41 call."""
        jan19 = _make_schedule(
            "2026-01-19",
            [
                _entry("18:00", "18:00", "Pollack"),
                _entry("19:30", "08:30", "Howitt"),
            ],
        )
        async with ScheduleStore() as store:
            await store.upsert(jan19)
            results = await store.get_for_time(datetime(2026, 1, 20, 8, 41))

        names = [e.name for e in results]
        assert "Pollack" in names
        assert "Howitt" not in names

    async def test_daytime_admin_included(self):
        """Admin on 11:30-15:00 should show for 12:00 call."""
        jan20 = _make_schedule(
            "2026-01-20",
            [
                _entry("11:30", "15:00", "Taylor"),
            ],
        )
        async with ScheduleStore() as store:
            await store.upsert(jan20)
            results = await store.get_for_time(datetime(2026, 1, 20, 12, 0))

        names = [e.name for e in results]
        assert "Taylor" in names

    async def test_daytime_admin_not_started(self):
        """Admin on 11:30-15:00 should not show for 08:41 call."""
        jan20 = _make_schedule(
            "2026-01-20",
            [
                _entry("11:30", "15:00", "Taylor"),
            ],
        )
        async with ScheduleStore() as store:
            await store.upsert(jan20)
            results = await store.get_for_time(datetime(2026, 1, 20, 8, 41))

        names = [e.name for e in results]
        assert "Taylor" not in names

    async def test_no_schedules_returns_empty(self):
        async with ScheduleStore() as store:
            results = await store.get_for_time(datetime(2026, 1, 20, 8, 41))
        assert results == []

    async def test_mixed_both_days(self):
        """Morning call gets overnight crew + daytime admin from today."""
        jan19 = _make_schedule(
            "2026-01-19",
            [
                _entry("18:00", "18:00", "Overnight Chief"),
            ],
        )
        jan20 = _make_schedule(
            "2026-01-20",
            [
                _entry("07:00", "15:00", "Morning Admin"),
                _entry("18:00", "18:00", "Next Shift"),
            ],
        )
        async with ScheduleStore() as store:
            await store.upsert(jan19)
            await store.upsert(jan20)
            results = await store.get_for_time(datetime(2026, 1, 20, 9, 0))

        names = [e.name for e in results]
        assert "Overnight Chief" in names
        assert "Morning Admin" in names
        assert "Next Shift" not in names
