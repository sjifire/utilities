"""Tests for the event-archive background task."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.events.models import EventRecord
from sjifire.ops.events.store import EventStore


@pytest.fixture(autouse=True)
def _clear_stores(monkeypatch):
    EventStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.setattr("sjifire.ops.cosmos.get_cosmos_container", _noop_container)
    yield
    EventStore._memory.clear()


async def _noop_container(name):
    return None


def _make_calendar_event(**overrides) -> dict:
    """Build a fake calendar event dict as returned by fetch_events."""
    defaults = {
        "event_id": "cal-123",
        "subject": "Ladder Training",
        "start": "2025-09-05T09:00:00",
        "end": "2025-09-05T12:00:00",
        "is_all_day": False,
        "location": "Station 31",
        "location_address": "",
        "body_preview": "Weekly ladder drill",
        "calendar_source": "Training",
    }
    defaults.update(overrides)
    return defaults


class TestEventArchive:
    async def test_creates_skeleton_for_unrecorded_events(self):
        from sjifire.ops.tasks.event_archive import event_archive

        events = [_make_calendar_event()]
        with patch(
            "sjifire.ops.events.calendar.fetch_events",
            AsyncMock(return_value=events),
        ):
            count = await event_archive()

        assert count == 1

        async with EventStore() as store:
            rec = await store.get_by_calendar_event_id("cal-123")
        assert rec is not None
        assert rec.subject == "Ladder Training"
        assert rec.created_by == "system"
        assert rec.calendar_source == "Training"
        assert rec.location == "Station 31"

    async def test_skips_events_with_existing_records(self):
        from sjifire.ops.tasks.event_archive import event_archive

        rec = EventRecord(
            calendar_event_id="cal-123",
            subject="Ladder Training",
            event_date=datetime(2025, 9, 5, 9, 0, tzinfo=UTC),
            created_by="admin@sjifire.org",
        )
        async with EventStore() as store:
            await store.upsert(rec)

        events = [_make_calendar_event()]
        with patch(
            "sjifire.ops.events.calendar.fetch_events",
            AsyncMock(return_value=events),
        ):
            count = await event_archive()

        assert count == 0

    async def test_returns_zero_when_no_events(self):
        from sjifire.ops.tasks.event_archive import event_archive

        with patch(
            "sjifire.ops.events.calendar.fetch_events",
            AsyncMock(return_value=[]),
        ):
            count = await event_archive()

        assert count == 0

    async def test_skips_events_without_event_id(self):
        from sjifire.ops.tasks.event_archive import event_archive

        events = [_make_calendar_event(event_id=None)]
        with patch(
            "sjifire.ops.events.calendar.fetch_events",
            AsyncMock(return_value=events),
        ):
            count = await event_archive()

        assert count == 0

    async def test_skeleton_metadata(self):
        from sjifire.ops.tasks.event_archive import event_archive

        events = [
            _make_calendar_event(
                event_id="cal-456",
                subject="Water Supply Drill",
                start="2025-09-10T14:00:00",
                end="2025-09-10T17:00:00",
                location="Hydrant Park",
                body_preview="Practice water supply operations",
                calendar_source="Training",
            )
        ]
        with patch(
            "sjifire.ops.events.calendar.fetch_events",
            AsyncMock(return_value=events),
        ):
            count = await event_archive()

        assert count == 1

        async with EventStore() as store:
            rec = await store.get_by_calendar_event_id("cal-456")
        assert rec.subject == "Water Supply Drill"
        assert rec.location == "Hydrant Park"
        assert rec.description == "Practice water supply operations"
        assert rec.calendar_source == "Training"
        assert rec.created_by == "system"
        assert rec.attendees == []
        assert rec.attachments == []

    async def test_multiple_events_mixed(self):
        """Mix of new and existing events — only new ones get archived."""
        from sjifire.ops.tasks.event_archive import event_archive

        rec = EventRecord(
            calendar_event_id="cal-existing",
            subject="Existing Event",
            event_date=datetime(2025, 9, 1, 9, 0, tzinfo=UTC),
            created_by="admin@sjifire.org",
        )
        async with EventStore() as store:
            await store.upsert(rec)

        events = [
            _make_calendar_event(event_id="cal-existing", subject="Existing Event"),
            _make_calendar_event(event_id="cal-new-1", subject="New Event 1"),
            _make_calendar_event(event_id="cal-new-2", subject="New Event 2"),
        ]
        with patch(
            "sjifire.ops.events.calendar.fetch_events",
            AsyncMock(return_value=events),
        ):
            count = await event_archive()

        assert count == 2
