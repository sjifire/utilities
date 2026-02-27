"""Async Cosmos DB operations for NERIS data.

Includes:
- ``NerisReportStore`` — cached NERIS report summaries
- ``NerisSnapshotStore`` — pre-update snapshots (30-day TTL)

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.
"""

import logging
import os
from typing import ClassVar, Self

from sjifire.core.config import get_cosmos_container, get_cosmos_database
from sjifire.ops.neris.models import NerisReportDocument, NerisSnapshotDocument

logger = logging.getLogger(__name__)
CONTAINER_NAME = "neris-reports"


class NerisReportStore:
    """Async read/write for cached NERIS report summaries in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with NerisReportStore() as store:
            await store.upsert(doc)
            reports = await store.list_all()
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
            logger.warning("No COSMOS_ENDPOINT set — using in-memory NERIS cache (dev only)")
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

    async def upsert(self, doc: NerisReportDocument) -> None:
        """Write or update a cached NERIS report summary.

        Args:
            doc: NERIS report document to upsert
        """
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.debug("Upserted NERIS report %s (in-memory)", doc.id)
            return

        await self._container.upsert_item(body=doc.to_cosmos())
        logger.debug("Upserted NERIS report %s", doc.id)

    async def bulk_upsert(self, reports: list[dict]) -> int:
        """Write NERIS API summary dicts to the cache.

        Each dict should have ``incident_number``, ``neris_id``,
        ``status``, ``incident_type``, and ``call_create`` keys
        (the format returned by ``_list_neris_reports``).

        Args:
            reports: List of NERIS summary dicts from the API

        Returns:
            Number of documents written
        """
        count = 0
        for r in reports:
            incident_number = r.get("incident_number", "")
            normalized = _normalize_incident_number(incident_number)
            if not normalized:
                continue
            year = f"20{normalized[:2]}" if len(normalized) >= 2 else ""
            doc = NerisReportDocument(
                id=normalized,
                year=year,
                neris_id=r.get("neris_id", ""),
                incident_number=incident_number,
                determinant_code=r.get("determinant_code", ""),
                status=r.get("status", ""),
                incident_type=r.get("incident_type", ""),
                call_create=r.get("call_create", ""),
            )
            await self.upsert(doc)
            count += 1
        return count

    async def list_all(self, *, max_items: int = 100) -> list[NerisReportDocument]:
        """List all cached NERIS report summaries.

        Args:
            max_items: Maximum number of results

        Returns:
            List of cached documents, ordered by call_create descending
        """
        if self._in_memory:
            results = [
                NerisReportDocument.from_cosmos(data)
                for data in self._memory.values()
                if data.get("year") != "meta"  # Skip metadata docs (checkpoint)
            ]
            results.sort(key=lambda d: d.call_create, reverse=True)
            return results[:max_items]

        query = "SELECT TOP @limit * FROM c WHERE c.year != 'meta' ORDER BY c.call_create DESC"
        parameters: list[dict] = [{"name": "@limit", "value": max_items}]

        items = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
        ):
            items.append(NerisReportDocument.from_cosmos(item))
            if len(items) >= max_items:
                break

        return items

    async def list_as_lookup(self, *, max_items: int = 100) -> dict:
        """Read cache and return lookup + reports in dashboard format.

        Returns:
            Dict with ``lookup`` (normalized incident number → summary)
            and ``reports`` (list of summary dicts).
        """
        docs = await self.list_all(max_items=max_items)

        lookup: dict[str, dict] = {}
        reports: list[dict] = []
        for doc in docs:
            summary = doc.to_summary()
            reports.append(summary)
            normalized = _normalize_incident_number(doc.incident_number)
            if normalized:
                lookup[normalized] = summary
            # Legacy reports store the dispatch ID in determinant_code
            if doc.determinant_code:
                det_normalized = _normalize_incident_number(doc.determinant_code)
                if det_normalized and det_normalized != normalized:
                    lookup[det_normalized] = summary

        return {"lookup": lookup, "reports": reports}

    async def get_sync_checkpoint(self) -> str | None:
        """Read the last sync timestamp (high-water mark).

        Returns:
            ISO timestamp string, or None if no previous sync.
        """
        if self._in_memory:
            item = self._memory.get("sync-checkpoint")
            return item.get("last_modified") if item else None

        try:
            item = await self._container.read_item(item="sync-checkpoint", partition_key="meta")
            return item.get("last_modified")
        except Exception:
            return None

    async def set_sync_checkpoint(self, last_modified: str) -> None:
        """Store the sync timestamp (high-water mark).

        Args:
            last_modified: ISO timestamp of the last successful sync
        """
        body = {
            "id": "sync-checkpoint",
            "year": "meta",
            "last_modified": last_modified,
        }
        if self._in_memory:
            self._memory["sync-checkpoint"] = body
            return

        await self._container.upsert_item(body=body)
        logger.info("Stored sync checkpoint: %s", last_modified)


def _normalize_incident_number(number: str) -> str:
    """Normalize incident number for cross-referencing.

    Dispatch IDs use "26-001980", NERIS uses "26001980".
    """
    return number.replace("-", "")


# ---------------------------------------------------------------------------
# NERIS Snapshot Store (pre-update backups with 30-day TTL)
# ---------------------------------------------------------------------------

SNAPSHOT_CONTAINER_NAME = "neris-snapshots"


class NerisSnapshotStore:
    """Async CRUD for NERIS pre-update snapshots in Cosmos DB.

    Snapshots are write-once, read-rarely — used for rollback investigation
    if a patch to NERIS needs to be undone. The 30-day Cosmos TTL on each
    document handles automatic cleanup.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with NerisSnapshotStore() as store:
            await store.create(doc)
    """

    _memory: ClassVar[dict[str, dict]] = {}

    def __init__(self) -> None:
        """Initialize store. Call ``__aenter__`` to connect."""
        self._container = None
        self._in_memory = False

    async def __aenter__(self) -> Self:
        """Get a container client from the shared Cosmos connection pool."""
        self._container = await get_cosmos_container(SNAPSHOT_CONTAINER_NAME)
        if self._container is None:
            self._in_memory = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """No-op — shared Cosmos client stays alive."""
        self._container = None

    async def create(self, doc: NerisSnapshotDocument) -> NerisSnapshotDocument:
        """Create a new snapshot document.

        Args:
            doc: Snapshot document to store

        Returns:
            The created document
        """
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.info("Created NERIS snapshot %s (in-memory)", doc.id)
            return doc

        result = await self._container.create_item(body=doc.to_cosmos())
        logger.info("Created NERIS snapshot %s (year=%s)", doc.id, doc.year)
        return NerisSnapshotDocument.from_cosmos(result)

    async def get_by_id(self, snapshot_id: str, year: str) -> NerisSnapshotDocument | None:
        """Get a snapshot by ID and year (partition key).

        Args:
            snapshot_id: Document ID
            year: Four-digit year string (partition key)

        Returns:
            NerisSnapshotDocument if found, None otherwise
        """
        if self._in_memory:
            data = self._memory.get(snapshot_id)
            if data and data.get("year") == year:
                return NerisSnapshotDocument.from_cosmos(data)
            return None

        try:
            result = await self._container.read_item(
                item=snapshot_id,
                partition_key=year,
            )
            return NerisSnapshotDocument.from_cosmos(result)
        except Exception:
            return None

    async def list_by_neris_id(
        self, neris_id: str, year: str | None = None, *, max_items: int = 20
    ) -> list[NerisSnapshotDocument]:
        """List snapshots for a NERIS incident.

        Args:
            neris_id: Compound NERIS ID
            year: Optional year filter (partition key)
            max_items: Maximum results

        Returns:
            List of snapshots, newest first
        """
        if self._in_memory:
            results = [
                NerisSnapshotDocument.from_cosmos(data)
                for data in self._memory.values()
                if data.get("neris_id") == neris_id and (year is None or data.get("year") == year)
            ]
            results.sort(key=lambda d: d.patched_at, reverse=True)
            return results[:max_items]

        conditions = ["c.neris_id = @neris_id"]
        parameters: list[dict] = [{"name": "@neris_id", "value": neris_id}]
        if year:
            conditions.append("c.year = @year")
            parameters.append({"name": "@year", "value": year})

        where = " AND ".join(conditions)
        query = f"SELECT * FROM c WHERE {where} ORDER BY c.patched_at DESC"  # noqa: S608

        items = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            max_item_count=max_items,
        ):
            items.append(NerisSnapshotDocument.from_cosmos(item))
            if len(items) >= max_items:
                break

        return items
