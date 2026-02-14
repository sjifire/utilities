"""MCP tools for schedule lookup with Cosmos DB caching.

Schedule data flows: Aladtec → Cosmos DB cache → MCP tool.
The cache auto-refreshes from Aladtec when stale (>24 hours old)
or missing for the requested dates. The cache covers the target
date +/- 1 day to capture crew around shift changes.

Shift-change logic: fire department shifts typically run 24 hours
(e.g. 18:00 to 18:00). Before the shift change hour, the previous
day's crew is still on duty. The shift change hour is detected from
the data (full-shift entries where start_time == end_time).
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from sjifire.core.config import get_timezone
from sjifire.core.schedule import detect_shift_change_hour, resolve_duty_date, should_exclude_section
from sjifire.mcp.auth import get_current_user
from sjifire.mcp.schedule.models import DayScheduleCache, ScheduleEntryCache
from sjifire.mcp.schedule.store import ScheduleStore

logger = logging.getLogger(__name__)

# Maximum cache age before triggering an Aladtec refresh
CACHE_MAX_AGE_HOURS = 24.0


def _detect_shift_change_hour_from_cache(cached: dict[str, DayScheduleCache]) -> int | None:
    """Detect shift change hour from cached schedule days.

    Flattens all entries across cached days and delegates to the
    shared ``detect_shift_change_hour`` utility.
    """
    all_entries = [e for day in cached.values() for e in day.entries]
    return detect_shift_change_hour(all_entries)


def _build_crew_list(
    day: DayScheduleCache,
    include_admin: bool,
) -> list[dict]:
    """Build the crew list from a day's schedule entries."""
    entries = day.entries
    if not include_admin:
        entries = [e for e in entries if not should_exclude_section(e.section)]
    return [
        {
            "name": e.name,
            "position": e.position,
            "section": e.section,
            "start_time": e.start_time,
            "end_time": e.end_time,
        }
        for e in entries
    ]


def _fetch_from_aladtec(start: date, end: date) -> list[DayScheduleCache]:
    """Fetch schedule from Aladtec and convert to cache models (blocking).

    Returns list of DayScheduleCache ready for Cosmos DB.
    """
    from sjifire.aladtec.schedule_scraper import AladtecScheduleScraper

    with AladtecScheduleScraper() as scraper:
        if not scraper.login():
            logger.error("Failed to log in to Aladtec for schedule refresh")
            return []
        schedules = scraper.get_schedule_range(start, end)

    results = []
    for day in schedules:
        date_str = day.date.isoformat()
        entries = [
            ScheduleEntryCache(
                name=e.name,
                position=e.position,
                section=e.section,
                start_time=e.start_time,
                end_time=e.end_time,
                platoon=e.platoon,
            )
            for e in day.get_filled_positions()
        ]
        results.append(
            DayScheduleCache(
                id=date_str,
                date=date_str,
                platoon=day.platoon,
                entries=entries,
            )
        )

    logger.info("Fetched %d days from Aladtec (%s to %s)", len(results), start, end)
    return results


async def _ensure_cache(
    store: ScheduleStore,
    needed_dates: list[str],
) -> dict[str, DayScheduleCache]:
    """Ensure cache has fresh data for the needed dates.

    Checks Cosmos DB for each date. If any are missing or stale,
    fetches from Aladtec and updates the cache.

    Args:
        store: Connected ScheduleStore
        needed_dates: List of YYYY-MM-DD strings to ensure

    Returns:
        Dict mapping date string to DayScheduleCache for all needed dates
    """
    cached = await store.get_range(needed_dates)

    # Find dates that are missing or stale
    stale_dates = []
    for date_str in needed_dates:
        day = cached.get(date_str)
        if day is None or day.is_stale(CACHE_MAX_AGE_HOURS):
            stale_dates.append(date_str)

    if not stale_dates:
        logger.info("Schedule cache hit for all %d dates", len(needed_dates))
        return cached

    # Refresh stale dates from Aladtec
    logger.info(
        "Refreshing %d stale/missing schedule dates from Aladtec",
        len(stale_dates),
    )
    start = datetime.strptime(min(stale_dates), "%Y-%m-%d").date()
    end = datetime.strptime(max(stale_dates), "%Y-%m-%d").date()

    fresh = await asyncio.to_thread(_fetch_from_aladtec, start, end)

    # Write fresh data to cache
    for day_cache in fresh:
        await store.upsert(day_cache)
        cached[day_cache.date] = day_cache

    return cached


async def get_on_duty_crew(
    target_date: str | None = None,
    include_admin: bool = False,
    target_hour: int | None = None,
) -> dict:
    """Get the crew on duty for a specific date or right now.

    When ``target_date`` is omitted the function is **time-aware**:
    it detects the shift-change hour from the data (full-shift entries
    where ``start_time == end_time``) and picks the correct day's crew
    based on the current local time.  Before the shift change, the
    previous day's crew is still on duty.  The response also includes
    an ``upcoming`` block with the next shift's crew.

    When ``target_date`` is provided with ``target_hour``, the function
    applies the same shift-change logic using the given hour instead of
    the current time. This is useful for historical lookups (e.g., an
    incident at 16:48 before an 18:00 shift change).

    When ``target_date`` is provided without ``target_hour``, the
    function returns that date's assigned crew (no time-based filtering).

    By default, administration staff and Time Off entries are excluded
    — pass ``include_admin=True`` to see everyone.

    Data is cached in Cosmos DB and auto-refreshes from Aladtec
    if the cache is more than 24 hours old.

    Args:
        target_date: Date in YYYY-MM-DD format. Defaults to today (time-aware).
        include_admin: Include administration staff (default: False).
        target_hour: Hour of day (0-23) for shift-change-aware historical lookups.

    Returns:
        Dict with date, platoon, crew list, shift_change_hour, and
        (when target_date is omitted) an upcoming block.
    """
    user = get_current_user()

    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else date.today()
    except ValueError:
        return {"error": f"Invalid date format: {target_date!r}. Expected YYYY-MM-DD."}
    logger.info("Schedule lookup for %s hour=%s (user: %s)", dt.isoformat(), target_hour, user.email)

    # Request target date +/- 1 day for shift-change coverage
    needed = [
        (dt - timedelta(days=1)).isoformat(),
        dt.isoformat(),
        (dt + timedelta(days=1)).isoformat(),
    ]

    async with ScheduleStore() as store:
        cached = await _ensure_cache(store, needed)

    # Detect shift change hour from full-shift entries in the cached data
    shift_change_hour = _detect_shift_change_hour_from_cache(cached)

    # Determine effective hour: current local time for "now", or explicit target_hour
    effective_hour: int | None = target_hour
    if target_date is None and shift_change_hour is not None:
        tz = get_timezone()
        effective_hour = datetime.now(tz).hour

    duty_date, upcoming_date = resolve_duty_date(dt, shift_change_hour, effective_hour)

    duty_str = duty_date.isoformat()
    day = cached.get(duty_str)

    if day is None:
        return {
            "date": duty_str,
            "crew": [],
            "count": 0,
            "shift_change_hour": shift_change_hour,
            "note": "No schedule data available for this date.",
        }

    crew = _build_crew_list(day, include_admin)

    result: dict = {
        "date": duty_str,
        "platoon": day.platoon,
        "crew": crew,
        "count": len(crew),
        "shift_change_hour": shift_change_hour,
        "cache_age_hours": round(
            (datetime.now(day.fetched_at.tzinfo) - day.fetched_at).total_seconds() / 3600,
            1,
        ),
    }

    # Include upcoming shift when showing current crew (no target_date)
    if upcoming_date is not None:
        upcoming_str = upcoming_date.isoformat()
        upcoming_day = cached.get(upcoming_str)
        if upcoming_day:
            result["upcoming"] = {
                "date": upcoming_str,
                "platoon": upcoming_day.platoon,
                "crew": _build_crew_list(upcoming_day, include_admin),
            }

    return result
