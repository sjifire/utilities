"""Distributed turn lock for multi-user chat sessions.

Prevents concurrent Claude API calls for the same incident across
multiple container replicas. Uses the ``conversations`` Cosmos DB
container (which already has TTL enabled) with a well-known document
ID ``"turn-lock"`` per incident partition.

Lock documents auto-expire via Cosmos DB TTL (120 seconds) so stale
locks from crashed replicas are cleaned up automatically.
"""

import logging
from datetime import UTC, datetime
from typing import ClassVar, Self

from sjifire.core.config import get_cosmos_container

logger = logging.getLogger(__name__)

CONTAINER_NAME = "conversations"  # Reuses existing container (TTL enabled)
LOCK_DOC_ID = "turn-lock"
LOCK_TTL_SECONDS = 120  # Auto-expire after 2 minutes


class TurnLock:
    """Represents an active turn lock for an incident."""

    def __init__(  # noqa: D107
        self,
        incident_id: str,
        holder_email: str,
        holder_name: str,
        acquired_at: str,
        etag: str = "",
    ) -> None:
        self.incident_id = incident_id
        self.holder_email = holder_email
        self.holder_name = holder_name
        self.acquired_at = acquired_at
        self.etag = etag

    def is_held_by(self, email: str) -> bool:
        """Check if this lock is held by the given user."""
        return self.holder_email == email


class TurnLockStore:
    """Distributed turn lock using Cosmos DB conditional writes.

    Each incident has at most one lock document (``id="turn-lock"``)
    in the conversations container. The lock is acquired via conditional
    create (409 = already held) and released via conditional delete.

    Falls back to in-memory storage when Cosmos DB is not configured.

    Usage::

        async with TurnLockStore() as store:
            lock = await store.acquire("incident-123", user)
            if lock is None:
                # Lock held by someone else
                existing = await store.get("incident-123")
    """

    _memory: ClassVar[dict[str, dict]] = {}

    def __init__(self) -> None:  # noqa: D107
        self._container = None
        self._in_memory = False

    async def __aenter__(self) -> Self:  # noqa: D105
        self._container = await get_cosmos_container(CONTAINER_NAME)
        if self._container is None:
            self._in_memory = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: D105
        self._container = None

    def _lock_doc(self, incident_id: str, email: str, name: str) -> dict:
        """Build a lock document for Cosmos DB."""
        return {
            "id": LOCK_DOC_ID,
            "incident_id": incident_id,
            "holder_email": email,
            "holder_name": name,
            "acquired_at": datetime.now(UTC).isoformat(),
            "ttl": LOCK_TTL_SECONDS,
        }

    async def acquire(self, incident_id: str, email: str, name: str) -> TurnLock | None:
        """Try to acquire the turn lock for an incident.

        Returns the ``TurnLock`` on success, or ``None`` if the lock
        is already held (even by the same user). This prevents
        concurrent engine tasks for the same incident — the client
        should queue the message and retry after the current turn.
        """
        doc = self._lock_doc(incident_id, email, name)

        if self._in_memory:
            key = f"turn-lock:{incident_id}"
            existing = self._memory.get(key)
            if existing:
                return None
            self._memory[key] = doc
            logger.info("Acquired turn lock for %s by %s (in-memory)", incident_id, email)
            return TurnLock(incident_id, email, name, doc["acquired_at"])

        # Try conditional create — fails with 409 if lock already exists
        try:
            result = await self._container.create_item(body=doc)
            etag = result.get("_etag", "")
            logger.info("Acquired turn lock for %s by %s", incident_id, email)
            return TurnLock(incident_id, email, name, doc["acquired_at"], etag=etag)
        except Exception as exc:
            if _is_conflict(exc):
                return None
            raise

    async def release(self, incident_id: str, email: str) -> bool:
        """Release the turn lock if held by the given user.

        Returns True if the lock was released, False otherwise.
        """
        if self._in_memory:
            key = f"turn-lock:{incident_id}"
            existing = self._memory.get(key)
            if existing and existing["holder_email"] == email:
                del self._memory[key]
                logger.info("Released turn lock for %s by %s (in-memory)", incident_id, email)
                return True
            return False

        # Read current lock to verify ownership
        lock = await self.get(incident_id)
        if lock is None or not lock.is_held_by(email):
            return False

        try:
            await self._container.delete_item(item=LOCK_DOC_ID, partition_key=incident_id)
            logger.info("Released turn lock for %s by %s", incident_id, email)
            return True
        except Exception:
            logger.warning("Failed to release turn lock for %s", incident_id, exc_info=True)
            return False

    async def get(self, incident_id: str) -> TurnLock | None:
        """Get the current turn lock for an incident, if any."""
        if self._in_memory:
            key = f"turn-lock:{incident_id}"
            data = self._memory.get(key)
            if data:
                return TurnLock(
                    incident_id,
                    data["holder_email"],
                    data["holder_name"],
                    data["acquired_at"],
                )
            return None

        try:
            result = await self._container.read_item(
                item=LOCK_DOC_ID,
                partition_key=incident_id,
            )
            return TurnLock(
                incident_id,
                result["holder_email"],
                result["holder_name"],
                result["acquired_at"],
                etag=result.get("_etag", ""),
            )
        except Exception:
            # 404 or other error = no lock
            return None


def _is_conflict(exc: Exception) -> bool:
    """Check if a Cosmos DB exception is a 409 Conflict."""
    # azure.cosmos.exceptions.CosmosResourceExistsError has status_code 409
    status = getattr(exc, "status_code", None)
    if status == 409:
        return True
    # Fallback: check exception class name for in-memory/mock scenarios
    return "Conflict" in type(exc).__name__ or "Exists" in type(exc).__name__
