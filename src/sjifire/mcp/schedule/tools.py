"""MCP tools for schedule lookup with Cosmos DB caching.

Schedule data flows: Aladtec → Cosmos DB cache → MCP tool.
The cache auto-refreshes from Aladtec when stale (>24 hours old)
or missing for the requested dates. The cache covers the target
date +/- 1 day to capture crew around shift changes.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from sjifire.mcp.auth import get_current_user
from sjifire.mcp.schedule.models import DayScheduleCache, ScheduleEntryCache
from sjifire.mcp.schedule.store import ScheduleStore

logger = logging.getLogger(__name__)

# Maximum cache age before triggering an Aladtec refresh
CACHE_MAX_AGE_HOURS = 24.0


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
        logger.debug("Schedule cache is fresh for all %d dates", len(needed_dates))
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


async def get_on_duty_crew(target_date: str | None = None) -> dict:
    """Get the crew that was on duty for a specific date.

    Returns who was scheduled on each section for the given date,
    plus the day before and after to capture shift-change context.

    Data is cached in Cosmos DB and auto-refreshes from Aladtec
    if the cache is more than 24 hours old.

    Args:
        target_date: Date in YYYY-MM-DD format. Defaults to today.

    Returns:
        Dict with "date" and "crew" list containing name, position,
        section, and shift times for each person on duty.
    """
    get_current_user()

    dt = datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else date.today()

    # Request target date +/- 1 day for shift-change coverage
    needed = [
        (dt - timedelta(days=1)).isoformat(),
        dt.isoformat(),
        (dt + timedelta(days=1)).isoformat(),
    ]

    async with ScheduleStore() as store:
        cached = await _ensure_cache(store, needed)

    # Return the target date's crew
    target_str = dt.isoformat()
    day = cached.get(target_str)

    if day is None:
        return {
            "date": target_str,
            "crew": [],
            "count": 0,
            "note": "No schedule data available for this date.",
        }

    crew = [
        {
            "name": e.name,
            "position": e.position,
            "section": e.section,
            "start_time": e.start_time,
            "end_time": e.end_time,
        }
        for e in day.entries
    ]

    return {
        "date": target_str,
        "platoon": day.platoon,
        "crew": crew,
        "count": len(crew),
        "cache_age_hours": round(
            (datetime.now(day.fetched_at.tzinfo) - day.fetched_at).total_seconds() / 3600,
            1,
        ),
    }
