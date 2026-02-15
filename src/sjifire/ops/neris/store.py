"""Async Cosmos DB operations for cached NERIS report summaries.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.
"""

import logging
import os
from typing import ClassVar, Self

from dotenv import load_dotenv

from sjifire.core.config import get_cosmos_database
from sjifire.ops.neris.models import NerisReportDocument

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
            results = [NerisReportDocument.from_cosmos(data) for data in self._memory.values()]
            results.sort(key=lambda d: d.call_create, reverse=True)
            return results[:max_items]

        query = "SELECT TOP @limit * FROM c ORDER BY c.call_create DESC"
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
            # ESO-originated reports store the dispatch ID in determinant_code
            if doc.determinant_code:
                det_normalized = _normalize_incident_number(doc.determinant_code)
                if det_normalized and det_normalized != normalized:
                    lookup[det_normalized] = summary

        return {"lookup": lookup, "reports": reports}


def _normalize_incident_number(number: str) -> str:
    """Normalize incident number for cross-referencing.

    Dispatch IDs use "26-001980", NERIS uses "26001980".
    """
    return number.replace("-", "")
