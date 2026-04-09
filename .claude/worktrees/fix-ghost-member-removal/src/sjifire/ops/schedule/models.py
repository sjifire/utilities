"""Pydantic models for cached schedule data in Cosmos DB."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class ScheduleEntryCache(BaseModel):
    """A single crew member's schedule entry."""

    name: str
    position: str
    section: str
    start_time: str  # HH:MM
    end_time: str  # HH:MM
    platoon: str = ""


class DayScheduleCache(BaseModel):
    """Cached schedule for a single day, stored in Cosmos DB.

    Partition key is ``date`` (YYYY-MM-DD string).
    """

    id: str  # Same as date string (YYYY-MM-DD)
    date: str  # YYYY-MM-DD, also the partition key
    platoon: str = ""
    entries: list[ScheduleEntryCache] = []
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def is_stale(self, max_age_hours: float = 24.0) -> bool:
        """Check if this cached data is older than max_age_hours."""
        age = datetime.now(UTC) - self.fetched_at
        return age.total_seconds() > max_age_hours * 3600

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> DayScheduleCache:
        """Deserialize from Cosmos DB document."""
        return cls.model_validate(data)
