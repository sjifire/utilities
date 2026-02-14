"""Async Cosmos DB operations for incident documents.

Internal implementation detail -- not exposed to MCP tools directly.
Tools go through this store with access control checks applied first.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.
"""

import logging
import os
from typing import ClassVar, Self

from dotenv import load_dotenv

from sjifire.core.config import get_cosmos_database
from sjifire.mcp.incidents.models import IncidentDocument

logger = logging.getLogger(__name__)
CONTAINER_NAME = "incidents"


class IncidentStore:
    """Async CRUD operations for incident documents in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured,
    so ``mcp dev`` works out of the box without Azure infrastructure.

    Usage::

        async with IncidentStore() as store:
            incident = await store.create(doc)
            incidents = await store.list_by_status("draft")
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
            # No Cosmos DB configured -- use in-memory store for dev
            logger.warning("No COSMOS_ENDPOINT set — using in-memory incident store (dev only)")
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

    async def create(self, doc: IncidentDocument) -> IncidentDocument:
        """Create a new incident document.

        Args:
            doc: Incident document to create

        Returns:
            The created document (with any server-side fields populated)
        """
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.info("Created incident %s (in-memory, year=%s)", doc.id, doc.year)
            return doc

        result = await self._container.create_item(body=doc.to_cosmos())
        logger.info("Created incident %s (year=%s)", doc.id, doc.year)
        return IncidentDocument.from_cosmos(result)

    async def get(self, incident_id: str, year: str) -> IncidentDocument | None:
        """Get an incident by ID and year (partition key).

        Args:
            incident_id: Document ID
            year: Four-digit year string (partition key)

        Returns:
            IncidentDocument if found, None otherwise
        """
        if self._in_memory:
            data = self._memory.get(incident_id)
            if data and data.get("year") == year:
                return IncidentDocument.from_cosmos(data)
            return None

        try:
            result = await self._container.read_item(
                item=incident_id,
                partition_key=year,
            )
            return IncidentDocument.from_cosmos(result)
        except Exception:
            logger.debug("Incident not found: %s (year=%s)", incident_id, year)
            return None

    async def get_by_id(self, incident_id: str) -> IncidentDocument | None:
        """Find an incident by document ID (cross-partition).

        Slower than ``get()`` but doesn't require the year partition key.
        Suitable for small datasets like a single fire department's incidents.

        Args:
            incident_id: Document ID (UUID)

        Returns:
            IncidentDocument if found, None otherwise
        """
        if self._in_memory:
            data = self._memory.get(incident_id)
            return IncidentDocument.from_cosmos(data) if data else None

        query = "SELECT * FROM c WHERE c.id = @id"
        parameters: list[dict] = [{"name": "@id", "value": incident_id}]

        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            max_item_count=1,
        ):
            return IncidentDocument.from_cosmos(item)

        return None

    async def update(self, doc: IncidentDocument) -> IncidentDocument:
        """Update an existing incident document.

        Args:
            doc: Document with updated fields (must have valid id and year)

        Returns:
            The updated document
        """
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.info("Updated incident %s (in-memory)", doc.id)
            return doc

        result = await self._container.replace_item(
            item=doc.id,
            body=doc.to_cosmos(),
        )
        logger.info("Updated incident %s", doc.id)
        return IncidentDocument.from_cosmos(result)

    async def delete(self, incident_id: str, year: str) -> None:
        """Delete an incident document.

        Args:
            incident_id: Document ID
            year: Four-digit year string (partition key)
        """
        if self._in_memory:
            self._memory.pop(incident_id, None)
            logger.info("Deleted incident %s (in-memory)", incident_id)
            return

        await self._container.delete_item(
            item=incident_id,
            partition_key=year,
        )
        logger.info("Deleted incident %s", incident_id)

    async def list_by_status(
        self,
        status: str | None = None,
        *,
        station: str | None = None,
        exclude_status: str | None = None,
        max_items: int = 50,
    ) -> list[IncidentDocument]:
        """List incidents, optionally filtered by status and/or station.

        Args:
            status: Filter by status (draft, in_progress, ready_review, submitted)
            station: Filter by station code
            exclude_status: Exclude incidents with this status
            max_items: Maximum number of results

        Returns:
            List of matching incident documents, sorted by incident_date ascending
        """
        if self._in_memory:
            return self._filter_memory(
                status=status, station=station, exclude_status=exclude_status, max_items=max_items
            )

        conditions = []
        parameters = []

        if status:
            conditions.append("c.status = @status")
            parameters.append({"name": "@status", "value": status})
        if exclude_status:
            conditions.append("c.status != @exclude_status")
            parameters.append({"name": "@exclude_status", "value": exclude_status})
        if station:
            conditions.append("c.station = @station")
            parameters.append({"name": "@station", "value": station})

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM c{where_clause} ORDER BY c.incident_date ASC"

        items = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters or None,
            max_item_count=max_items,
        ):
            items.append(IncidentDocument.from_cosmos(item))
            if len(items) >= max_items:
                break

        return items

    async def list_for_user(
        self,
        user_email: str,
        *,
        status: str | None = None,
        exclude_status: str | None = None,
        max_items: int = 50,
    ) -> list[IncidentDocument]:
        """List incidents accessible to a specific user.

        Returns incidents where the user is the creator or a crew member.

        Args:
            user_email: User's email address (lowered)
            status: Optional status filter
            exclude_status: Exclude incidents with this status
            max_items: Maximum results

        Returns:
            List of accessible incidents, sorted by incident_date ascending
        """
        if self._in_memory:
            return self._filter_memory(
                status=status,
                exclude_status=exclude_status,
                user_email=user_email.lower(),
                max_items=max_items,
            )

        conditions = ['(c.created_by = @email OR ARRAY_CONTAINS(c.crew, {"email": @email}, true))']
        parameters: list[dict] = [{"name": "@email", "value": user_email.lower()}]

        if status:
            conditions.append("c.status = @status")
            parameters.append({"name": "@status", "value": status})
        if exclude_status:
            conditions.append("c.status != @exclude_status")
            parameters.append({"name": "@exclude_status", "value": exclude_status})

        where_clause = f" WHERE {' AND '.join(conditions)}"
        query = f"SELECT * FROM c{where_clause} ORDER BY c.incident_date ASC"

        items = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            max_item_count=max_items,
        ):
            items.append(IncidentDocument.from_cosmos(item))
            if len(items) >= max_items:
                break

        return items

    def _filter_memory(
        self,
        *,
        status: str | None = None,
        station: str | None = None,
        exclude_status: str | None = None,
        user_email: str | None = None,
        max_items: int = 50,
    ) -> list[IncidentDocument]:
        """Filter in-memory incidents (dev mode only)."""
        results = []
        for data in self._memory.values():
            if status and data.get("status") != status:
                continue
            if exclude_status and data.get("status") == exclude_status:
                continue
            if station and data.get("station") != station:
                continue
            if user_email:
                is_creator = data.get("created_by") == user_email
                is_crew = any(c.get("email") == user_email for c in data.get("crew", []))
                if not is_creator and not is_crew:
                    continue
            results.append(IncidentDocument.from_cosmos(data))
        results.sort(key=lambda doc: doc.incident_date)
        return results[:max_items]
