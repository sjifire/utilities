"""Pydantic models for incident attachments stored in Azure Blob Storage.

Attachment metadata is embedded in the IncidentDocument (Cosmos DB).
The actual file bytes live in Azure Blob Storage under
``incidents/{year}/{incident_id}/{attachment_id}-{filename}``.
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

MAX_ATTACHMENTS = 50
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2000
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/tiff",
        "application/pdf",
    }
)
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


class AttachmentMeta(BaseModel):
    """Metadata for a single attachment on an incident report.

    Stored as an element in ``IncidentDocument.attachments``.
    The blob itself lives in Azure Blob Storage; this model holds
    only the reference and descriptive fields.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str = Field(max_length=255)
    title: str = Field(default="", max_length=MAX_TITLE_LENGTH)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    content_type: str = Field(max_length=100)
    size_bytes: int = 0
    blob_path: str = Field(default="", max_length=500)
    uploaded_by: str = Field(max_length=254)  # email
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("uploaded_by", mode="before")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        """Lowercase email for consistent matching."""
        return v.lower() if v else v


def build_blob_path(year: str, incident_id: str, attachment_id: str, filename: str) -> str:
    """Build the blob storage path for an attachment.

    Layout: ``incidents/{year}/{incident_id}/{attachment_id}-{filename}``
    """
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"incidents/{year}/{incident_id}/{attachment_id}-{safe_name}"
