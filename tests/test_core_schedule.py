"""Tests for core schedule utilities (shift-change detection & duty date resolution)."""

from dataclasses import dataclass
from datetime import date

import pytest

from sjifire.core.schedule import (
    clean_position,
    detect_shift_change_hour,
    is_filled_entry,
    resolve_duty_date,
    should_exclude_section,
)


@dataclass
class FakeEntry:
    """Minimal object satisfying the HasShiftTimes protocol."""

    start_time: str
    end_time: str


# ── detect_shift_change_hour ────────────────────────────────────────


class TestDetectShiftChangeHour:
    def test_typical_1800_shift(self):
        """Full-shift entries with 18:00/18:00 → shift change at 18."""
        entries = [
            FakeEntry("18:00", "18:00"),
            FakeEntry("18:00", "18:00"),
            FakeEntry("19:00", "07:00"),  # partial shift, ignored
        ]
        assert detect_shift_change_hour(entries) == 18

    def test_0800_shift(self):
        entries = [
            FakeEntry("08:00", "08:00"),
            FakeEntry("08:00", "08:00"),
        ]
        assert detect_shift_change_hour(entries) == 8

    def test_most_common_wins(self):
        """When mixed, the most frequent full-shift hour wins."""
        entries = [
            FakeEntry("18:00", "18:00"),
            FakeEntry("18:00", "18:00"),
            FakeEntry("18:00", "18:00"),
            FakeEntry("06:00", "06:00"),  # minority
        ]
        assert detect_shift_change_hour(entries) == 18

    def test_no_full_shift_entries_returns_none(self):
        entries = [
            FakeEntry("08:00", "17:00"),
            FakeEntry("19:00", "07:00"),
        ]
        assert detect_shift_change_hour(entries) is None

    def test_empty_list_returns_none(self):
        assert detect_shift_change_hour([]) is None

    def test_malformed_time_skipped(self):
        entries = [
            FakeEntry("bad", "bad"),
            FakeEntry("18:00", "18:00"),
        ]
        assert detect_shift_change_hour(entries) == 18

    def test_empty_times_skipped(self):
        entries = [
            FakeEntry("", ""),
            FakeEntry("18:00", "18:00"),
        ]
        assert detect_shift_change_hour(entries) == 18


# ── resolve_duty_date ───────────────────────────────────────────────


class TestResolveDutyDate:
    """Tests for the shift-change-aware duty date resolution."""

    def test_before_shift_change_returns_previous_day(self):
        """Incident at 16:48 with 18:00 shift change → previous day's crew."""
        target = date(2026, 2, 13)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=18, hour=16)
        assert duty == date(2026, 2, 12)
        assert upcoming == date(2026, 2, 13)

    def test_after_shift_change_returns_same_day(self):
        """Incident at 19:00 with 18:00 shift change → same day's crew."""
        target = date(2026, 2, 13)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=18, hour=19)
        assert duty == date(2026, 2, 13)
        assert upcoming == date(2026, 2, 14)

    def test_at_exact_shift_change_returns_same_day(self):
        """Incident at exactly 18:00 → new crew has taken over."""
        target = date(2026, 2, 13)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=18, hour=18)
        assert duty == date(2026, 2, 13)
        assert upcoming == date(2026, 2, 14)

    def test_midnight_before_shift_change(self):
        """Incident at 00:00 with 18:00 shift change → previous day's crew."""
        target = date(2026, 2, 13)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=18, hour=0)
        assert duty == date(2026, 2, 12)
        assert upcoming == date(2026, 2, 13)

    def test_hour_17_just_before_change(self):
        """Incident at 17:xx with 18:00 shift change → still previous crew."""
        target = date(2026, 2, 13)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=18, hour=17)
        assert duty == date(2026, 2, 12)
        assert upcoming == date(2026, 2, 13)

    def test_no_hour_returns_target_date(self):
        """No time context → return target date unchanged."""
        target = date(2026, 2, 13)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=18, hour=None)
        assert duty == date(2026, 2, 13)
        assert upcoming is None

    def test_no_shift_change_hour_returns_target_date(self):
        """Unknown shift change hour → return target date unchanged."""
        target = date(2026, 2, 13)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=None, hour=16)
        assert duty == date(2026, 2, 13)
        assert upcoming is None

    def test_both_none_returns_target_date(self):
        """No shift info at all → return target date unchanged."""
        target = date(2026, 2, 13)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=None, hour=None)
        assert duty == date(2026, 2, 13)
        assert upcoming is None

    def test_0800_shift_change(self):
        """Different shift change hour (08:00)."""
        target = date(2026, 2, 13)

        # 07:00 → before 08:00 change, previous day's crew
        duty, upcoming = resolve_duty_date(target, shift_change_hour=8, hour=7)
        assert duty == date(2026, 2, 12)
        assert upcoming == date(2026, 2, 13)

        # 09:00 → after 08:00 change, current day's crew
        duty, upcoming = resolve_duty_date(target, shift_change_hour=8, hour=9)
        assert duty == date(2026, 2, 13)
        assert upcoming == date(2026, 2, 14)

    def test_cross_month_boundary(self):
        """Shift change logic works across month boundaries."""
        target = date(2026, 3, 1)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=18, hour=10)
        assert duty == date(2026, 2, 28)
        assert upcoming == date(2026, 3, 1)

    def test_cross_year_boundary(self):
        """Shift change logic works across year boundaries."""
        target = date(2027, 1, 1)
        duty, upcoming = resolve_duty_date(target, shift_change_hour=18, hour=10)
        assert duty == date(2026, 12, 31)
        assert upcoming == date(2027, 1, 1)


# ── is_filled_entry ─────────────────────────────────────────────────


class TestIsFilledEntry:
    def test_real_person(self):
        assert is_filled_entry("John Doe") is True

    def test_empty_name(self):
        assert is_filled_entry("") is False

    def test_placeholder_pattern(self):
        assert is_filled_entry("S31 / Firefighter") is False

    def test_slash_in_real_name(self):
        """A real name wouldn't have ' / ' with spaces."""
        assert is_filled_entry("John/Doe") is True


# ── clean_position ──────────────────────────────────────────────────


class TestCleanPosition:
    def test_removes_colon(self):
        assert clean_position("Firefighter:") == "Firefighter"

    def test_strips_whitespace(self):
        assert clean_position("  Captain  ") == "Captain"

    def test_no_change_needed(self):
        assert clean_position("EMT") == "EMT"
