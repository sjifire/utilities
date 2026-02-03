"""Tests for sjifire.scripts.calendar_sync module."""

from datetime import date

import pytest

from sjifire.scripts.calendar_sync import get_month_date_range, parse_month


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
            year, month = parse_month(month_str)
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
