"""Tests for schedule cache models."""

from datetime import UTC, datetime, timedelta

from sjifire.mcp.schedule.models import DayScheduleCache, ScheduleEntryCache


class TestScheduleEntryCache:
    def test_basic_construction(self):
        entry = ScheduleEntryCache(
            name="John Doe",
            position="Firefighter",
            section="Station 31",
            start_time="08:00",
            end_time="08:00",
            platoon="A",
        )
        assert entry.name == "John Doe"
        assert entry.position == "Firefighter"
        assert entry.section == "Station 31"
        assert entry.start_time == "08:00"
        assert entry.end_time == "08:00"
        assert entry.platoon == "A"

    def test_defaults(self):
        entry = ScheduleEntryCache(
            name="Jane Smith",
            position="EMT",
            section="Station 32",
            start_time="19:00",
            end_time="07:00",
        )
        assert entry.platoon == ""


class TestDayScheduleCache:
    def _make_cache(self, **overrides) -> DayScheduleCache:
        defaults = {
            "id": "2026-02-12",
            "date": "2026-02-12",
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
            "fetched_at": datetime(2026, 2, 12, 10, 0, 0, tzinfo=UTC),
        }
        defaults.update(overrides)
        return DayScheduleCache(**defaults)

    def test_to_cosmos_roundtrip(self):
        doc = self._make_cache()
        cosmos_dict = doc.to_cosmos()

        assert isinstance(cosmos_dict, dict)
        assert cosmos_dict["date"] == "2026-02-12"
        assert cosmos_dict["platoon"] == "A"
        assert len(cosmos_dict["entries"]) == 1
        assert cosmos_dict["entries"][0]["name"] == "John Doe"

        restored = DayScheduleCache.from_cosmos(cosmos_dict)
        assert restored.date == doc.date
        assert restored.platoon == doc.platoon
        assert len(restored.entries) == 1
        assert restored.entries[0].name == "John Doe"
        assert restored.entries[0].position == "Firefighter"

    def test_from_cosmos(self):
        cosmos_dict = {
            "id": "2026-02-12",
            "date": "2026-02-12",
            "platoon": "B",
            "entries": [
                {
                    "name": "Jane Smith",
                    "position": "EMT",
                    "section": "Station 32",
                    "start_time": "19:00",
                    "end_time": "07:00",
                    "platoon": "B",
                },
            ],
            "fetched_at": "2026-02-12T10:00:00+00:00",
        }
        doc = DayScheduleCache.from_cosmos(cosmos_dict)
        assert doc.date == "2026-02-12"
        assert doc.platoon == "B"
        assert len(doc.entries) == 1
        assert doc.entries[0].name == "Jane Smith"

    def test_is_stale_fresh_data(self):
        doc = self._make_cache(fetched_at=datetime.now(UTC))
        assert doc.is_stale() is False

    def test_is_stale_old_data(self):
        old_time = datetime.now(UTC) - timedelta(hours=25)
        doc = self._make_cache(fetched_at=old_time)
        assert doc.is_stale() is True

    def test_is_stale_boundary(self):
        """Data fetched exactly 24 hours ago should be considered stale."""
        boundary_time = datetime.now(UTC) - timedelta(hours=24)
        doc = self._make_cache(fetched_at=boundary_time)
        assert doc.is_stale() is True
