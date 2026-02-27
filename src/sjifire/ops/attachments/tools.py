"""MCP tools for incident report attachments.

Provides upload, list, get, update, and delete operations for files
attached to incident reports. Files are stored in Azure Blob Storage;
metadata is embedded in the IncidentDocument in Cosmos DB.

Two usage modes:
1. **Parse mode** — upload an image so the LLM can extract data from it
   (returns base64 for vision). Set ``for_parsing=True``.
2. **Attach mode** — save a file with title/description as a permanent
   attachment on the report. This is the default.
"""

import base64
import logging
from datetime import UTC, datetime

from sjifire.ops.attachments.models import (
    ALLOWED_CONTENT_TYPES,
    MAX_ATTACHMENTS,
    MAX_FILE_SIZE,
    AttachmentMeta,
    build_blob_path,
)
from sjifire.ops.attachments.store import AttachmentBlobStore
from sjifire.ops.auth import check_doc_edit_access, check_doc_view_access, get_current_user
from sjifire.ops.incidents.models import EditEntry
from sjifire.ops.incidents.store import IncidentStore

logger = logging.getLogger(__name__)


async def _check_edit_access(doc, user_email: str, is_editor: bool) -> bool:
    """Check if user can edit (attach to) this incident."""
    return await check_doc_edit_access(doc.created_by, user_email, is_editor)


async def upload_attachment(
    incident_id: str,
    filename: str,
    data_base64: str,
    *,
    content_type: str = "image/jpeg",
    title: str = "",
    description: str = "",
    for_parsing: bool = False,
) -> dict:
    """Upload a file and attach it to an incident report.

    Stores the file in Azure Blob Storage and records metadata on the
    incident document. Supports images (JPEG, PNG, WebP, GIF, TIFF)
    and PDFs up to 20 MB.

    Args:
        incident_id: The incident document ID
        filename: Original filename (e.g. "scene-photo.jpg")
        data_base64: File contents as a base64-encoded string
        content_type: MIME type (default "image/jpeg"). Allowed types:
            image/jpeg, image/png, image/webp, image/gif, image/tiff,
            application/pdf
        title: Short title for the attachment (optional, max 200 chars)
        description: Longer description (optional, max 2000 chars)
        for_parsing: If true, also returns the base64 data in the
            response so the LLM can parse/analyze the image content.
            The file is still saved to blob storage either way.

    Returns:
        Attachment metadata including ID, blob path, and (if
        for_parsing) the base64 image data for vision analysis
    """
    user = get_current_user()

    if content_type not in ALLOWED_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
        return {"error": f"Content type '{content_type}' not allowed. Allowed: {allowed}"}

    try:
        data = base64.b64decode(data_base64)
    except Exception:
        return {"error": "Invalid base64 data"}

    if len(data) > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE // (1024 * 1024)
        return {"error": f"File too large ({len(data)} bytes). Maximum is {max_mb} MB."}

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)
        if doc is None:
            return {"error": "Incident not found"}

        if not await _check_edit_access(doc, user.email, user.is_editor):
            return {"error": "You don't have permission to add attachments to this incident"}

        if doc.status == "submitted":
            return {"error": "Cannot add attachments to a submitted incident"}

        if len(doc.attachments) >= MAX_ATTACHMENTS:
            return {"error": f"Maximum of {MAX_ATTACHMENTS} attachments reached"}

        # Build metadata and blob path
        meta = AttachmentMeta(
            filename=filename,
            title=title,
            description=description,
            content_type=content_type,
            size_bytes=len(data),
            uploaded_by=user.email,
        )
        meta.blob_path = build_blob_path(doc.year, incident_id, meta.id, filename)

        # Upload to blob storage
        async with AttachmentBlobStore() as blob_store:
            await blob_store.upload(meta.blob_path, data, content_type)

        # Save metadata on the incident
        doc.attachments.append(meta)
        doc.updated_at = datetime.now(UTC)
        doc.edit_history.append(
            EditEntry(
                editor_email=user.email,
                editor_name=user.name,
                fields_changed=["attachments"],
            )
        )
        await store.update(doc)

    logger.info(
        "User %s uploaded attachment %s to incident %s (%d bytes)",
        user.email,
        meta.id,
        incident_id,
        len(data),
    )

    result = meta.model_dump(mode="json")
    result["attachment_count"] = len(doc.attachments)

    if for_parsing and content_type.startswith("image/"):
        result["image_data"] = {
            "base64": data_base64,
            "media_type": content_type,
        }

    return result


async def list_attachments(incident_id: str) -> dict:
    """List all attachments on an incident report.

    Returns metadata for each attachment (ID, filename, title,
    description, content type, size, uploader). Does not include
    file contents — use get_attachment to download.

    Args:
        incident_id: The incident document ID

    Returns:
        List of attachment metadata objects
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return {"error": "Incident not found"}

    # View access: editor, creator, or personnel
    can_view = await check_doc_view_access(
        doc.created_by,
        doc.personnel_emails(),
        user.email,
        user.is_editor,
    )
    if not can_view:
        return {"error": "You don't have access to this incident"}

    return {
        "attachments": [a.model_dump(mode="json") for a in doc.attachments],
        "count": len(doc.attachments),
    }


async def get_attachment(
    incident_id: str,
    attachment_id: str,
    *,
    include_data: bool = False,
) -> dict:
    """Get metadata for a single attachment, optionally with download URL.

    Args:
        incident_id: The incident document ID
        attachment_id: The attachment ID
        include_data: If true and the attachment is an image, include
            base64 data for LLM vision analysis

    Returns:
        Attachment metadata with download_url, and optionally image_data
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return {"error": "Incident not found"}

    can_view = await check_doc_view_access(
        doc.created_by,
        doc.personnel_emails(),
        user.email,
        user.is_editor,
    )
    if not can_view:
        return {"error": "You don't have access to this incident"}

    meta = next((a for a in doc.attachments if a.id == attachment_id), None)
    if meta is None:
        return {"error": f"Attachment '{attachment_id}' not found on this incident"}

    result = meta.model_dump(mode="json")

    async with AttachmentBlobStore() as blob_store:
        result["download_url"] = await blob_store.generate_download_url(meta.blob_path)

        if include_data and meta.content_type.startswith("image/"):
            data, _ = await blob_store.download(meta.blob_path)
            result["image_data"] = {
                "base64": base64.b64encode(data).decode(),
                "media_type": meta.content_type,
            }

    return result


async def update_attachment(
    incident_id: str,
    attachment_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict:
    """Update title and/or description on an attachment.

    Args:
        incident_id: The incident document ID
        attachment_id: The attachment ID
        title: New title (e.g., "E31 accountability board")
        description: New description

    Returns:
        Updated attachment metadata
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)
        if doc is None:
            return {"error": "Incident not found"}

        if not await _check_edit_access(doc, user.email, user.is_editor):
            return {"error": "You don't have permission to update attachments"}

        meta = next((a for a in doc.attachments if a.id == attachment_id), None)
        if meta is None:
            return {"error": f"Attachment '{attachment_id}' not found on this incident"}

        if title is not None:
            meta.title = title
        if description is not None:
            meta.description = description

        doc.updated_at = datetime.now(UTC)
        await store.update(doc)

    return meta.model_dump(mode="json")


async def delete_attachment(incident_id: str, attachment_id: str) -> dict:
    """Delete an attachment from an incident report.

    Removes both the blob from storage and the metadata from the
    incident document.

    Args:
        incident_id: The incident document ID
        attachment_id: The attachment ID to delete

    Returns:
        Confirmation with updated attachment count
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)
        if doc is None:
            return {"error": "Incident not found"}

        if not await _check_edit_access(doc, user.email, user.is_editor):
            return {"error": "You don't have permission to delete attachments"}

        if doc.status == "submitted":
            return {"error": "Cannot delete attachments from a submitted incident"}

        meta = next((a for a in doc.attachments if a.id == attachment_id), None)
        if meta is None:
            return {"error": f"Attachment '{attachment_id}' not found on this incident"}

        # Delete from blob storage
        async with AttachmentBlobStore() as blob_store:
            await blob_store.delete(meta.blob_path)

        # Remove from incident metadata
        doc.attachments = [a for a in doc.attachments if a.id != attachment_id]
        doc.updated_at = datetime.now(UTC)
        doc.edit_history.append(
            EditEntry(
                editor_email=user.email,
                editor_name=user.name,
                fields_changed=["attachments"],
            )
        )
        await store.update(doc)

    logger.info(
        "User %s deleted attachment %s from incident %s",
        user.email,
        attachment_id,
        incident_id,
    )

    return {
        "deleted": attachment_id,
        "filename": meta.filename,
        "attachment_count": len(doc.attachments),
    }
