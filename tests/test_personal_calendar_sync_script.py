"""Tests for sjifire.scripts.personal_calendar_sync module."""

import json
import sys
import tempfile
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.aladtec.schedule_scraper import DaySchedule, ScheduleEntry
from sjifire.calendar.personal_sync import PersonalSyncResult
from sjifire.scripts.personal_calendar_sync import (
    get_month_date_range,
    main,
    match_schedule_name_to_email,
    normalize_name,
    parse_month,
)

# =============================================================================
# Helper Function Tests
# =============================================================================


class TestNormalizeName:
    """Tests for normalize_name function."""

    def test_lowercases_name(self):
        """Converts name to lowercase."""
        assert normalize_name("John Smith") == "john smith"

    def test_strips_extra_spaces(self):
        """Removes extra whitespace."""
        assert normalize_name("  John   Smith  ") == "john smith"


class TestMatchScheduleNameToEmail:
    """Tests for match_schedule_name_to_email function."""

    def test_matches_last_first_format(self):
        """Matches 'Last, First' schedule names."""
        members = {"Adam Greene": "agreene@sjifire.org"}
        assert match_schedule_name_to_email("Greene, Adam", members) == "agreene@sjifire.org"

    def test_matches_first_last_format(self):
        """Matches 'First Last' schedule names."""
        members = {"John Smith": "jsmith@sjifire.org"}
        assert match_schedule_name_to_email("John Smith", members) == "jsmith@sjifire.org"

    def test_case_insensitive_match(self):
        """Matching is case insensitive."""
        members = {"John Smith": "jsmith@sjifire.org"}
        assert match_schedule_name_to_email("SMITH, JOHN", members) == "jsmith@sjifire.org"

    def test_partial_match_with_middle_name(self):
        """Matches when schedule has middle name."""
        members = {"John Smith": "jsmith@sjifire.org"}
        assert match_schedule_name_to_email("Smith, John Robert", members) == "jsmith@sjifire.org"

    def test_returns_none_for_no_match(self):
        """Returns None when no match found."""
        members = {"John Smith": "jsmith@sjifire.org"}
        assert match_schedule_name_to_email("Jane Doe", members) is None


class TestParseMonth:
    """Tests for parse_month function."""

    def test_parses_month_year(self):
        """Parses 'Feb 2026' format."""
        year, month = parse_month("Feb 2026")
        assert year == 2026
        assert month == 2

    def test_parses_iso_format(self):
        """Parses '2026-02' format."""
        year, month = parse_month("2026-02")
        assert year == 2026
        assert month == 2

    def test_invalid_format_raises(self):
        """Invalid format raises ValueError."""
        with pytest.raises(ValueError):
            parse_month("invalid")


class TestGetMonthDateRange:
    """Tests for get_month_date_range function."""

    def test_february_range(self):
        """Gets February date range."""
        start, end = get_month_date_range(2026, 2)
        assert start == date(2026, 2, 1)
        assert end == date(2026, 2, 28)

    def test_leap_year_february(self):
        """Gets February date range for leap year."""
        start, end = get_month_date_range(2024, 2)
        assert start == date(2024, 2, 1)
        assert end == date(2024, 2, 29)


# =============================================================================
# CLI Argument Tests
# =============================================================================


class TestCLIArguments:
    """Tests for CLI argument parsing."""

    def test_requires_user_or_all(self, capsys):
        """CLI requires --user or --all."""
        with (
            patch.object(sys, "argv", ["personal-calendar-sync", "--month", "Feb 2026"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 2

    def test_cannot_combine_user_and_all(self, capsys):
        """CLI rejects --user and --all together."""
        with (
            patch.object(
                sys,
                "argv",
                [
                    "personal-calendar-sync",
                    "--user",
                    "test@example.com",
                    "--all",
                    "--month",
                    "Feb 2026",
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 2

    def test_requires_month_or_months(self, capsys):
        """CLI requires --month or --months."""
        with (
            patch.object(sys, "argv", ["personal-calendar-sync", "--all"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 2

    def test_cannot_combine_month_and_months(self, capsys):
        """CLI rejects --month and --months together."""
        with (
            patch.object(
                sys,
                "argv",
                ["personal-calendar-sync", "--all", "--month", "Feb 2026", "--months", "4"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 2


# =============================================================================
# Main Function Tests
# =============================================================================


@pytest.fixture
def mock_member_scraper():
    """Mock AladtecMemberScraper."""
    with patch("sjifire.scripts.personal_calendar_sync.AladtecMemberScraper") as mock:
        instance = MagicMock()
        instance.__enter__ = MagicMock(return_value=instance)
        instance.__exit__ = MagicMock(return_value=False)
        instance.login.return_value = True
        # Return members with emails
        member1 = MagicMock()
        member1.first_name = "Adam"
        member1.last_name = "Greene"
        member1.email = "agreene@sjifire.org"
        member2 = MagicMock()
        member2.first_name = "John"
        member2.last_name = "Smith"
        member2.email = "jsmith@sjifire.org"
        instance.get_members.return_value = [member1, member2]
        mock.return_value = instance
        yield instance


@pytest.fixture
def mock_schedule_scraper():
    """Mock AladtecScheduleScraper."""
    with patch("sjifire.scripts.personal_calendar_sync.AladtecScheduleScraper") as mock:
        instance = MagicMock()
        instance.__enter__ = MagicMock(return_value=instance)
        instance.__exit__ = MagicMock(return_value=False)
        instance.login.return_value = True
        # Return schedule entries
        entry1 = ScheduleEntry(
            date=date(2026, 2, 18),
            section="S31",
            position="Captain",
            name="Greene, Adam",
            start_time="18:00",
            end_time="18:00",
        )
        entry2 = ScheduleEntry(
            date=date(2026, 2, 18),
            section="S31",
            position="Firefighter",
            name="Smith, John",
            start_time="18:00",
            end_time="18:00",
        )
        day_schedule = DaySchedule(date=date(2026, 2, 18), platoon="A", entries=[entry1, entry2])
        instance.get_schedule_range.return_value = [day_schedule]
        mock.return_value = instance
        yield instance


@pytest.fixture
def mock_personal_sync():
    """Mock PersonalCalendarSync."""
    with patch("sjifire.scripts.personal_calendar_sync.PersonalCalendarSync") as mock:
        instance = MagicMock()
        instance.sync_user = AsyncMock(
            return_value=PersonalSyncResult(user="test@example.com", events_created=1)
        )
        instance.get_or_create_calendar = AsyncMock(return_value="calendar-id")
        instance.get_existing_events = AsyncMock(return_value={})
        mock.return_value = instance
        yield instance


class TestMainWithAllFlag:
    """Tests for main() with --all flag."""

    def test_all_syncs_all_users_with_entries(
        self, mock_env_vars, mock_member_scraper, mock_schedule_scraper, mock_personal_sync
    ):
        """--all syncs all users who have schedule entries."""
        with patch.object(sys, "argv", ["personal-calendar-sync", "--all", "--month", "Feb 2026"]):
            result = main()

        assert result == 0
        # Should call sync_user for both users with entries
        assert mock_personal_sync.sync_user.call_count == 2

    def test_all_respects_dry_run(
        self, mock_env_vars, mock_member_scraper, mock_schedule_scraper, mock_personal_sync
    ):
        """--all with --dry-run passes dry_run=True to sync_user."""
        with patch.object(
            sys, "argv", ["personal-calendar-sync", "--all", "--month", "Feb 2026", "--dry-run"]
        ):
            main()

        # Check dry_run argument was True
        call_args = mock_personal_sync.sync_user.call_args_list[0]
        assert call_args[0][4] is True  # dry_run is 5th positional arg

    def test_all_with_months_calculates_range(
        self, mock_env_vars, mock_member_scraper, mock_schedule_scraper, mock_personal_sync
    ):
        """--all with --months calculates correct date range."""
        with (
            patch.object(sys, "argv", ["personal-calendar-sync", "--all", "--months", "2"]),
            patch("sjifire.scripts.personal_calendar_sync.date") as mock_date,
        ):
            mock_date.today.return_value = date(2026, 2, 15)
            mock_date.side_effect = date
            main()

        # Verify get_schedule_range was called with correct range
        call_args = mock_schedule_scraper.get_schedule_range.call_args
        assert call_args[0][0] == date(2026, 2, 1)  # start of current month


class TestMainWithLoadSchedule:
    """Tests for main() with --load-schedule flag."""

    def test_load_schedule_skips_aladtec_fetch(
        self, mock_env_vars, mock_member_scraper, mock_personal_sync
    ):
        """--load-schedule reads from file instead of fetching from Aladtec."""
        # Create a temp schedule file
        schedule_data = [
            {
                "date": "2026-02-18",
                "platoon": "A",
                "entries": [
                    {
                        "date": "2026-02-18",
                        "section": "S31",
                        "position": "Captain",
                        "name": "Greene, Adam",
                        "start_time": "18:00",
                        "end_time": "18:00",
                        "platoon": "A",
                    }
                ],
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(schedule_data, f)
            temp_path = f.name

        try:
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "personal-calendar-sync",
                        "--all",
                        "--month",
                        "Feb 2026",
                        "--load-schedule",
                        temp_path,
                    ],
                ),
                patch(
                    "sjifire.scripts.personal_calendar_sync.AladtecScheduleScraper"
                ) as mock_scraper,
            ):
                result = main()

            assert result == 0
            # AladtecScheduleScraper should NOT be instantiated
            mock_scraper.assert_not_called()
        finally:
            from pathlib import Path

            Path(temp_path).unlink()

    def test_load_schedule_file_not_found(self, mock_env_vars, mock_member_scraper):
        """--load-schedule returns error if file not found."""
        with patch.object(
            sys,
            "argv",
            [
                "personal-calendar-sync",
                "--all",
                "--month",
                "Feb 2026",
                "--load-schedule",
                "/nonexistent/file.json",
            ],
        ):
            result = main()

        assert result == 1


class TestMainWithUserFlag:
    """Tests for main() with --user flag."""

    def test_user_syncs_single_user(
        self, mock_env_vars, mock_member_scraper, mock_schedule_scraper, mock_personal_sync
    ):
        """--user syncs only specified user."""
        with patch.object(
            sys,
            "argv",
            ["personal-calendar-sync", "--user", "agreene@sjifire.org", "--month", "Feb 2026"],
        ):
            result = main()

        assert result == 0
        # Should only call sync_user once for the specified user
        assert mock_personal_sync.sync_user.call_count == 1
        call_args = mock_personal_sync.sync_user.call_args
        assert call_args[0][0] == "agreene@sjifire.org"

    def test_user_not_in_schedule_warns(
        self, mock_env_vars, mock_member_scraper, mock_schedule_scraper, mock_personal_sync
    ):
        """--user with user not in schedule shows warning."""
        with patch.object(
            sys,
            "argv",
            ["personal-calendar-sync", "--user", "nobody@sjifire.org", "--month", "Feb 2026"],
        ):
            result = main()

        # Should still return 0 (not an error, just no entries)
        assert result == 0
        # Should not call sync_user
        mock_personal_sync.sync_user.assert_not_called()


class TestMainWithForceFlag:
    """Tests for main() with --force flag."""

    def test_force_passes_to_sync_user(
        self, mock_env_vars, mock_member_scraper, mock_schedule_scraper, mock_personal_sync
    ):
        """--force passes force=True to sync_user."""
        with patch.object(
            sys,
            "argv",
            [
                "personal-calendar-sync",
                "--user",
                "agreene@sjifire.org",
                "--month",
                "Feb 2026",
                "--force",
            ],
        ):
            main()

        # Check force argument was True
        call_args = mock_personal_sync.sync_user.call_args
        assert call_args[0][5] is True  # force is 6th positional arg


# =============================================================================
# Schedule Serialization Tests
# =============================================================================


class TestScheduleSerialization:
    """Tests for save_schedules and load_schedules functions."""

    def test_save_and_load_round_trip(self):
        """Saved schedules can be loaded correctly."""
        from sjifire.aladtec.schedule_scraper import load_schedules, save_schedules

        original = [
            DaySchedule(
                date=date(2026, 2, 18),
                platoon="A",
                entries=[
                    ScheduleEntry(
                        date=date(2026, 2, 18),
                        section="S31",
                        position="Captain",
                        name="Greene, Adam",
                        start_time="18:00",
                        end_time="18:00",
                        platoon="A",
                    ),
                    ScheduleEntry(
                        date=date(2026, 2, 18),
                        section="S31",
                        position="Firefighter",
                        name="Smith, John",
                        start_time="19:00",
                        end_time="20:00",
                        platoon="A",
                    ),
                ],
            )
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            save_schedules(original, temp_path)
            loaded = load_schedules(temp_path)

            assert len(loaded) == 1
            assert loaded[0].date == date(2026, 2, 18)
            assert loaded[0].platoon == "A"
            assert len(loaded[0].entries) == 2
            assert loaded[0].entries[0].name == "Greene, Adam"
            assert loaded[0].entries[0].start_time == "18:00"
            assert loaded[0].entries[1].name == "Smith, John"
            assert loaded[0].entries[1].start_time == "19:00"
        finally:
            from pathlib import Path

            Path(temp_path).unlink()

    def test_load_nonexistent_file_raises(self):
        """Loading nonexistent file raises FileNotFoundError."""
        from sjifire.aladtec.schedule_scraper import load_schedules

        with pytest.raises(FileNotFoundError):
            load_schedules("/nonexistent/path.json")
