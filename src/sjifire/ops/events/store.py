"""Async Cosmos DB operations for event records.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing.
"""

import logging
from datetime import UTC, datetime
from typing import ClassVar

from sjifire.ops.cosmos import CosmosStore
from sjifire.ops.events.models import EventRecord

logger = logging.getLogger(__name__)


class EventStore(CosmosStore):
    """Async CRUD for event records in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with EventStore() as store:
            record = await store.upsert(doc)
            records = await store.list_by_year("2026")
    """

    _container_name: ClassVar[str] = "events"
    _memory: ClassVar[dict[str, dict]] = {}

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

        return await self._query_one(
            "SELECT * FROM c WHERE c.id = @id",
            [{"name": "@id", "value": record_id}],
            EventRecord,
        )

    async def get_by_calendar_event_id(self, event_id: str) -> EventRecord | None:
        """Find an event record by calendar event ID (cross-partition)."""
        if self._in_memory:
            for data in self._memory.values():
                if data.get("calendar_event_id") == event_id:
                    return EventRecord.from_cosmos(data)
            return None

        return await self._query_one(
            "SELECT * FROM c WHERE c.calendar_event_id = @eid",
            [{"name": "@eid", "value": event_id}],
            EventRecord,
        )

    async def list_by_year(self, year: str) -> list[EventRecord]:
        """List all event records for a year (single-partition)."""
        if self._in_memory:
            results = [
                EventRecord.from_cosmos(d) for d in self._memory.values() if d.get("year") == year
            ]
            results.sort(key=lambda r: r.event_date)
            return results

        return await self._query_many(
            "SELECT * FROM c WHERE c.year = @year ORDER BY c.event_date ASC",
            [{"name": "@year", "value": year}],
            EventRecord,
            max_items=500,
            partition_key=year,
        )

    async def list_recent(self, *, max_items: int = 200) -> list[EventRecord]:
        """List recent event records (cross-partition, newest first)."""
        if self._in_memory:
            results = [EventRecord.from_cosmos(d) for d in self._memory.values()]
            results.sort(key=lambda r: r.event_date, reverse=True)
            return results[:max_items]

        return await self._query_many(
            "SELECT * FROM c ORDER BY c.event_date DESC",
            None,
            EventRecord,
            max_items=max_items,
        )

    async def delete(self, record_id: str, year: str) -> None:
        """Delete an event record."""
        if self._in_memory:
            self._memory.pop(record_id, None)
            logger.info("Deleted event %s (in-memory)", record_id)
            return

        await self._container.delete_item(item=record_id, partition_key=year)
        logger.info("Deleted event %s", record_id)
