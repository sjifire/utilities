"""Pydantic models for incident attachments stored in Azure Blob Storage.

Attachment metadata is embedded in the IncidentDocument (Cosmos DB).
The actual file bytes live in Azure Blob Storage under
``incidents/{year}/{incident_id}/{attachment_id}-{filename}``.
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from sjifire.core.normalize import LowerEmailStr

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


def validate_file_upload(content_type: str, size: int) -> str | None:
    """Validate file content type and size.

    Args:
        content_type: MIME type of the uploaded file
        size: File size in bytes

    Returns:
        Error message string if validation fails, None if OK
    """
    if content_type not in ALLOWED_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
        return f"Content type '{content_type}' not allowed. Allowed: {allowed}"
    if size > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE // (1024 * 1024)
        return f"File too large ({size} bytes). Maximum is {max_mb} MB."
    return None


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
    uploaded_by: LowerEmailStr = Field(max_length=254)  # email
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def build_blob_path(year: str, incident_id: str, attachment_id: str, filename: str) -> str:
    """Build the blob storage path for an attachment.

    Layout: ``incidents/{year}/{incident_id}/{attachment_id}-{filename}``
    """
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"incidents/{year}/{incident_id}/{attachment_id}-{safe_name}"
