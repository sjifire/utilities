"""Async Cosmos DB operations for cached schedule data.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.
"""

import logging
import os
from typing import ClassVar, Self

from dotenv import load_dotenv

from sjifire.mcp.schedule.models import DayScheduleCache

logger = logging.getLogger(__name__)

DATABASE_NAME = "sjifire-incidents"
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

        database = self._client.get_database_client(DATABASE_NAME)
        self._container = database.get_container_client(CONTAINER_NAME)
        logger.info("Connected to Cosmos DB: %s/%s", DATABASE_NAME, CONTAINER_NAME)
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
