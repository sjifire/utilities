"""Pydantic models for NERIS data in Cosmos DB.

Includes cached report summaries and pre-update snapshots.
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class NerisReportDocument(BaseModel):
    """Cached summary of a NERIS incident report.

    Partition key is ``year`` (four-digit string derived from incident number).
    """

    id: str  # Normalized incident number (e.g. "26001980")
    year: str  # Partition key (e.g. "2026")
    neris_id: str = ""
    incident_number: str = ""  # Original format (e.g. "26-001980")
    determinant_code: str = ""  # Dispatch ID from legacy system (e.g. "26002059")
    status: str = ""
    incident_type: str = ""
    call_create: str = ""
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> NerisReportDocument:
        """Deserialize from Cosmos DB document."""
        return cls.model_validate(data)

    def to_summary(self) -> dict:
        """Convert to the summary dict format used by the dashboard."""
        return {
            "source": "neris",
            "neris_id": self.neris_id,
            "incident_number": self.incident_number,
            "determinant_code": self.determinant_code,
            "status": self.status,
            "incident_type": self.incident_type,
            "call_create": self.call_create,
        }


class NerisSnapshotDocument(BaseModel):
    """Snapshot of a NERIS record taken before a patch update.

    Written once as a safety net before pushing corrections to NERIS.
    The ``ttl`` field tells Cosmos DB to auto-expire the document after
    30 days, keeping snapshot storage self-cleaning.

    Partition key: ``/year`` (derived from incident datetime).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    year: str = ""  # Partition key — from incident_datetime
    neris_id: str  # Compound NERIS ID (e.g. "FD53055879|26SJ0020|1770457554")
    incident_id: str  # Local incident document ID
    incident_number: str = ""  # CAD number (e.g. "26-002358")
    snapshot: dict  # Full NERIS record at time of snapshot
    patches_applied: dict  # The patch properties dict that was sent
    patched_by: str  # User email
    patched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl: int = 2_592_000  # 30-day TTL (Cosmos auto-deletes)

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB storage."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> NerisSnapshotDocument:
        """Deserialize from Cosmos DB document."""
        return cls.model_validate(data)
