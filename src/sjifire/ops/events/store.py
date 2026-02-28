"""Async Cosmos DB operations for event records.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing.
"""

import logging
from datetime import UTC, datetime
from typing import ClassVar, Self

from sjifire.core.config import get_cosmos_container
from sjifire.ops.events.models import EventRecord

logger = logging.getLogger(__name__)
CONTAINER_NAME = "events"


class EventStore:
    """Async CRUD for event records in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with EventStore() as store:
            record = await store.upsert(doc)
            records = await store.list_by_year("2026")
    """

    _memory: ClassVar[dict[str, dict]] = {}

    def __init__(self) -> None:
        """Initialize store. Call ``__aenter__`` to connect."""
        self._container = None
        self._in_memory = False

    async def __aenter__(self) -> Self:
        """Get a container client from the shared Cosmos connection pool."""
        self._container = await get_cosmos_container(CONTAINER_NAME)
        if self._container is None:
            self._in_memory = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """No-op — shared Cosmos client stays alive."""
        self._container = None

    async def upsert(self, doc: EventRecord) -> EventRecord:
        """Create or update an event record."""
        doc.updated_at = datetime.now(UTC)
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.info("Upserted event %s (in-memory, year=%s)", doc.id, doc.year)
            return doc

        result = await self._container.upsert_item(body=doc.to_cosmos())
        logger.info("Upserted event %s (year=%s)", doc.id, doc.year)
        return EventRecord.from_cosmos(result)

    async def get_by_id(self, record_id: str) -> EventRecord | None:
        """Find an event record by ID (cross-partition)."""
        if self._in_memory:
            data = self._memory.get(record_id)
            return EventRecord.from_cosmos(data) if data else None

        query = "SELECT * FROM c WHERE c.id = @id"
        parameters: list[dict] = [{"name": "@id", "value": record_id}]
        async for item in self._container.query_items(
            query=query, parameters=parameters, max_item_count=1
        ):
            return EventRecord.from_cosmos(item)
        return None

    async def get_by_calendar_event_id(self, event_id: str) -> EventRecord | None:
        """Find an event record by calendar event ID (cross-partition)."""
        if self._in_memory:
            for data in self._memory.values():
                if data.get("calendar_event_id") == event_id:
                    return EventRecord.from_cosmos(data)
            return None

        query = "SELECT * FROM c WHERE c.calendar_event_id = @eid"
        parameters: list[dict] = [{"name": "@eid", "value": event_id}]
        async for item in self._container.query_items(
            query=query, parameters=parameters, max_item_count=1
        ):
            return EventRecord.from_cosmos(item)
        return None

    async def list_by_year(self, year: str) -> list[EventRecord]:
        """List all event records for a year (single-partition)."""
        if self._in_memory:
            results = [
                EventRecord.from_cosmos(d) for d in self._memory.values() if d.get("year") == year
            ]
            results.sort(key=lambda r: r.event_date)
            return results

        query = "SELECT * FROM c WHERE c.year = @year ORDER BY c.event_date ASC"
        parameters: list[dict] = [{"name": "@year", "value": year}]
        return [
            EventRecord.from_cosmos(item)
            async for item in self._container.query_items(
                query=query, parameters=parameters, partition_key=year
            )
        ]

    async def list_recent(self, *, max_items: int = 200) -> list[EventRecord]:
        """List recent event records (cross-partition, newest first)."""
        if self._in_memory:
            results = [EventRecord.from_cosmos(d) for d in self._memory.values()]
            results.sort(key=lambda r: r.event_date, reverse=True)
            return results[:max_items]

        query = "SELECT * FROM c ORDER BY c.event_date DESC"
        items = []
        async for item in self._container.query_items(query=query, max_item_count=max_items):
            items.append(EventRecord.from_cosmos(item))
            if len(items) >= max_items:
                break
        return items

    async def delete(self, record_id: str, year: str) -> None:
        """Delete an event record."""
        if self._in_memory:
            self._memory.pop(record_id, None)
            logger.info("Deleted event %s (in-memory)", record_id)
            return

        await self._container.delete_item(item=record_id, partition_key=year)
        logger.info("Deleted event %s", record_id)
