"""Pydantic models for event records stored in Cosmos DB."""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class AttendeeRecord(BaseModel):
    """A person who attended an event."""

    name: str = Field(max_length=200)
    email: str | None = Field(default=None, max_length=254)
    source: str = Field(default="manual", max_length=20)  # "manual" or "parsed"

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str | None) -> str | None:
        return v.lower() if v else v


class EventAttachmentMeta(BaseModel):
    """Metadata for a file attached to an event record.

    Blobs live in Azure Blob Storage at
    ``events/{year}/{record_id}/{attachment_id}-{filename}``.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str = Field(max_length=255)
    content_type: str = Field(max_length=100)
    size_bytes: int = 0
    blob_path: str = Field(default="", max_length=500)
    uploaded_by: str = Field(max_length=254)
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("uploaded_by", mode="before")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.lower() if v else v


MAX_ATTENDEES = 200
MAX_ATTACHMENTS = 50


class EventRecord(BaseModel):
    """Event record stored in Cosmos DB.

    Links to a calendar event via ``calendar_event_id`` (optional —
    manual events have no calendar link). Partition key is ``year``,
    derived from ``event_date``.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    year: str = ""  # Partition key — set by validator from event_date

    # Calendar link (empty for manual events)
    calendar_event_id: str = ""
    calendar_source: str = ""  # "Training", "Pub-Ed", or "" for manual

    # Event info
    subject: str = Field(max_length=500)
    event_date: datetime
    end_date: datetime | None = None
    location: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=10_000)

    # Attendance
    attendees: list[AttendeeRecord] = Field(default_factory=list, max_length=MAX_ATTENDEES)

    # Attachments — metadata only; blobs in Azure Blob Storage
    attachments: list[EventAttachmentMeta] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    # Notes
    notes: str = Field(default="", max_length=10_000)

    # Tracking
    created_by: str = Field(max_length=254)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _set_year(self) -> EventRecord:
        """Derive year partition key from event_date."""
        self.year = str(self.event_date.year)
        return self

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB storage."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> EventRecord:
        """Deserialize from Cosmos DB document."""
        # Support legacy documents that used training_date
        if "training_date" in data and "event_date" not in data:
            data["event_date"] = data.pop("training_date")
        return cls.model_validate(data)


def build_event_blob_path(year: str, record_id: str, attachment_id: str, filename: str) -> str:
    """Build blob storage path for an event attachment.

    Layout: ``events/{year}/{record_id}/{attachment_id}-{filename}``
    """
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"events/{year}/{record_id}/{attachment_id}-{safe_name}"
