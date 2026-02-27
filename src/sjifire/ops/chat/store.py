"""Async Cosmos DB operations for chat conversations and usage budgets.

When ``COSMOS_ENDPOINT`` is not set, falls back to an in-memory store
for local development and testing with ``mcp dev``.
"""

import logging
from typing import ClassVar

from sjifire.ops.chat.models import ConversationDocument, UserBudget
from sjifire.ops.cosmos import CosmosStore

logger = logging.getLogger(__name__)


class ConversationStore(CosmosStore):
    """Async CRUD for chat conversation documents in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with ConversationStore() as store:
            conv = await store.create(doc)
    """

    _container_name: ClassVar[str] = "conversations"
    _memory: ClassVar[dict[str, dict]] = {}

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
        """Get the conversation for an incident (at most one per incident).

        When multiple conversations exist (e.g. from a race condition),
        returns the most recently updated one so recovery polling finds
        the conversation that the engine actually completed.
        """
        if self._in_memory:
            candidates = [
                data for data in self._memory.values() if data.get("incident_id") == incident_id
            ]
            if not candidates:
                return None
            # Return the most recently updated conversation
            candidates.sort(
                key=lambda d: d.get("updated_at") or d.get("created_at", ""),
                reverse=True,
            )
            return ConversationDocument.from_cosmos(candidates[0])

        # Exclude turn-lock documents which share this container/partition.
        # ORDER BY _ts DESC ensures we get the most recently updated
        # conversation when duplicates exist from race conditions.
        return await self._query_one(
            "SELECT * FROM c WHERE c.incident_id = @iid AND c.id != 'turn-lock' ORDER BY c._ts DESC",
            [{"name": "@iid", "value": incident_id}],
            ConversationDocument,
            partition_key=incident_id,
        )

    async def update(self, doc: ConversationDocument) -> ConversationDocument:
        """Update (or re-create) a conversation document.

        Uses ``upsert_item`` so that a mid-turn reset (which deletes the
        document) doesn't cause the engine's final save to fail.
        """
        if self._in_memory:
            self._memory[doc.id] = doc.to_cosmos()
            logger.info("Updated conversation %s (in-memory)", doc.id)
            return doc

        result = await self._container.upsert_item(body=doc.to_cosmos())
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


class BudgetStore(CosmosStore):
    """Async CRUD for user budget documents in Cosmos DB.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with BudgetStore() as store:
            budget = await store.get_or_create("user@sjifire.org", "2026-02")
    """

    _container_name: ClassVar[str] = "budgets"
    _memory: ClassVar[dict[str, dict]] = {}

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
