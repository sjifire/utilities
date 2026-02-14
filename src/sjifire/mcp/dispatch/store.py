"""Async Cosmos DB operations for dispatch call documents.

Stores completed dispatch calls for fast retrieval and historical access
beyond iSpyFire's 30-day retention window.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.

This module is the **single source of truth** for all dispatch data
operations: Cosmos CRUD, iSpyFire fetching, and enrichment. Callers
(MCP tools, CLI scripts) should use store methods rather than calling
``enrich_dispatch`` or ``ISpyFireClient`` directly.
"""

import asyncio
import logging
import os
import re
from datetime import UTC, datetime
from typing import ClassVar, Self

from dotenv import load_dotenv

from sjifire.core.config import get_cosmos_database
from sjifire.ispyfire.models import DispatchCall
from sjifire.mcp.dispatch.models import DispatchCallDocument

logger = logging.getLogger(__name__)

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

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

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

    async def list_recent(self, *, limit: int = 15) -> list[DispatchCallDocument]:
        """List the most recent dispatch calls.

        Args:
            limit: Maximum number of results

        Returns:
            List of documents ordered by time_reported descending
        """
        if self._in_memory:
            results = [DispatchCallDocument.from_cosmos(data) for data in self._memory.values()]
            results.sort(
                key=lambda d: d.time_reported.isoformat() if d.time_reported else "",
                reverse=True,
            )
            return results[:limit]

        query = "SELECT TOP @limit * FROM c ORDER BY c.time_reported DESC"
        parameters: list[dict] = [{"name": "@limit", "value": limit}]

        items = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
        ):
            items.append(DispatchCallDocument.from_cosmos(item))
            if len(items) >= limit:
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

    # ------------------------------------------------------------------
    # Lookup (unified UUID or dispatch ID)
    # ------------------------------------------------------------------

    async def lookup(self, call_id: str) -> DispatchCallDocument | None:
        """Look up a call in Cosmos DB by UUID or dispatch ID.

        Dispatch IDs (e.g. "26-001678") route to ``get_by_dispatch_id``.
        UUIDs try the current year, then the previous year.

        Args:
            call_id: Call UUID or dispatch ID

        Returns:
            DispatchCallDocument if found, None otherwise
        """
        if re.match(r"\d{2}-\d+", call_id):
            return await self.get_by_dispatch_id(call_id)

        current_year = str(datetime.now(UTC).year)
        doc = await self.get(call_id, current_year)
        if doc:
            return doc
        prev_year = str(int(current_year) - 1)
        return await self.get(call_id, prev_year)

    # ------------------------------------------------------------------
    # Enrichment (single source of truth for enrich_dispatch calls)
    # ------------------------------------------------------------------

    async def _enrich(self, doc: DispatchCallDocument) -> DispatchCallDocument:
        """Enrich a document with AI analysis, crew roster, and timing.

        This is the ONLY place ``enrich_dispatch`` should be called.

        Args:
            doc: Document to enrich

        Returns:
            The enriched document (same instance, mutated)
        """
        from sjifire.mcp.dispatch.enrich import enrich_dispatch

        try:
            doc.analysis = await enrich_dispatch(doc)
        except Exception:
            logger.warning("Enrichment failed for %s", doc.long_term_call_id, exc_info=True)
        return doc

    async def store_call(self, call: DispatchCall) -> DispatchCallDocument:
        """Convert, enrich, and store a single dispatch call.

        Enriches unless the document already has analysis data.

        Args:
            call: Raw DispatchCall dataclass from iSpyFire

        Returns:
            The stored and (possibly) enriched document
        """
        doc = DispatchCallDocument.from_dispatch_call(call)
        if not doc.analysis.incident_commander:
            await self._enrich(doc)
        await self.upsert(doc)
        return doc

    async def store_completed(self, calls: list[DispatchCall]) -> int:
        """Store completed dispatch calls with enrichment.

        Skips calls that are not completed.

        Args:
            calls: List of DispatchCall dataclasses

        Returns:
            Number of calls stored
        """
        count = 0
        for call in calls:
            if not call.is_completed:
                continue
            await self.store_call(call)
            count += 1

        if count:
            logger.info("Stored %d completed dispatch calls", count)
        return count

    async def enrich_stored(
        self, *, force: bool = False, limit: int = 100
    ) -> list[DispatchCallDocument]:
        """Re-enrich stored documents.

        Processes documents missing analysis, or all documents when
        ``force=True``.

        Args:
            force: Re-analyze all documents, even those with existing analysis
            limit: Maximum number of documents to process

        Returns:
            All targeted documents (enriched or not). Check
            ``doc.analysis.incident_commander`` to see if enrichment
            produced results.
        """
        docs = await self.list_recent(limit=limit)

        if not force:
            docs = [d for d in docs if not d.analysis.incident_commander and not d.analysis.summary]

        for doc in docs:
            await self._enrich(doc)
            if doc.analysis.incident_commander or doc.analysis.summary:
                await self.upsert(doc)

        return docs

    # ------------------------------------------------------------------
    # iSpyFire integration (fetch + store in one step)
    # ------------------------------------------------------------------

    async def get_or_fetch(self, call_id: str) -> DispatchCallDocument | None:
        """Get a call from Cosmos DB, falling back to iSpyFire.

        Checks the store first. If not found, fetches from iSpyFire.
        Completed calls are automatically enriched and stored.

        Args:
            call_id: Call UUID or dispatch ID (e.g. "26-001678")

        Returns:
            DispatchCallDocument if found in either source, None otherwise
        """
        doc = await self.lookup(call_id)
        if doc:
            return doc

        call = await asyncio.to_thread(self._fetch_call, call_id)
        if call is None:
            return None

        if call.is_completed:
            return await self.store_call(call)
        return DispatchCallDocument.from_dispatch_call(call)

    async def fetch_and_store_recent(self, days: int) -> list[DispatchCallDocument]:
        """Fetch recent calls from iSpyFire and store completed ones.

        Returns all calls as documents (both open and completed).
        Completed calls are enriched and stored as a side effect.

        Args:
            days: Number of days to look back

        Returns:
            List of DispatchCallDocuments for all fetched calls
        """
        calls = await asyncio.to_thread(self._fetch_recent, days)
        stored = 0
        docs = []
        for call in calls:
            if call.is_completed:
                doc = await self.store_call(call)
                stored += 1
            else:
                doc = DispatchCallDocument.from_dispatch_call(call)
            docs.append(doc)

        if stored:
            logger.info("Stored %d completed calls from listing", stored)
        return docs

    async def fetch_open(self) -> list[DispatchCallDocument]:
        """Fetch currently open calls from iSpyFire.

        Open calls are not stored (they're mutable until completed).

        Returns:
            List of DispatchCallDocuments for open calls
        """
        calls = await asyncio.to_thread(self._fetch_open)
        return [DispatchCallDocument.from_dispatch_call(c) for c in calls]

    # ------------------------------------------------------------------
    # iSpyFire client helpers (sync, run via asyncio.to_thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_call(call_id: str) -> DispatchCall | None:
        """Fetch a single call from iSpyFire (blocking)."""
        from sjifire.ispyfire.client import ISpyFireClient

        with ISpyFireClient() as client:
            return client.get_call_details(call_id)

    @staticmethod
    def _fetch_recent(days: int) -> list[DispatchCall]:
        """Fetch recent calls with full details from iSpyFire (blocking)."""
        from sjifire.ispyfire.client import ISpyFireClient

        with ISpyFireClient() as client:
            summaries = client.get_calls(days=days)
            return [d for s in summaries if (d := client.get_call_details(s.id))]

    @staticmethod
    def _fetch_open() -> list[DispatchCall]:
        """Fetch currently open calls from iSpyFire (blocking)."""
        from sjifire.ispyfire.client import ISpyFireClient

        with ISpyFireClient() as client:
            return client.get_open_calls()
