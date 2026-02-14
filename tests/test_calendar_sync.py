"""Tests for sjifire.calendar.duty_sync module."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.aladtec.schedule_scraper import DaySchedule, ScheduleEntry
from sjifire.calendar.duty_sync import (
    DutyCalendarSync,
    is_filled_entry,
    is_operational_section,
    is_unfilled_position,
    should_exclude_section,
)
from sjifire.calendar.models import AllDayDutyEvent, CrewMember


class TestIsOperationalSection:
    """Tests for is_operational_section function.

    Uses a denylist approach: all sections are included except known
    non-operational ones (Administration, Prevention, Training,
    Trades, Time Off).
    """

    def test_station_s31_is_operational(self):
        """S31 (station) is operational."""
        assert is_operational_section("S31") is True

    def test_station_s32_is_operational(self):
        """S32 (station) is operational."""
        assert is_operational_section("S32") is True

    def test_station_s33_is_operational(self):
        """S33 (station) is operational."""
        assert is_operational_section("S33") is True

    def test_station_s35_is_operational(self):
        """S35 (station) is operational."""
        assert is_operational_section("S35") is True

    def test_station_s36_is_operational(self):
        """S36 (station) is operational."""
        assert is_operational_section("S36") is True

    def test_station_lowercase_is_operational(self):
        """Lowercase station names are operational."""
        assert is_operational_section("s31") is True

    def test_station_word_is_operational(self):
        """'Station 31' format is operational."""
        assert is_operational_section("Station 31") is True

    def test_chief_officer_is_operational(self):
        """Chief Officer is operational."""
        assert is_operational_section("Chief Officer") is True

    def test_chief_on_call_is_operational(self):
        """Chief on Call is operational."""
        assert is_operational_section("Chief on Call") is True

    def test_backup_duty_is_operational(self):
        """Backup Duty is operational."""
        assert is_operational_section("Backup Duty") is True

    def test_backup_is_operational(self):
        """Backup is operational."""
        assert is_operational_section("Backup") is True

    def test_support_is_operational(self):
        """Support is operational."""
        assert is_operational_section("Support") is True

    def test_marine_is_operational(self):
        """Marine section is operational."""
        assert is_operational_section("Marine") is True

    def test_standby_is_operational(self):
        """Standby section is operational."""
        assert is_operational_section("Standby") is True

    def test_administration_is_not_operational(self):
        """Administration is not operational."""
        assert is_operational_section("Administration") is False

    def test_operations_is_operational(self):
        """Operations section is operational."""
        assert is_operational_section("Operations") is True

    def test_prevention_is_not_operational(self):
        """Prevention is not operational."""
        assert is_operational_section("Prevention") is False

    def test_training_is_not_operational(self):
        """Training is not operational."""
        assert is_operational_section("Training") is False

    def test_trades_is_not_operational(self):
        """Trades is not operational."""
        assert is_operational_section("Trades") is False

    def test_state_mobe_is_operational(self):
        """State Mobe (wildland mobilization) is operational."""
        assert is_operational_section("State Mobe") is True

    def test_mobilization_is_operational(self):
        """Mobilization sections are operational."""
        assert is_operational_section("Mobilization") is True

    def test_time_off_is_not_operational(self):
        """Time Off is not operational."""
        assert is_operational_section("Time Off") is False


class TestShouldExcludeSection:
    """Tests for should_exclude_section (inverse of is_operational_section)."""

    def test_excludes_non_operational(self):
        """Non-operational sections are excluded."""
        assert should_exclude_section("Administration") is True
        assert should_exclude_section("Training") is True
        assert should_exclude_section("Time Off") is True

    def test_includes_operational(self):
        """Operational sections are not excluded."""
        assert should_exclude_section("S31") is False
        assert should_exclude_section("Chief Officer") is False
        assert should_exclude_section("Backup Duty") is False


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


class TestDutyCalendarSyncFiltering:
    """Tests for DutyCalendarSync filtering methods."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync instance."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient"),
        ):
            return DutyCalendarSync()

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


class TestDutyCalendarSyncContactLookup:
    """Tests for contact lookup in DutyCalendarSync."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync instance."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient"),
        ):
            return DutyCalendarSync()

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


class TestDutyCalendarSyncConvertSchedules:
    """Tests for schedule to event conversion."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync instance."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient"),
        ):
            return DutyCalendarSync()

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
        assert feb2_event.until_platoon == "A"
        assert "S31" in feb2_event.until_crew
        assert feb2_event.until_crew["S31"][0].name == "John Doe"

    def test_convert_schedules_from_1800_from_today(self, calendar_sync, sample_schedules):
        """From 1800 crew comes from today's shift."""
        events = calendar_sync.convert_schedules_to_events(sample_schedules, {})

        # Feb 2 event should have Feb 2 crew as "from 1800"
        feb2_event = next(e for e in events if e.event_date == date(2026, 2, 2))
        assert feb2_event.from_platoon == "B"
        assert "S31" in feb2_event.from_crew
        assert feb2_event.from_crew["S31"][0].name == "Jane Smith"

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


class TestDutyCalendarSyncGraphAPI:
    """Tests for DutyCalendarSync Graph API methods."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync with mocked Graph client."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = DutyCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_event(self):
        """Create sample AllDayDutyEvent."""
        return AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_platoon="A",
            until_crew={"S31": [CrewMember(name="John Doe", position="Captain")]},
            from_platoon="B",
            from_crew={"S31": [CrewMember(name="Jane Smith", position="Captain")]},
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
        """Get existing events returns date to (ID, body) mapping."""
        mock_event = MagicMock()
        mock_event.id = "event-123"
        mock_event.start = MagicMock()
        mock_event.start.date_time = "2026-02-01T00:00:00"
        mock_event.body = MagicMock()
        mock_event.body.content = "<html>test body</html>"

        mock_result = MagicMock()
        mock_result.value = [mock_event]

        calendar_sync.client.users.by_user_id.return_value.calendar_view.get = AsyncMock(
            return_value=mock_result
        )

        result = await calendar_sync.get_existing_events(date(2026, 2, 1), date(2026, 2, 28))

        assert date(2026, 2, 1) in result
        event_id, body = result[date(2026, 2, 1)]
        assert event_id == "event-123"
        assert body == "<html>test body</html>"

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


class TestDutyCalendarSyncBatchOperations:
    """Tests for DutyCalendarSync batch operations."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync with mocked Graph client."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = DutyCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_events(self):
        """Create multiple sample events."""
        return [
            AllDayDutyEvent(
                event_date=date(2026, 2, i),
                until_platoon="A",
                until_crew={},
                from_platoon="B",
                from_crew={},
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


class TestDutyCalendarSyncSyncEvents:
    """Tests for sync_events orchestration."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync with mocked methods."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = DutyCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_events(self):
        """Create sample events for syncing."""
        return [
            AllDayDutyEvent(
                event_date=date(2026, 2, 1),
                until_platoon="A",
                until_crew={},
                from_platoon="B",
                from_crew={},
            ),
            AllDayDutyEvent(
                event_date=date(2026, 2, 2),
                until_platoon="B",
                until_crew={},
                from_platoon="A",
                from_crew={},
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
        """Sync updates events when body content differs."""
        # All events exist with different body content
        calendar_sync.get_existing_events = AsyncMock(
            return_value={
                date(2026, 2, 1): ("id-1", "<html>old body 1</html>"),
                date(2026, 2, 2): ("id-2", "<html>old body 2</html>"),
            }
        )
        calendar_sync.create_events_batch = AsyncMock(return_value=(0, []))
        calendar_sync.update_events_batch = AsyncMock(return_value=(2, []))

        result = await calendar_sync.sync_events(sample_events, date(2026, 2, 1), date(2026, 2, 28))

        assert result.events_created == 0
        assert result.events_updated == 2
        calendar_sync.update_events_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_events_skips_unchanged(self, calendar_sync, sample_events):
        """Sync skips events when body content is identical."""
        # Mock existing events with same body as new events
        calendar_sync.get_existing_events = AsyncMock(
            return_value={
                date(2026, 2, 1): ("id-1", sample_events[0].body_html),
                date(2026, 2, 2): ("id-2", sample_events[1].body_html),
            }
        )
        calendar_sync.create_events_batch = AsyncMock(return_value=(0, []))
        calendar_sync.update_events_batch = AsyncMock(return_value=(0, []))

        result = await calendar_sync.sync_events(sample_events, date(2026, 2, 1), date(2026, 2, 28))

        assert result.events_created == 0
        assert result.events_updated == 0
        assert result.events_unchanged == 2
        calendar_sync.update_events_batch.assert_not_called()

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


class TestDutyCalendarSyncDeleteDateRange:
    """Tests for delete_date_range method."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync with mocked methods."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = DutyCalendarSync()
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
                    date(2026, 2, 1): ("id-1", "body1"),
                    date(2026, 2, 2): ("id-2", "body2"),
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
                        date(2026, 2, 1): ("id-1", "body1"),
                        date(2026, 2, 2): ("id-2", "body2"),
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


class TestDutyCalendarSyncUpdateEventsBatch:
    """Tests for update_events_batch method."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = DutyCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_events(self):
        """Create sample events with event_ids for updating."""
        events = [
            AllDayDutyEvent(
                event_date=date(2026, 2, 1),
                until_platoon="A",
                until_crew={},
                from_platoon="B",
                from_crew={},
            ),
            AllDayDutyEvent(
                event_date=date(2026, 2, 2),
                until_platoon="B",
                until_crew={},
                from_platoon="A",
                from_crew={},
            ),
        ]
        events[0].event_id = "id-1"
        events[1].event_id = "id-2"
        return events

    @pytest.mark.asyncio
    async def test_update_events_batch_success(self, calendar_sync, sample_events):
        """Batch update returns success count."""
        calendar_sync.client.users.by_user_id.return_value.events.by_event_id.return_value.patch = (
            AsyncMock()
        )

        count, errors = await calendar_sync.update_events_batch(sample_events)

        assert count == 2
        assert errors == []

    @pytest.mark.asyncio
    async def test_update_events_batch_empty(self, calendar_sync):
        """Batch update handles empty list."""
        count, errors = await calendar_sync.update_events_batch([])

        assert count == 0
        assert errors == []

    @pytest.mark.asyncio
    async def test_update_events_batch_with_failures(self, calendar_sync, sample_events):
        """Batch update reports errors on failures."""
        # Make first call succeed, second fail
        call_count = [0]

        async def mock_patch(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("API Error")

        calendar_sync.client.users.by_user_id.return_value.events.by_event_id.return_value.patch = (
            mock_patch
        )

        count, errors = await calendar_sync.update_events_batch(sample_events)

        assert count == 1
        assert len(errors) == 1
        assert "Failed to update" in errors[0]


class TestDutyCalendarSyncSyncWrapper:
    """Tests for the synchronous sync() wrapper method."""

    @pytest.fixture
    def calendar_sync(self, mock_env_vars):
        """Create DutyCalendarSync with mocked methods."""
        with (
            patch("sjifire.calendar.duty_sync.ClientSecretCredential"),
            patch("sjifire.calendar.duty_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = DutyCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_schedules(self):
        """Create sample DaySchedule objects."""
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
                    )
                ],
            ),
        ]

    def test_sync_empty_schedules(self, calendar_sync):
        """Sync with empty schedules returns empty result."""
        result = calendar_sync.sync([], dry_run=False)

        assert result.events_created == 0
        assert result.events_updated == 0

    def test_sync_calls_sync_events(self, calendar_sync, sample_schedules):
        """Sync converts schedules and calls sync_events."""
        with (
            patch.object(calendar_sync, "_load_user_contacts", new=AsyncMock(return_value={})),
            patch.object(
                calendar_sync,
                "sync_events",
                new=AsyncMock(return_value=MagicMock(events_created=1, events_updated=0)),
            ) as mock_sync_events,
        ):
            result = calendar_sync.sync(sample_schedules, dry_run=False)

        mock_sync_events.assert_called_once()
        assert result.events_created == 1

    def test_sync_dry_run_passed_through(self, calendar_sync, sample_schedules):
        """Dry run flag is passed to sync_events."""
        with (
            patch.object(calendar_sync, "_load_user_contacts", new=AsyncMock(return_value={})),
            patch.object(
                calendar_sync,
                "sync_events",
                new=AsyncMock(return_value=MagicMock(events_created=0, events_updated=0)),
            ) as mock_sync_events,
        ):
            calendar_sync.sync(sample_schedules, dry_run=True)

        # Check dry_run was passed (4th positional arg)
        call_args = mock_sync_events.call_args
        assert call_args[0][3] is True
