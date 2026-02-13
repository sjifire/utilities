"""Async Cosmos DB operations for dispatch call documents.

Stores completed dispatch calls for fast retrieval and historical access
beyond iSpyFire's 30-day retention window.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.
"""

import logging
import os
from typing import ClassVar, Self

from dotenv import load_dotenv

from sjifire.ispyfire.models import DispatchCall
from sjifire.mcp.dispatch.models import DispatchCallDocument

logger = logging.getLogger(__name__)

DATABASE_NAME = "sjifire-incidents"
CONTAINER_NAME = "dispatch-calls"


class DispatchStore:
    """Async CRUD for dispatch call documents in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured,
    so ``mcp dev`` works out of the box without Azure infrastructure.

    Usage::

        async with DispatchStore() as store:
            doc = await store.get("call-uuid", "2026")
            await store.upsert(doc)
    """

    # Shared in-memory store across instances (persists for server lifetime)
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
            logger.warning("No COSMOS_ENDPOINT set — using in-memory dispatch store (dev only)")
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

    async def get(self, call_uuid: str, year: str) -> DispatchCallDocument | None:
        """Point-read a dispatch call by UUID and year (partition key).

        Args:
            call_uuid: The iSpyFire UUID (_id)
            year: Four-digit year string (partition key)

        Returns:
            DispatchCallDocument if found, None otherwise
        """
        if self._in_memory:
            data = self._memory.get(call_uuid)
            if data and data.get("year") == year:
                return DispatchCallDocument.from_cosmos(data)
            return None

        try:
            result = await self._container.read_item(
                item=call_uuid,
                partition_key=year,
            )
            return DispatchCallDocument.from_cosmos(result)
        except Exception:
            logger.debug("Dispatch call not found: %s (year=%s)", call_uuid, year)
            return None

    async def get_by_dispatch_id(self, dispatch_id: str) -> DispatchCallDocument | None:
        """Find a dispatch call by its dispatch ID (e.g. "26-001678").

        Uses the two-digit prefix to target a single partition when possible.

        Args:
            dispatch_id: LongTermCallID, e.g. "26-001678"

        Returns:
            DispatchCallDocument if found, None otherwise
        """
        from sjifire.mcp.dispatch.models import year_from_dispatch_id

        year = year_from_dispatch_id(dispatch_id)

        if self._in_memory:
            for data in self._memory.values():
                if data.get("long_term_call_id") == dispatch_id:
                    return DispatchCallDocument.from_cosmos(data)
            return None

        query = "SELECT * FROM c WHERE c.long_term_call_id = @dispatch_id"
        parameters: list[dict] = [{"name": "@dispatch_id", "value": dispatch_id}]

        # Single-partition query if we can derive the year
        partition_key = year if year else None

        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            partition_key=partition_key,
            max_item_count=1,
        ):
            return DispatchCallDocument.from_cosmos(item)

        return None

    async def upsert(self, doc: DispatchCallDocument) -> DispatchCallDocument:
        """Write or update a dispatch call document.

        Args:
            doc: Document to upsert

        Returns:
            The upserted document
        """
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.debug("Upserted dispatch call %s (in-memory)", doc.id)
            return doc

        result = await self._container.upsert_item(body=doc.to_cosmos())
        logger.debug("Upserted dispatch call %s", doc.id)
        return DispatchCallDocument.from_cosmos(result)

    async def list_by_date_range(
        self,
        start_date: str,
        end_date: str,
        *,
        max_items: int = 100,
    ) -> list[DispatchCallDocument]:
        """List dispatch calls within a date range.

        Queries on ``time_reported`` which is stored as an ISO 8601
        datetime string — lexicographic comparison works for
        YYYY-MM-DD prefixed strings.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            max_items: Maximum results

        Returns:
            List of matching documents, ordered by time_reported desc
        """
        if self._in_memory:
            results = []
            for data in self._memory.values():
                tr = data.get("time_reported") or ""
                if tr >= start_date and tr <= end_date + "~":
                    results.append(DispatchCallDocument.from_cosmos(data))
            results.sort(
                key=lambda d: d.time_reported.isoformat() if d.time_reported else "",
                reverse=True,
            )
            return results[:max_items]

        query = (
            "SELECT * FROM c "
            "WHERE c.time_reported >= @start AND c.time_reported <= @end "
            "ORDER BY c.time_reported DESC"
        )
        parameters = [
            {"name": "@start", "value": start_date},
            {"name": "@end", "value": end_date + "~"},
        ]

        items = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            max_item_count=max_items,
        ):
            items.append(DispatchCallDocument.from_cosmos(item))
            if len(items) >= max_items:
                break

        return items

    async def list_by_address(
        self,
        address: str,
        *,
        exclude_id: str = "",
        max_items: int = 10,
    ) -> list[DispatchCallDocument]:
        """List dispatch calls at the same address (site history).

        Args:
            address: Street address to match exactly
            exclude_id: Call UUID to exclude (the current call)
            max_items: Maximum results

        Returns:
            List of matching documents, ordered by time_reported desc
        """
        if self._in_memory:
            results = [
                DispatchCallDocument.from_cosmos(data)
                for data in self._memory.values()
                if data.get("address") == address and data.get("id") != exclude_id
            ]
            results.sort(
                key=lambda d: d.time_reported.isoformat() if d.time_reported else "",
                reverse=True,
            )
            return results[:max_items]

        query = (
            "SELECT * FROM c "
            "WHERE c.address = @address AND c.id != @exclude_id "
            "ORDER BY c.time_reported DESC"
        )
        parameters = [
            {"name": "@address", "value": address},
            {"name": "@exclude_id", "value": exclude_id},
        ]

        items = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            max_item_count=max_items,
        ):
            items.append(DispatchCallDocument.from_cosmos(item))
            if len(items) >= max_items:
                break

        return items

    async def get_existing_ids(self, ids: list[str]) -> set[str]:
        """Check which call UUIDs already exist in the store.

        Used by the archive command to skip re-fetching details for
        calls that are already stored.

        Args:
            ids: List of iSpyFire call UUIDs to check

        Returns:
            Set of UUIDs that already exist in the store
        """
        if not ids:
            return set()

        if self._in_memory:
            return {uid for uid in ids if uid in self._memory}

        # Cross-partition query using ARRAY_CONTAINS
        query = "SELECT c.id FROM c WHERE ARRAY_CONTAINS(@ids, c.id)"
        parameters: list[dict] = [{"name": "@ids", "value": ids}]

        existing: set[str] = set()
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
        ):
            existing.add(item["id"])

        return existing

    async def store_completed(self, calls: list[DispatchCall]) -> int:
        """Store completed dispatch calls.

        Skips calls that are not completed. Upserts each completed call
        to Cosmos DB.

        Args:
            calls: List of DispatchCall dataclasses

        Returns:
            Number of calls stored
        """
        count = 0
        for call in calls:
            if not call.is_completed:
                continue
            doc = DispatchCallDocument.from_dispatch_call(call)
            await self.upsert(doc)
            count += 1

        if count:
            logger.info("Stored %d completed dispatch calls", count)
        return count
