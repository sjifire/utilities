"""Async Cosmos DB operations for cached schedule data.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.
"""

import logging
import os
from datetime import date, datetime, timedelta
from typing import ClassVar, Self

from dotenv import load_dotenv

from sjifire.core.config import get_cosmos_database
from sjifire.mcp.schedule.models import DayScheduleCache, ScheduleEntryCache

logger = logging.getLogger(__name__)
CONTAINER_NAME = "schedules"


class ScheduleStore:
    """Async read/write for cached schedule data in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with ScheduleStore() as store:
            day = await store.get("2026-02-12")
            await store.upsert(day_cache)
    """

    # Shared in-memory cache across instances (persists for server lifetime)
    _memory: ClassVar[dict[str, dict]] = {}

    def __init__(self) -> None:
        """Initialize store. Call ``__aenter__`` to connect."""
        self._client = None
        self._container = None
        self._credential = None
        self._in_memory = False

    async def __aenter__(self) -> Self:
        """Connect to Cosmos DB, or fall back to in-memory mode."""
        load_dotenv()

        endpoint = os.getenv("COSMOS_ENDPOINT")
        key = os.getenv("COSMOS_KEY")

        if key:
            from azure.cosmos.aio import CosmosClient

            self._client = CosmosClient(endpoint, credential=key)
        elif endpoint:
            from azure.cosmos.aio import CosmosClient
            from azure.identity.aio import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
            self._client = CosmosClient(endpoint, credential=self._credential)
        else:
            logger.warning("No COSMOS_ENDPOINT set — using in-memory schedule cache (dev only)")
            self._in_memory = True
            return self

        database = self._client.get_database_client(get_cosmos_database())
        self._container = database.get_container_client(CONTAINER_NAME)
        logger.info("Connected to Cosmos DB: %s/%s", get_cosmos_database(), CONTAINER_NAME)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close connections."""
        if self._client:
            await self._client.close()
            self._client = None
        if self._credential:
            await self._credential.close()
            self._credential = None
        self._container = None

    async def get(self, date_str: str) -> DayScheduleCache | None:
        """Get cached schedule for a date.

        Args:
            date_str: Date in YYYY-MM-DD format (used as both id and partition key)

        Returns:
            Cached schedule or None if not found
        """
        if self._in_memory:
            data = self._memory.get(date_str)
            return DayScheduleCache.from_cosmos(data) if data else None

        try:
            result = await self._container.read_item(
                item=date_str,
                partition_key=date_str,
            )
            return DayScheduleCache.from_cosmos(result)
        except Exception:
            return None

    async def upsert(self, doc: DayScheduleCache) -> None:
        """Write or update a cached schedule day.

        Args:
            doc: Schedule cache document to upsert
        """
        if self._in_memory:
            self._memory[doc.date] = doc.to_cosmos()
            logger.debug("Upserted schedule cache for %s (in-memory)", doc.date)
            return

        await self._container.upsert_item(body=doc.to_cosmos())
        logger.debug("Upserted schedule cache for %s", doc.date)

    async def get_range(self, dates: list[str]) -> dict[str, DayScheduleCache]:
        """Get cached schedules for multiple dates.

        Args:
            dates: List of date strings (YYYY-MM-DD)

        Returns:
            Dict mapping date string to cached schedule (only found dates)
        """
        result = {}
        for date_str in dates:
            cached = await self.get(date_str)
            if cached is not None:
                result[date_str] = cached
        return result

    async def get_for_time(self, dt: datetime) -> list[ScheduleEntryCache]:
        """Get schedule entries for everyone on duty at a specific time.

        Fetches both today's and yesterday's schedules and filters each
        entry by whether its shift window actually covers ``dt``. No
        hardcoded shift change hour — the entry start/end times determine
        coverage.

        Args:
            dt: The datetime to query (e.g. call time)

        Returns:
            Schedule entries on duty at that time. Empty list if no
            schedule is cached for either day.
        """
        today_str = dt.strftime("%Y-%m-%d")
        yesterday_str = (dt - timedelta(days=1)).strftime("%Y-%m-%d")

        today = await self.get(today_str)
        yesterday = await self.get(yesterday_str)

        results: list[ScheduleEntryCache] = []

        if yesterday:
            query_date = date.fromisoformat(yesterday_str)
            results.extend(e for e in yesterday.entries if _entry_covers_time(e, query_date, dt))
        if today:
            query_date = date.fromisoformat(today_str)
            results.extend(e for e in today.entries if _entry_covers_time(e, query_date, dt))

        return results


def _entry_covers_time(
    entry: ScheduleEntryCache,
    schedule_date: date,
    dt: datetime,
) -> bool:
    """Check if a schedule entry's shift window covers a given time.

    Computes absolute start/end from the schedule date and the entry's
    HH:MM times. If end_time <= start_time, the shift wraps to the next
    day (e.g. 18:00-12:00 = 18:00 today to 12:00 tomorrow).

    Args:
        entry: Schedule entry with start_time/end_time as HH:MM
        schedule_date: The date this entry belongs to
        dt: The datetime to check

    Returns:
        True if dt falls within [start, end).
    """
    if not entry.start_time or not entry.end_time:
        return False

    try:
        start_h, start_m = (int(x) for x in entry.start_time.split(":"))
        end_h, end_m = (int(x) for x in entry.end_time.split(":"))
    except (ValueError, AttributeError):
        return False

    # Minutes since schedule_date midnight for start, end, and query time
    start_offset = start_h * 60 + start_m
    end_offset = end_h * 60 + end_m

    days_from_schedule = (dt.date() - schedule_date).days
    query_offset = days_from_schedule * 24 * 60 + dt.hour * 60 + dt.minute

    # If end <= start, shift wraps to next day (e.g. 18:00-12:00)
    if end_offset <= start_offset:
        end_offset += 24 * 60

    return start_offset <= query_offset < end_offset
