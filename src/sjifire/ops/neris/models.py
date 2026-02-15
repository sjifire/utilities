"""Pydantic models for cached NERIS report summaries in Cosmos DB."""

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
    determinant_code: str = ""  # Dispatch ID from ESO (e.g. "26002059")
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
