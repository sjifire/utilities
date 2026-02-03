"""Tests for sjifire.scripts.calendar_sync module."""

import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from sjifire.calendar.models import SyncResult
from sjifire.scripts.calendar_sync import get_month_date_range, main, parse_month


class TestParseMonth:
    """Tests for parse_month function."""

    def test_month_year_format(self):
        """Parse 'Jan 2026' format."""
        year, month = parse_month("Jan 2026")
        assert year == 2026
        assert month == 1

    def test_full_month_year_format(self):
        """Parse 'January 2026' format."""
        year, month = parse_month("January 2026")
        assert year == 2026
        assert month == 1

    def test_iso_format(self):
        """Parse '2026-01' format."""
        year, month = parse_month("2026-01")
        assert year == 2026
        assert month == 1

    def test_iso_format_single_digit(self):
        """Parse '2026-1' format."""
        year, month = parse_month("2026-1")
        assert year == 2026
        assert month == 1

    def test_slash_format(self):
        """Parse '01/2026' format."""
        year, month = parse_month("01/2026")
        assert year == 2026
        assert month == 1

    def test_slash_format_single_digit(self):
        """Parse '1/2026' format."""
        year, month = parse_month("1/2026")
        assert year == 2026
        assert month == 1

    def test_case_insensitive(self):
        """Parse is case insensitive."""
        year, month = parse_month("JAN 2026")
        assert year == 2026
        assert month == 1

        year, month = parse_month("jan 2026")
        assert year == 2026
        assert month == 1

    def test_various_months(self):
        """Parse various month names."""
        test_cases = [
            ("Feb 2026", 2),
            ("Mar 2026", 3),
            ("Apr 2026", 4),
            ("May 2026", 5),
            ("Jun 2026", 6),
            ("Jul 2026", 7),
            ("Aug 2026", 8),
            ("Sep 2026", 9),
            ("Sept 2026", 9),
            ("Oct 2026", 10),
            ("Nov 2026", 11),
            ("Dec 2026", 12),
        ]
        for month_str, expected_month in test_cases:
            _, month = parse_month(month_str)
            assert month == expected_month

    def test_strips_whitespace(self):
        """Parse strips leading/trailing whitespace."""
        year, month = parse_month("  Jan 2026  ")
        assert year == 2026
        assert month == 1

    def test_invalid_format_raises(self):
        """Invalid format raises ValueError."""
        with pytest.raises(ValueError):
            parse_month("invalid")

    def test_invalid_month_name_raises(self):
        """Invalid month name raises ValueError."""
        with pytest.raises(ValueError):
            parse_month("Foo 2026")


class TestGetMonthDateRange:
    """Tests for get_month_date_range function."""

    def test_january(self):
        """Get January date range."""
        start, end = get_month_date_range(2026, 1)
        assert start == date(2026, 1, 1)
        assert end == date(2026, 1, 31)

    def test_february_regular(self):
        """Get February date range (non-leap year)."""
        start, end = get_month_date_range(2025, 2)
        assert start == date(2025, 2, 1)
        assert end == date(2025, 2, 28)

    def test_february_leap_year(self):
        """Get February date range (leap year)."""
        start, end = get_month_date_range(2024, 2)
        assert start == date(2024, 2, 1)
        assert end == date(2024, 2, 29)

    def test_april_30_days(self):
        """Get April date range (30 days)."""
        start, end = get_month_date_range(2026, 4)
        assert start == date(2026, 4, 1)
        assert end == date(2026, 4, 30)

    def test_december(self):
        """Get December date range."""
        start, end = get_month_date_range(2026, 12)
        assert start == date(2026, 12, 1)
        assert end == date(2026, 12, 31)


class TestMainCLI:
    """Tests for main CLI function."""

    @pytest.fixture
    def mock_calendar_sync(self):
        """Mock CalendarSync class."""
        with patch("sjifire.scripts.calendar_sync.CalendarSync") as mock:
            instance = MagicMock()
            mock.return_value = instance
            yield instance

    @pytest.fixture
    def mock_aladtec_scraper(self):
        """Mock AladtecScheduleScraper."""
        with patch("sjifire.scripts.calendar_sync.AladtecScheduleScraper") as mock:
            instance = MagicMock()
            instance.__enter__ = MagicMock(return_value=instance)
            instance.__exit__ = MagicMock(return_value=False)
            instance.login.return_value = True
            instance.get_schedule_range.return_value = []
            mock.return_value = instance
            yield instance

    def test_requires_month_or_months_or_delete(self, capsys):
        """CLI requires at least one of --month, --months, or --delete."""
        with (
            patch.object(sys, "argv", ["calendar-sync"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 2  # argparse error

    def test_cannot_combine_month_and_months(self, capsys):
        """CLI rejects --month and --months together."""
        with (
            patch.object(sys, "argv", ["calendar-sync", "--month", "Jan 2026", "--months", "4"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 2

    def test_cannot_combine_month_and_delete(self, capsys):
        """CLI rejects --month and --delete together."""
        with (
            patch.object(
                sys, "argv", ["calendar-sync", "--month", "Jan 2026", "--delete", "Feb 2026"]
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 2

    def test_delete_mode_calls_delete_date_range(self, mock_calendar_sync, mock_env_vars):
        """Delete mode calls delete_date_range."""
        mock_calendar_sync.delete_date_range.return_value = SyncResult(events_deleted=5)

        with patch.object(sys, "argv", ["calendar-sync", "--delete", "Jan 2026"]):
            result = main()

        assert result == 0
        mock_calendar_sync.delete_date_range.assert_called_once()
        # Check it was called with correct date range
        call_args = mock_calendar_sync.delete_date_range.call_args
        assert call_args[0][0] == date(2026, 1, 1)  # start_date
        assert call_args[0][1] == date(2026, 1, 31)  # end_date

    def test_delete_mode_dry_run(self, mock_calendar_sync, mock_env_vars):
        """Delete mode respects --dry-run flag."""
        mock_calendar_sync.delete_date_range.return_value = SyncResult(events_deleted=5)

        with patch.object(sys, "argv", ["calendar-sync", "--delete", "Jan 2026", "--dry-run"]):
            result = main()

        assert result == 0
        call_args = mock_calendar_sync.delete_date_range.call_args
        assert call_args[1]["dry_run"] is True

    def test_delete_mode_returns_error_on_failures(self, mock_calendar_sync, mock_env_vars):
        """Delete mode returns 1 if there are errors."""
        mock_calendar_sync.delete_date_range.return_value = SyncResult(
            events_deleted=3, errors=["Failed to delete event"]
        )

        with patch.object(sys, "argv", ["calendar-sync", "--delete", "Jan 2026"]):
            result = main()

        assert result == 1

    def test_delete_mode_invalid_month_returns_error(self, mock_calendar_sync, mock_env_vars):
        """Delete mode returns error for invalid month."""
        with patch.object(sys, "argv", ["calendar-sync", "--delete", "invalid"]):
            result = main()

        assert result == 1
        mock_calendar_sync.delete_date_range.assert_not_called()

    def test_month_mode_calls_sync(self, mock_calendar_sync, mock_aladtec_scraper, mock_env_vars):
        """Month mode fetches from Aladtec and syncs."""
        mock_calendar_sync.sync.return_value = SyncResult(events_created=10)

        with patch.object(sys, "argv", ["calendar-sync", "--month", "Jan 2026"]):
            result = main()

        assert result == 0
        mock_aladtec_scraper.login.assert_called_once()
        mock_aladtec_scraper.get_schedule_range.assert_called_once()
        mock_calendar_sync.sync.assert_called_once()

    def test_month_mode_aladtec_login_failure(
        self, mock_calendar_sync, mock_aladtec_scraper, mock_env_vars
    ):
        """Month mode returns error if Aladtec login fails."""
        mock_aladtec_scraper.login.return_value = False

        with patch.object(sys, "argv", ["calendar-sync", "--month", "Jan 2026"]):
            result = main()

        assert result == 1
        mock_calendar_sync.sync.assert_not_called()

    def test_months_mode_calculates_range(
        self, mock_calendar_sync, mock_aladtec_scraper, mock_env_vars
    ):
        """Months mode calculates correct date range."""
        mock_calendar_sync.sync.return_value = SyncResult(events_created=30)

        with (
            patch.object(sys, "argv", ["calendar-sync", "--months", "2"]),
            patch("sjifire.scripts.calendar_sync.date") as mock_date,
        ):
            mock_date.today.return_value = date(2026, 2, 15)
            mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
            result = main()

        assert result == 0
        # Verify get_schedule_range was called with correct range
        call_args = mock_aladtec_scraper.get_schedule_range.call_args
        assert call_args[0][0] == date(2026, 2, 1)  # start of current month

    def test_custom_mailbox(self, mock_calendar_sync, mock_env_vars):
        """Custom mailbox is passed to CalendarSync."""
        mock_calendar_sync.delete_date_range.return_value = SyncResult()

        with patch.object(
            sys, "argv", ["calendar-sync", "--delete", "Jan 2026", "--mailbox", "custom@test.com"]
        ):
            main()

        # Check CalendarSync was constructed with custom mailbox
        with patch("sjifire.scripts.calendar_sync.CalendarSync") as mock_class:
            mock_class.return_value = mock_calendar_sync
            mock_calendar_sync.delete_date_range.return_value = SyncResult()

            with patch.object(
                sys,
                "argv",
                ["calendar-sync", "--delete", "Jan 2026", "--mailbox", "custom@test.com"],
            ):
                main()

            mock_class.assert_called_with(mailbox="custom@test.com")
