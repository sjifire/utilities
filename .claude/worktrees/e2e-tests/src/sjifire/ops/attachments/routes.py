"""HTTP route handlers for attachment upload/download from the browser UI.

Routes:
- POST /reports/{incident_id}/attachments      → Upload file (multipart form)
- GET  /reports/{incident_id}/attachments       → List attachments (JSON)
- GET  /reports/{incident_id}/attachments/{id}  → Download/redirect to blob
- DELETE /reports/{incident_id}/attachments/{id} → Delete attachment
"""

import base64
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from sjifire.ops.attachments.models import ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE
from sjifire.ops.attachments.store import AttachmentBlobStore
from sjifire.ops.attachments.tools import (
    delete_attachment as _delete_tool,
)
from sjifire.ops.attachments.tools import (
    list_attachments as _list_tool,
)
from sjifire.ops.attachments.tools import (
    upload_attachment as _upload_tool,
)
from sjifire.ops.auth import get_request_user

logger = logging.getLogger(__name__)


async def upload_attachment_route(request: Request) -> Response:
    """Handle multipart file upload from the browser.

    Expects multipart/form-data with:
    - ``file``: The file to upload
    - ``title``: Optional title (form field)
    - ``description``: Optional description (form field)
    - ``for_parsing``: Optional "true" to return image data for LLM
    """
    user = get_request_user(request)
    if user is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    incident_id = request.path_params["incident_id"]

    form = await request.form()
    uploaded = form.get("file")
    if uploaded is None:
        return JSONResponse({"error": "No file provided"}, status_code=400)

    content_type = uploaded.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
        return JSONResponse(
            {"error": f"Content type '{content_type}' not allowed. Allowed: {allowed}"},
            status_code=400,
        )

    data = await uploaded.read()
    if len(data) > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE // (1024 * 1024)
        return JSONResponse(
            {"error": f"File too large. Maximum is {max_mb} MB."},
            status_code=400,
        )

    title = form.get("title", "")
    description = form.get("description", "")
    for_parsing = form.get("for_parsing", "").lower() == "true"

    data_b64 = base64.b64encode(data).decode()

    result = await _upload_tool(
        incident_id=incident_id,
        filename=uploaded.filename or "attachment",
        data_base64=data_b64,
        content_type=content_type,
        title=str(title),
        description=str(description),
        for_parsing=for_parsing,
    )

    status = 400 if "error" in result else 201
    return JSONResponse(result, status_code=status)


async def list_attachments_route(request: Request) -> Response:
    """List all attachments for an incident (JSON)."""
    user = get_request_user(request)
    if user is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    incident_id = request.path_params["incident_id"]
    result = await _list_tool(incident_id)

    status = 400 if "error" in result else 200
    return JSONResponse(result, status_code=status)


async def download_attachment_route(request: Request) -> Response:
    """Download an attachment blob directly."""
    user = get_request_user(request)
    if user is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    incident_id = request.path_params["incident_id"]
    attachment_id = request.path_params["attachment_id"]

    # Get metadata to find blob path and verify access
    from sjifire.ops.incidents.store import IncidentStore

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return JSONResponse({"error": "Incident not found"}, status_code=404)

    if not (user.is_editor or doc.created_by == user.email or user.email in doc.personnel_emails()):
        return JSONResponse({"error": "Access denied"}, status_code=403)

    meta = next((a for a in doc.attachments if a.id == attachment_id), None)
    if meta is None:
        return JSONResponse({"error": "Attachment not found"}, status_code=404)

    try:
        async with AttachmentBlobStore() as blob_store:
            data, ct = await blob_store.download(meta.blob_path)
    except FileNotFoundError:
        return JSONResponse({"error": "Blob not found"}, status_code=404)

    return Response(
        content=data,
        media_type=ct,
        headers={
            "Content-Disposition": f'inline; filename="{meta.filename}"',
        },
    )


async def delete_attachment_route(request: Request) -> Response:
    """Delete an attachment via HTTP."""
    user = get_request_user(request)
    if user is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    incident_id = request.path_params["incident_id"]
    attachment_id = request.path_params["attachment_id"]

    result = await _delete_tool(incident_id, attachment_id)

    status = 400 if "error" in result else 200
    return JSONResponse(result, status_code=status)
