"""Async Cosmos DB operations for chat conversations and usage budgets.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.
"""

import logging
import os
from typing import ClassVar, Self

from dotenv import load_dotenv

from sjifire.core.config import get_cosmos_database
from sjifire.mcp.chat.models import ConversationDocument, UserBudget

logger = logging.getLogger(__name__)

CONVERSATIONS_CONTAINER = "conversations"
BUDGETS_CONTAINER = "budgets"


class ConversationStore:
    """Async CRUD for chat conversation documents in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with ConversationStore() as store:
            conv = await store.create(doc)
    """

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
            logger.warning("No COSMOS_ENDPOINT set — using in-memory conversation store (dev only)")
            self._in_memory = True
            return self

        database = self._client.get_database_client(get_cosmos_database())
        self._container = database.get_container_client(CONVERSATIONS_CONTAINER)
        logger.info("Connected to Cosmos DB: %s/%s", get_cosmos_database(), CONVERSATIONS_CONTAINER)
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

    async def create(self, doc: ConversationDocument) -> ConversationDocument:
        """Create a new conversation document."""
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.info("Created conversation %s (in-memory)", doc.id)
            return doc

        result = await self._container.create_item(body=doc.to_cosmos())
        logger.info("Created conversation %s", doc.id)
        return ConversationDocument.from_cosmos(result)

    async def get(self, conversation_id: str, incident_id: str) -> ConversationDocument | None:
        """Get a conversation by ID and incident_id (partition key)."""
        if self._in_memory:
            data = self._memory.get(conversation_id)
            if data and data.get("incident_id") == incident_id:
                return ConversationDocument.from_cosmos(data)
            return None

        try:
            result = await self._container.read_item(
                item=conversation_id,
                partition_key=incident_id,
            )
            return ConversationDocument.from_cosmos(result)
        except Exception:
            logger.debug("Conversation not found: %s", conversation_id)
            return None

    async def get_by_incident(self, incident_id: str) -> ConversationDocument | None:
        """Get the conversation for an incident (at most one per incident)."""
        if self._in_memory:
            for data in self._memory.values():
                if data.get("incident_id") == incident_id:
                    return ConversationDocument.from_cosmos(data)
            return None

        query = "SELECT * FROM c WHERE c.incident_id = @iid"
        parameters: list[dict] = [{"name": "@iid", "value": incident_id}]

        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            partition_key=incident_id,
            max_item_count=1,
        ):
            return ConversationDocument.from_cosmos(item)

        return None

    async def update(self, doc: ConversationDocument) -> ConversationDocument:
        """Update an existing conversation document."""
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.info("Updated conversation %s (in-memory)", doc.id)
            return doc

        result = await self._container.replace_item(
            item=doc.id,
            body=doc.to_cosmos(),
        )
        logger.info("Updated conversation %s", doc.id)
        return ConversationDocument.from_cosmos(result)

    async def delete_by_incident(self, incident_id: str) -> bool:
        """Delete the conversation for an incident. Returns True if deleted."""
        doc = await self.get_by_incident(incident_id)
        if doc is None:
            return False

        if self._in_memory:
            self._memory.pop(doc.id, None)
            logger.info("Deleted conversation %s (in-memory)", doc.id)
            return True

        await self._container.delete_item(item=doc.id, partition_key=incident_id)
        logger.info("Deleted conversation %s for incident %s", doc.id, incident_id)
        return True


class BudgetStore:
    """Async CRUD for user budget documents in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with BudgetStore() as store:
            budget = await store.get_or_create("user@sjifire.org", "2026-02")
    """

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
            logger.warning("No COSMOS_ENDPOINT set — using in-memory budget store (dev only)")
            self._in_memory = True
            return self

        database = self._client.get_database_client(get_cosmos_database())
        self._container = database.get_container_client(BUDGETS_CONTAINER)
        logger.info("Connected to Cosmos DB: %s/%s", get_cosmos_database(), BUDGETS_CONTAINER)
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

    async def get_or_create(self, user_email: str, month: str) -> UserBudget:
        """Get the budget for a user+month, creating if it doesn't exist."""
        doc_id = f"{user_email}:{month}"

        if self._in_memory:
            data = self._memory.get(doc_id)
            if data:
                return UserBudget.from_cosmos(data)
            doc = UserBudget(id=doc_id, month=month, user_email=user_email)
            self._memory[doc_id] = doc.to_cosmos()
            return doc

        try:
            result = await self._container.read_item(
                item=doc_id,
                partition_key=month,
            )
            return UserBudget.from_cosmos(result)
        except Exception:
            doc = UserBudget(id=doc_id, month=month, user_email=user_email)
            result = await self._container.create_item(body=doc.to_cosmos())
            logger.info("Created budget for %s month %s", user_email, month)
            return UserBudget.from_cosmos(result)

    async def update(self, doc: UserBudget) -> UserBudget:
        """Update an existing budget document."""
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            return doc

        result = await self._container.replace_item(
            item=doc.id,
            body=doc.to_cosmos(),
        )
        return UserBudget.from_cosmos(result)
