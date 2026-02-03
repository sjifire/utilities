"""Tests for sjifire.calendar.sync module."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.aladtec.schedule import DaySchedule, ScheduleEntry
from sjifire.calendar.models import CrewMember
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
        with patch("sjifire.calendar.sync.ClientSecretCredential"):
            with patch("sjifire.calendar.sync.GraphServiceClient"):
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
        with patch("sjifire.calendar.sync.ClientSecretCredential"):
            with patch("sjifire.calendar.sync.GraphServiceClient"):
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
        with patch("sjifire.calendar.sync.ClientSecretCredential"):
            with patch("sjifire.calendar.sync.GraphServiceClient"):
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
