"""Tests for sjifire.calendar.sync module."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.aladtec.schedule import DaySchedule, ScheduleEntry
from sjifire.calendar.models import AllDayDutyEvent, CrewMember
from sjifire.calendar.sync import (
    EXCLUDED_SECTIONS,
    CalendarSync,
    is_filled_entry,
    is_unfilled_position,
    should_exclude_section,
)


class TestShouldExcludeSection:
    """Tests for should_exclude_section function."""

    def test_excludes_administration(self):
        """Administration is excluded."""
        assert should_exclude_section("Administration") is True

    def test_excludes_operations(self):
        """Operations is excluded."""
        assert should_exclude_section("Operations") is True

    def test_excludes_prevention(self):
        """Prevention is excluded."""
        assert should_exclude_section("Prevention") is True

    def test_excludes_training(self):
        """Training is excluded."""
        assert should_exclude_section("Training") is True

    def test_excludes_trades(self):
        """Trades is excluded."""
        assert should_exclude_section("Trades") is True

    def test_excludes_state_mobe(self):
        """State Mobe is excluded."""
        assert should_exclude_section("State Mobe") is True

    def test_excludes_time_off(self):
        """Time Off is excluded."""
        assert should_exclude_section("Time Off") is True

    def test_includes_s31(self):
        """S31 is not excluded."""
        assert should_exclude_section("S31") is False

    def test_includes_s32(self):
        """S32 is not excluded."""
        assert should_exclude_section("S32") is False

    def test_includes_chief_officer(self):
        """Chief Officer is not excluded."""
        assert should_exclude_section("Chief Officer") is False

    def test_includes_backup_duty(self):
        """Backup Duty is not excluded."""
        assert should_exclude_section("Backup Duty") is False

    def test_excluded_sections_list(self):
        """Verify all excluded sections are in the list."""
        expected = [
            "Administration",
            "Operations",
            "Prevention",
            "Training",
            "Trades",
            "State Mobe",
            "Time Off",
        ]
        for section in expected:
            assert section in EXCLUDED_SECTIONS


class TestIsUnfilledPosition:
    """Tests for is_unfilled_position function."""

    def test_unfilled_with_slash(self):
        """Unfilled positions have ' / ' in name."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="S31 / Firefighter",
            start_time="18:00",
            end_time="18:00",
        )
        assert is_unfilled_position(entry) is True

    def test_filled_regular_name(self):
        """Regular names don't have ' / '."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        assert is_unfilled_position(entry) is False

    def test_filled_name_with_slash_no_spaces(self):
        """Names with slash but no spaces are filled."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John/Jane Doe",  # Unlikely but test edge case
            start_time="18:00",
            end_time="18:00",
        )
        assert is_unfilled_position(entry) is False


class TestIsFilledEntry:
    """Tests for is_filled_entry function."""

    def test_filled_with_name(self):
        """Entry with name is filled."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        assert is_filled_entry(entry) is True

    def test_not_filled_empty_name(self):
        """Entry with empty name is not filled."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="",
            start_time="18:00",
            end_time="18:00",
        )
        assert is_filled_entry(entry) is False

    def test_not_filled_unfilled_position(self):
        """Unfilled position placeholder is not filled."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="S31 / Firefighter",
            start_time="18:00",
            end_time="18:00",
        )
        assert is_filled_entry(entry) is False


class TestCalendarSyncFiltering:
    """Tests for CalendarSync filtering methods."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create CalendarSync instance."""
        with (
            patch("sjifire.calendar.sync.ClientSecretCredential"),
            patch("sjifire.calendar.sync.GraphServiceClient"),
        ):
            return CalendarSync()

    @pytest.fixture
    def sample_day_schedule(self):
        """Create sample day schedule with mixed entries."""
        entries = [
            # Filled station entries
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Captain",
                name="John Doe",
                start_time="18:00",
                end_time="18:00",
            ),
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Firefighter",
                name="Jane Smith",
                start_time="18:00",
                end_time="18:00",
            ),
            # Unfilled position
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="EMT",
                name="S31 / EMT",
                start_time="18:00",
                end_time="18:00",
            ),
            # Excluded section
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="Administration",
                position="Chief",
                name="Bob Chief",
                start_time="18:00",
                end_time="18:00",
            ),
            # Empty name
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S32",
                position="Firefighter",
                name="",
                start_time="18:00",
                end_time="18:00",
            ),
        ]
        return DaySchedule(date=date(2026, 2, 1), platoon="A", entries=entries)

    def test_get_filled_entries(self, calendar_sync, sample_day_schedule):
        """Get filled entries excludes unfilled and excluded sections."""
        filled = calendar_sync._get_filled_entries(sample_day_schedule)

        # Should only include John Doe and Jane Smith
        assert len(filled) == 2
        names = [e.name for e in filled]
        assert "John Doe" in names
        assert "Jane Smith" in names
        assert "S31 / EMT" not in names
        assert "Bob Chief" not in names

    def test_entries_to_crew_deduplicates(self, calendar_sync):
        """Entries to crew deduplicates by section/position/name."""
        entries = [
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Captain",
                name="John Doe",
                start_time="18:00",
                end_time="18:00",
            ),
            # Duplicate
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Captain",
                name="John Doe",
                start_time="06:00",
                end_time="18:00",
            ),
        ]
        user_cache = {}
        crew = calendar_sync._entries_to_crew(entries, user_cache)

        assert len(crew["S31"]) == 1
        assert crew["S31"][0].name == "John Doe"

    def test_entries_to_crew_groups_by_section(self, calendar_sync):
        """Entries to crew groups by section."""
        entries = [
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Captain",
                name="John Doe",
                start_time="18:00",
                end_time="18:00",
            ),
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S32",
                position="Captain",
                name="Jane Smith",
                start_time="18:00",
                end_time="18:00",
            ),
        ]
        user_cache = {}
        crew = calendar_sync._entries_to_crew(entries, user_cache)

        assert "S31" in crew
        assert "S32" in crew
        assert len(crew["S31"]) == 1
        assert len(crew["S32"]) == 1

    def test_entries_to_crew_creates_crew_members(self, calendar_sync):
        """Entries to crew creates CrewMember objects."""
        entries = [
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Captain",
                name="John Doe",
                start_time="18:00",
                end_time="18:00",
            ),
        ]
        user_cache = {}
        crew = calendar_sync._entries_to_crew(entries, user_cache)

        member = crew["S31"][0]
        assert isinstance(member, CrewMember)
        assert member.name == "John Doe"
        assert member.position == "Captain"

    def test_entries_to_crew_looks_up_contact(self, calendar_sync):
        """Entries to crew looks up contact info."""
        entries = [
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Captain",
                name="John Doe",
                start_time="18:00",
                end_time="18:00",
            ),
        ]
        user_cache = {
            "John Doe": {"email": "john@test.com", "phone": "555-1234"},
        }
        crew = calendar_sync._entries_to_crew(entries, user_cache)

        member = crew["S31"][0]
        assert member.email == "john@test.com"
        assert member.phone == "555-1234"


class TestCalendarSyncContactLookup:
    """Tests for contact lookup in CalendarSync."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create CalendarSync instance."""
        with (
            patch("sjifire.calendar.sync.ClientSecretCredential"),
            patch("sjifire.calendar.sync.GraphServiceClient"),
        ):
            return CalendarSync()

    def test_lookup_exact_match(self, calendar_sync):
        """Look up contact by exact name match."""
        user_cache = {
            "John Doe": {"email": "john@test.com", "phone": "555-1234"},
        }
        email, phone = calendar_sync._lookup_contact("John Doe", user_cache)

        assert email == "john@test.com"
        assert phone == "555-1234"

    def test_lookup_case_insensitive(self, calendar_sync):
        """Look up contact case-insensitively."""
        user_cache = {
            "John Doe": {"email": "john@test.com", "phone": "555-1234"},
        }
        email, phone = calendar_sync._lookup_contact("john doe", user_cache)

        assert email == "john@test.com"
        assert phone == "555-1234"

    def test_lookup_partial_match(self, calendar_sync):
        """Look up contact by partial name match."""
        user_cache = {
            "Capt John Doe": {"email": "john@test.com", "phone": "555-1234"},
        }
        email, phone = calendar_sync._lookup_contact("John Doe", user_cache)

        assert email == "john@test.com"
        assert phone == "555-1234"

    def test_lookup_not_found(self, calendar_sync):
        """Look up returns None for unknown name."""
        user_cache = {
            "Jane Smith": {"email": "jane@test.com", "phone": "555-5678"},
        }
        email, phone = calendar_sync._lookup_contact("John Doe", user_cache)

        assert email is None
        assert phone is None

    def test_lookup_missing_email(self, calendar_sync):
        """Look up with missing email."""
        user_cache = {
            "John Doe": {"phone": "555-1234"},
        }
        email, phone = calendar_sync._lookup_contact("John Doe", user_cache)

        assert email is None
        assert phone == "555-1234"

    def test_lookup_missing_phone(self, calendar_sync):
        """Look up with missing phone."""
        user_cache = {
            "John Doe": {"email": "john@test.com"},
        }
        email, phone = calendar_sync._lookup_contact("John Doe", user_cache)

        assert email == "john@test.com"
        assert phone is None


class TestCalendarSyncConvertSchedules:
    """Tests for schedule to event conversion."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create CalendarSync instance."""
        with (
            patch("sjifire.calendar.sync.ClientSecretCredential"),
            patch("sjifire.calendar.sync.GraphServiceClient"),
        ):
            return CalendarSync()

    @pytest.fixture
    def sample_schedules(self):
        """Create sample schedules for multiple days."""
        return [
            DaySchedule(
                date=date(2026, 2, 1),
                platoon="A",
                entries=[
                    ScheduleEntry(
                        date=date(2026, 2, 1),
                        section="S31",
                        position="Captain",
                        name="John Doe",
                        start_time="18:00",
                        end_time="18:00",
                    ),
                ],
            ),
            DaySchedule(
                date=date(2026, 2, 2),
                platoon="B",
                entries=[
                    ScheduleEntry(
                        date=date(2026, 2, 2),
                        section="S31",
                        position="Captain",
                        name="Jane Smith",
                        start_time="18:00",
                        end_time="18:00",
                    ),
                ],
            ),
        ]

    def test_convert_schedules_creates_events(self, calendar_sync, sample_schedules):
        """Convert schedules creates events."""
        events = calendar_sync.convert_schedules_to_events(sample_schedules, {})

        # Should create 2 events (one per day)
        assert len(events) == 2

    def test_convert_schedules_event_dates(self, calendar_sync, sample_schedules):
        """Events have correct dates."""
        events = calendar_sync.convert_schedules_to_events(sample_schedules, {})

        dates = [e.event_date for e in events]
        assert date(2026, 2, 1) in dates
        assert date(2026, 2, 2) in dates

    def test_convert_schedules_until_1800_from_previous(self, calendar_sync, sample_schedules):
        """Until 1800 crew comes from previous day's shift."""
        events = calendar_sync.convert_schedules_to_events(sample_schedules, {})

        # Feb 2 event should have Feb 1 crew as "until 1800"
        feb2_event = next(e for e in events if e.event_date == date(2026, 2, 2))
        assert feb2_event.until_1800_platoon == "A"
        assert "S31" in feb2_event.until_1800_crew
        assert feb2_event.until_1800_crew["S31"][0].name == "John Doe"

    def test_convert_schedules_from_1800_from_today(self, calendar_sync, sample_schedules):
        """From 1800 crew comes from today's shift."""
        events = calendar_sync.convert_schedules_to_events(sample_schedules, {})

        # Feb 2 event should have Feb 2 crew as "from 1800"
        feb2_event = next(e for e in events if e.event_date == date(2026, 2, 2))
        assert feb2_event.from_1800_platoon == "B"
        assert "S31" in feb2_event.from_1800_crew
        assert feb2_event.from_1800_crew["S31"][0].name == "Jane Smith"

    def test_convert_schedules_empty_list(self, calendar_sync):
        """Convert empty schedules returns empty list."""
        events = calendar_sync.convert_schedules_to_events([], {})
        assert events == []

    def test_convert_schedules_sorted_by_date(self, calendar_sync, sample_schedules):
        """Events are sorted by date."""
        # Reverse the schedule order to test sorting
        reversed_schedules = list(reversed(sample_schedules))
        events = calendar_sync.convert_schedules_to_events(reversed_schedules, {})

        assert events[0].event_date < events[1].event_date


class TestCalendarSyncGraphAPI:
    """Tests for CalendarSync Graph API methods."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create CalendarSync with mocked Graph client."""
        with (
            patch("sjifire.calendar.sync.ClientSecretCredential"),
            patch("sjifire.calendar.sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = CalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_event(self):
        """Create sample AllDayDutyEvent."""
        return AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew={"S31": [CrewMember(name="John Doe", position="Captain")]},
            from_1800_platoon="B",
            from_1800_crew={"S31": [CrewMember(name="Jane Smith", position="Captain")]},
        )

    @pytest.mark.asyncio
    async def test_load_user_contacts_caches_results(self, calendar_sync):
        """User contacts are cached after first load."""
        # Setup mock
        mock_user = MagicMock()
        mock_user.display_name = "John Doe"
        mock_user.mail = "john@test.com"
        mock_user.mobile_phone = "555-1234"

        mock_result = MagicMock()
        mock_result.value = [mock_user]

        calendar_sync.client.users.get = AsyncMock(return_value=mock_result)

        # First call loads from API
        result1 = await calendar_sync._load_user_contacts()
        assert "John Doe" in result1

        # Second call uses cache (no additional API call)
        result2 = await calendar_sync._load_user_contacts()
        assert result2 is result1
        assert calendar_sync.client.users.get.call_count == 1

    @pytest.mark.asyncio
    async def test_load_user_contacts_handles_error(self, calendar_sync):
        """User contacts returns empty dict on error."""
        calendar_sync.client.users.get = AsyncMock(side_effect=Exception("API Error"))

        result = await calendar_sync._load_user_contacts()
        assert result == {}

    @pytest.mark.asyncio
    async def test_load_user_contacts_extracts_plain_name(self, calendar_sync):
        """User contacts stores both display name and plain name."""
        mock_user = MagicMock()
        mock_user.display_name = "Capt John Doe"
        mock_user.mail = "john@test.com"
        mock_user.mobile_phone = "555-1234"

        mock_result = MagicMock()
        mock_result.value = [mock_user]

        calendar_sync.client.users.get = AsyncMock(return_value=mock_result)

        result = await calendar_sync._load_user_contacts()

        # Both full name and plain name should be cached
        assert "Capt John Doe" in result
        assert "John Doe" in result

    @pytest.mark.asyncio
    async def test_get_existing_events_returns_dict(self, calendar_sync):
        """Get existing events returns date to ID mapping."""
        mock_event = MagicMock()
        mock_event.id = "event-123"
        mock_event.start = MagicMock()
        mock_event.start.date_time = "2026-02-01T00:00:00"

        mock_result = MagicMock()
        mock_result.value = [mock_event]

        calendar_sync.client.users.by_user_id.return_value.calendar_view.get = AsyncMock(
            return_value=mock_result
        )

        result = await calendar_sync.get_existing_events(date(2026, 2, 1), date(2026, 2, 28))

        assert date(2026, 2, 1) in result
        assert result[date(2026, 2, 1)] == "event-123"

    @pytest.mark.asyncio
    async def test_get_existing_events_handles_error(self, calendar_sync):
        """Get existing events returns empty dict on error."""
        calendar_sync.client.users.by_user_id.return_value.calendar_view.get = AsyncMock(
            side_effect=Exception("API Error")
        )

        result = await calendar_sync.get_existing_events(date(2026, 2, 1), date(2026, 2, 28))
        assert result == {}

    @pytest.mark.asyncio
    async def test_create_event_returns_id(self, calendar_sync, sample_event):
        """Create event returns event ID on success."""
        mock_result = MagicMock()
        mock_result.id = "new-event-123"

        calendar_sync.client.users.by_user_id.return_value.events.post = AsyncMock(
            return_value=mock_result
        )

        result = await calendar_sync.create_event(sample_event)
        assert result == "new-event-123"

    @pytest.mark.asyncio
    async def test_create_event_returns_none_on_error(self, calendar_sync, sample_event):
        """Create event returns None on error."""
        calendar_sync.client.users.by_user_id.return_value.events.post = AsyncMock(
            side_effect=Exception("API Error")
        )

        result = await calendar_sync.create_event(sample_event)
        assert result is None

    @pytest.mark.asyncio
    async def test_update_event_returns_true(self, calendar_sync, sample_event):
        """Update event returns True on success."""
        sample_event.event_id = "existing-123"

        calendar_sync.client.users.by_user_id.return_value.events.by_event_id.return_value.patch = (
            AsyncMock()
        )

        result = await calendar_sync.update_event(sample_event)
        assert result is True

    @pytest.mark.asyncio
    async def test_update_event_returns_false_without_id(self, calendar_sync, sample_event):
        """Update event returns False without event_id."""
        sample_event.event_id = None

        result = await calendar_sync.update_event(sample_event)
        assert result is False

    @pytest.mark.asyncio
    async def test_update_event_returns_false_on_error(self, calendar_sync, sample_event):
        """Update event returns False on error."""
        sample_event.event_id = "existing-123"

        calendar_sync.client.users.by_user_id.return_value.events.by_event_id.return_value.patch = (
            AsyncMock(side_effect=Exception("API Error"))
        )

        result = await calendar_sync.update_event(sample_event)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_event_returns_true(self, calendar_sync):
        """Delete event returns True on success."""
        calendar_sync.client.users.by_user_id.return_value.events.by_event_id.return_value.delete = AsyncMock()

        result = await calendar_sync.delete_event("event-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_event_returns_false_on_error(self, calendar_sync):
        """Delete event returns False on error."""
        calendar_sync.client.users.by_user_id.return_value.events.by_event_id.return_value.delete = AsyncMock(
            side_effect=Exception("API Error")
        )

        result = await calendar_sync.delete_event("event-123")
        assert result is False


class TestCalendarSyncBatchOperations:
    """Tests for CalendarSync batch operations."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create CalendarSync with mocked Graph client."""
        with (
            patch("sjifire.calendar.sync.ClientSecretCredential"),
            patch("sjifire.calendar.sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = CalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_events(self):
        """Create multiple sample events."""
        return [
            AllDayDutyEvent(
                event_date=date(2026, 2, i),
                until_1800_platoon="A",
                until_1800_crew={},
                from_1800_platoon="B",
                from_1800_crew={},
            )
            for i in range(1, 6)  # 5 events
        ]

    @pytest.mark.asyncio
    async def test_create_events_batch_success(self, calendar_sync, sample_events):
        """Batch create returns success count."""
        mock_result = MagicMock()
        mock_result.id = "new-id"

        calendar_sync.client.users.by_user_id.return_value.events.post = AsyncMock(
            return_value=mock_result
        )

        count, errors = await calendar_sync.create_events_batch(sample_events)

        assert count == 5
        assert errors == []

    @pytest.mark.asyncio
    async def test_create_events_batch_partial_failure(self, calendar_sync, sample_events):
        """Batch create handles partial failures."""
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise Exception("API Error")
            mock_result = MagicMock()
            mock_result.id = f"id-{call_count}"
            return mock_result

        calendar_sync.client.users.by_user_id.return_value.events.post = mock_post

        count, errors = await calendar_sync.create_events_batch(sample_events)

        assert count == 4
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_create_events_batch_empty_list(self, calendar_sync):
        """Batch create handles empty list."""
        count, errors = await calendar_sync.create_events_batch([])

        assert count == 0
        assert errors == []

    @pytest.mark.asyncio
    async def test_delete_events_batch_success(self, calendar_sync):
        """Batch delete returns success count."""
        calendar_sync.client.users.by_user_id.return_value.events.by_event_id.return_value.delete = AsyncMock()

        events_to_delete = {
            date(2026, 2, 1): "id-1",
            date(2026, 2, 2): "id-2",
            date(2026, 2, 3): "id-3",
        }

        count, errors = await calendar_sync.delete_events_batch(events_to_delete)

        assert count == 3
        assert errors == []

    @pytest.mark.asyncio
    async def test_delete_events_batch_empty(self, calendar_sync):
        """Batch delete handles empty dict."""
        count, errors = await calendar_sync.delete_events_batch({})

        assert count == 0
        assert errors == []


class TestCalendarSyncSyncEvents:
    """Tests for sync_events orchestration."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create CalendarSync with mocked methods."""
        with (
            patch("sjifire.calendar.sync.ClientSecretCredential"),
            patch("sjifire.calendar.sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = CalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_events(self):
        """Create sample events for syncing."""
        return [
            AllDayDutyEvent(
                event_date=date(2026, 2, 1),
                until_1800_platoon="A",
                until_1800_crew={},
                from_1800_platoon="B",
                from_1800_crew={},
            ),
            AllDayDutyEvent(
                event_date=date(2026, 2, 2),
                until_1800_platoon="B",
                until_1800_crew={},
                from_1800_platoon="A",
                from_1800_crew={},
            ),
        ]

    @pytest.mark.asyncio
    async def test_sync_events_creates_new(self, calendar_sync, sample_events):
        """Sync creates events that don't exist."""
        # No existing events
        calendar_sync.get_existing_events = AsyncMock(return_value={})
        calendar_sync.create_events_batch = AsyncMock(return_value=(2, []))
        calendar_sync.update_events_batch = AsyncMock(return_value=(0, []))

        result = await calendar_sync.sync_events(sample_events, date(2026, 2, 1), date(2026, 2, 28))

        assert result.events_created == 2
        assert result.events_updated == 0
        calendar_sync.create_events_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_events_updates_existing(self, calendar_sync, sample_events):
        """Sync updates events that exist."""
        # All events exist
        calendar_sync.get_existing_events = AsyncMock(
            return_value={
                date(2026, 2, 1): "id-1",
                date(2026, 2, 2): "id-2",
            }
        )
        calendar_sync.create_events_batch = AsyncMock(return_value=(0, []))
        calendar_sync.update_events_batch = AsyncMock(return_value=(2, []))

        result = await calendar_sync.sync_events(sample_events, date(2026, 2, 1), date(2026, 2, 28))

        assert result.events_created == 0
        assert result.events_updated == 2
        calendar_sync.update_events_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_events_dry_run(self, calendar_sync, sample_events):
        """Dry run doesn't call batch methods."""
        calendar_sync.get_existing_events = AsyncMock(return_value={})
        calendar_sync.create_events_batch = AsyncMock()
        calendar_sync.update_events_batch = AsyncMock()

        result = await calendar_sync.sync_events(
            sample_events, date(2026, 2, 1), date(2026, 2, 28), dry_run=True
        )

        assert result.events_created == 2
        calendar_sync.create_events_batch.assert_not_called()
        calendar_sync.update_events_batch.assert_not_called()


class TestCalendarSyncDeleteDateRange:
    """Tests for delete_date_range method."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create CalendarSync with mocked methods."""
        with (
            patch("sjifire.calendar.sync.ClientSecretCredential"),
            patch("sjifire.calendar.sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = CalendarSync()
            sync.client = mock_client
            return sync

    def test_delete_date_range_no_events(self, calendar_sync):
        """Delete with no events returns empty result."""
        with patch.object(calendar_sync, "get_existing_events", new=AsyncMock(return_value={})):
            result = calendar_sync.delete_date_range(date(2026, 2, 1), date(2026, 2, 28))

        assert result.events_deleted == 0
        assert result.errors == []

    def test_delete_date_range_dry_run(self, calendar_sync):
        """Dry run counts events without deleting."""
        with patch.object(
            calendar_sync,
            "get_existing_events",
            new=AsyncMock(
                return_value={
                    date(2026, 2, 1): "id-1",
                    date(2026, 2, 2): "id-2",
                }
            ),
        ):
            result = calendar_sync.delete_date_range(
                date(2026, 2, 1), date(2026, 2, 28), dry_run=True
            )

        assert result.events_deleted == 2

    def test_delete_date_range_success(self, calendar_sync):
        """Delete calls batch delete and returns count."""
        with (
            patch.object(
                calendar_sync,
                "get_existing_events",
                new=AsyncMock(
                    return_value={
                        date(2026, 2, 1): "id-1",
                        date(2026, 2, 2): "id-2",
                    }
                ),
            ),
            patch.object(
                calendar_sync,
                "delete_events_batch",
                new=AsyncMock(return_value=(2, [])),
            ),
        ):
            result = calendar_sync.delete_date_range(date(2026, 2, 1), date(2026, 2, 28))

        assert result.events_deleted == 2
        assert result.errors == []
