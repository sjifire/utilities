"""Shared schedule utilities used by both calendar sync and MCP tools.

Consolidates shift-change detection and section-filtering logic so
that calendar sync, MCP schedule tools, and the dashboard all use
the same rules.
"""

import logging
from collections import Counter
from typing import Protocol

from sjifire.core.config import get_org_config

logger = logging.getLogger(__name__)


class HasShiftTimes(Protocol):
    """Any object with start_time and end_time string fields."""

    start_time: str
    end_time: str


def should_exclude_section(section: str) -> bool:
    """Check if a schedule section should be hidden from display.

    Uses a denylist loaded from ``schedule_excluded_sections`` in
    ``organization.json``.  All sections not in that list are shown.

    This is the single source of truth for section filtering, used
    by the calendar sync, MCP schedule tool, and the dashboard.
    """
    return section.lower() in get_org_config().schedule_excluded_sections


def is_filled_entry(name: str) -> bool:
    """Check if a schedule entry name represents a real person.

    Unfilled positions either have empty names or use the
    ``"Section / Position"`` placeholder pattern (e.g. ``"S31 / Firefighter"``).

    This is the single source of truth for filled-position detection,
    used by the schedule scraper, calendar sync, and MCP tools.
    """
    if not name:
        return False
    return " / " not in name


def clean_position(position: str) -> str:
    """Clean up position title — remove colons and strip whitespace."""
    return position.replace(":", "").strip()


def position_sort_key(position: str) -> int:
    """Sort key for positions within a section (lower = more senior).

    Uses ``position_order`` from ``organization.json``.  The list index
    determines sort priority.  Positions not in the list sort last.
    """
    cleaned = clean_position(position)
    for i, label in enumerate(get_org_config().position_order):
        if label in cleaned:
            return i
    return 99


def section_sort_key(section: str) -> tuple[int, str]:
    """Sort key for sections using config-driven priority order.

    Reads ``schedule_section_order`` from organization.json — a list of
    keywords checked case-insensitively against the section name.  The
    first matching keyword determines priority (position in the list).
    Unmatched sections sort alphabetically at the end.
    """
    section_lower = section.lower()
    for i, keyword in enumerate(get_org_config().schedule_section_order):
        if keyword.lower() in section_lower:
            return (i, section)

    # Unmatched sections sort after all configured ones
    return (len(get_org_config().schedule_section_order), section)


def detect_shift_change_hour(entries: list[HasShiftTimes]) -> int | None:
    """Detect the shift change hour from schedule entries.

    Full-shift entries (where ``start_time == end_time``) encode the
    shift boundary hour (e.g. ``"18:00"``/``"18:00"`` means shifts
    change at 18:00).  Returns the most common such hour, or ``None``
    if no full-shift entries are found.

    Works with both ``ScheduleEntry`` (Aladtec scraper) and
    ``ScheduleEntryCache`` (Cosmos DB cache) objects.

    Args:
        entries: Flat list of schedule entry objects with
            ``start_time`` and ``end_time`` string attributes.

    Returns:
        Hour (0-23) when shifts typically change, or None.
    """
    hour_counts: Counter[int] = Counter()

    for entry in entries:
        if entry.start_time and entry.start_time == entry.end_time:
            try:
                hour = int(entry.start_time.split(":")[0])
                hour_counts[hour] += 1
            except (ValueError, IndexError):
                continue

    if not hour_counts:
        return None

    most_common, count = hour_counts.most_common(1)[0]
    logger.info("Detected shift change hour: %02d:00 (%d entries)", most_common, count)
    return most_common
