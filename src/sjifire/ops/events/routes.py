"""HTTP route handlers for event records (Events tab on the dashboard).

Routes:
- GET  /events                                        → Redirect to dashboard#events
- GET  /events/data                                   → JSON data
- GET  /events/records/{record_id}                    → Record detail
- POST /events/records                                → Create record
- PATCH /events/records/{record_id}                   → Update record
- POST /events/records/{record_id}/upload             → Upload file
- POST /events/records/{record_id}/parse              → Parse attendees
- DELETE /events/records/{record_id}/attachments/{id} → Delete attachment
- GET  /events/records/{record_id}/attachments/{id}   → Download attachment
"""

import logging
import os
from datetime import date, datetime, timedelta

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from sjifire.ops.attachments.models import validate_file_upload
from sjifire.ops.attachments.store import AttachmentBlobStore
from sjifire.ops.auth import (
    UserContext,
    check_group_membership,
    require_auth,
)
from sjifire.ops.events.models import (
    AttendeeRecord,
    EventAttachmentMeta,
    EventRecord,
    build_event_blob_path,
)
from sjifire.ops.events.store import EventStore

logger = logging.getLogger(__name__)

# Event managers group — falls back to editor group
_EVENT_MANAGERS_GROUP_ID: str | None = None


def _get_event_managers_group_id() -> str:
    global _EVENT_MANAGERS_GROUP_ID
    if _EVENT_MANAGERS_GROUP_ID is None:
        _EVENT_MANAGERS_GROUP_ID = (
            os.getenv("ENTRA_EVENT_MANAGERS_GROUP_ID")
            or os.getenv("ENTRA_TRAINING_MANAGERS_GROUP_ID")
            or os.getenv("ENTRA_REPORT_EDITORS_GROUP_ID", "")
        )
    return _EVENT_MANAGERS_GROUP_ID


async def _is_manager(user: UserContext) -> bool:
    group_id = _get_event_managers_group_id()
    if not group_id:
        return False
    return await check_group_membership(user.user_id, group_id, fallback=user.is_editor)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


async def events_page(request: Request) -> Response:
    """Redirect to the dashboard events tab."""
    from starlette.responses import RedirectResponse

    return RedirectResponse("/dashboard#events", status_code=302)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


async def events_data(request: Request) -> Response:
    """Return combined calendar events + event records as JSON."""
    user = await require_auth(request)
    if isinstance(user, Response):
        return user

    is_mgr = await _is_manager(user)
    today = date.today()
    range_start = today - timedelta(days=180)
    range_end = today + timedelta(days=90)

    # Fetch calendar events and records concurrently
    import asyncio

    from sjifire.ops.events.calendar import fetch_events

    cal_task = asyncio.create_task(fetch_events(range_start, range_end))

    async with EventStore() as store:
        records = await store.list_recent(max_items=300)

    cal_events = await cal_task
    logger.info(
        "Events data: %d calendar events, %d records for user %s",
        len(cal_events),
        len(records),
        user.email,
    )

    # Index records by calendar_event_id for enrichment
    record_by_event: dict[str, EventRecord] = {}
    manual_records: list[EventRecord] = []
    for rec in records:
        if rec.calendar_event_id:
            record_by_event[rec.calendar_event_id] = rec
        else:
            manual_records.append(rec)

    # Build combined event list
    combined: list[dict] = []
    seen_event_ids: set[str] = set()

    for ev in cal_events:
        eid = ev["event_id"]
        seen_event_ids.add(eid)
        rec = record_by_event.get(eid)
        entry = {
            "event_id": eid,
            "subject": ev["subject"],
            "start": ev["start"],
            "end": ev["end"],
            "is_all_day": ev["is_all_day"],
            "location": ev["location"],
            "location_address": ev.get("location_address", ""),
            "body_preview": ev["body_preview"],
            "calendar_source": ev["calendar_source"],
            "has_record": rec is not None,
            "record_id": rec.id if rec else None,
            "attendee_count": len(rec.attendees) if rec else 0,
            "attachment_count": len(rec.attachments) if rec else 0,
            "notes": rec.notes if rec else "",
        }
        combined.append(entry)

    # Add manual records (no calendar event)
    for rec in manual_records:
        dt = rec.event_date
        if range_start <= dt.date() <= range_end:
            combined.append(
                {
                    "event_id": None,
                    "subject": rec.subject,
                    "start": dt.isoformat(),
                    "end": rec.end_date.isoformat() if rec.end_date else "",
                    "is_all_day": False,
                    "location": rec.location,
                    "location_address": "",
                    "body_preview": rec.description[:500] if rec.description else "",
                    "calendar_source": "Manual",
                    "has_record": True,
                    "record_id": rec.id,
                    "attendee_count": len(rec.attendees),
                    "attachment_count": len(rec.attachments),
                    "notes": rec.notes,
                }
            )

    # Sort by start date
    combined.sort(key=lambda e: e.get("start", ""))

    # Split into upcoming vs past
    today_str = today.isoformat()
    upcoming = [e for e in combined if (e.get("start") or "") >= today_str]
    past = [e for e in combined if (e.get("start") or "") < today_str]
    past.reverse()  # Most recent first for past events

    # Calendar source labels
    from sjifire.ops.events.calendar import _get_calendar_sources

    calendars = _get_calendar_sources()

    return JSONResponse(
        {
            "upcoming": upcoming,
            "past": past,
            "calendars": calendars,
            "is_manager": is_mgr,
            "user_name": user.name,
            "user_email": user.email,
        }
    )


# ---------------------------------------------------------------------------
# Records CRUD
# ---------------------------------------------------------------------------


async def get_record(request: Request) -> Response:
    """Get full event record detail."""
    user = await require_auth(request)
    if isinstance(user, Response):
        return user

    record_id = request.path_params["record_id"]
    async with EventStore() as store:
        rec = await store.get_by_id(record_id)

    if rec is None:
        return JSONResponse({"error": "Record not found"}, status_code=404)

    return JSONResponse(rec.to_cosmos())


async def create_record(request: Request) -> Response:
    """Create an event record (calendar-linked or manual)."""
    user = await require_auth(request, group_id=_get_event_managers_group_id())
    if isinstance(user, Response):
        return user

    body = await request.json()
    calendar_event_id = body.get("calendar_event_id", "")
    calendar_source = body.get("calendar_source", "")
    subject = body.get("subject", "")
    start = body.get("start", "")
    end = body.get("end", "")
    location = body.get("location", "")
    description = body.get("description", "")

    if not subject or not start:
        return JSONResponse({"error": "subject and start are required"}, status_code=400)

    # If calendar-linked, check for existing record
    if calendar_event_id:
        async with EventStore() as store:
            existing = await store.get_by_calendar_event_id(calendar_event_id)
        if existing:
            return JSONResponse(existing.to_cosmos(), status_code=200)

    event_date = datetime.fromisoformat(start)
    end_date = datetime.fromisoformat(end) if end else None

    rec = EventRecord(
        calendar_event_id=calendar_event_id,
        calendar_source=calendar_source,
        subject=subject,
        event_date=event_date,
        end_date=end_date,
        location=location,
        description=description,
        created_by=user.email,
    )

    async with EventStore() as store:
        rec = await store.upsert(rec)

    return JSONResponse(rec.to_cosmos(), status_code=201)


async def update_record(request: Request) -> Response:
    """Update an event record (attendees, notes, etc.)."""
    user = await require_auth(request, group_id=_get_event_managers_group_id())
    if isinstance(user, Response):
        return user

    record_id = request.path_params["record_id"]
    body = await request.json()

    async with EventStore() as store:
        rec = await store.get_by_id(record_id)
        if rec is None:
            return JSONResponse({"error": "Record not found"}, status_code=404)

        if "attendees" in body:
            rec.attendees = [AttendeeRecord(**a) for a in body["attendees"]]
        if "notes" in body:
            rec.notes = body["notes"]
        if "subject" in body:
            rec.subject = body["subject"]
        if "location" in body:
            rec.location = body["location"]
        if "description" in body:
            rec.description = body["description"]
        if "event_date" in body:
            rec.event_date = datetime.fromisoformat(body["event_date"])
        if "end_date" in body:
            val = body["end_date"]
            rec.end_date = datetime.fromisoformat(val) if val else None

        rec = await store.upsert(rec)

    return JSONResponse(rec.to_cosmos())


# ---------------------------------------------------------------------------
# File uploads
# ---------------------------------------------------------------------------


async def upload_file(request: Request) -> Response:
    """Upload a file attachment to an event record."""
    user = await require_auth(request, group_id=_get_event_managers_group_id())
    if isinstance(user, Response):
        return user

    record_id = request.path_params["record_id"]

    form = await request.form()
    uploaded = form.get("file")
    if uploaded is None:
        return JSONResponse({"error": "No file provided"}, status_code=400)

    content_type = uploaded.content_type or "application/octet-stream"
    data = await uploaded.read()
    error = validate_file_upload(content_type, len(data))
    if error:
        return JSONResponse({"error": error}, status_code=400)

    async with EventStore() as store:
        rec = await store.get_by_id(record_id)
        if rec is None:
            return JSONResponse({"error": "Record not found"}, status_code=404)

        attachment = EventAttachmentMeta(
            filename=uploaded.filename or "attachment",
            content_type=content_type,
            size_bytes=len(data),
            uploaded_by=user.email,
        )
        attachment.blob_path = build_event_blob_path(
            rec.year, rec.id, attachment.id, attachment.filename
        )

        # Upload to blob storage
        async with AttachmentBlobStore() as blob_store:
            await blob_store.upload(attachment.blob_path, data, content_type)

        rec.attachments.append(attachment)
        rec = await store.upsert(rec)

    return JSONResponse(
        {
            "attachment": attachment.model_dump(mode="json"),
            "attachment_count": len(rec.attachments),
        },
        status_code=201,
    )


async def parse_attendees(request: Request) -> Response:
    """Parse attendees from an attachment or raw text."""
    user = await require_auth(request, group_id=_get_event_managers_group_id())
    if isinstance(user, Response):
        return user

    record_id = request.path_params["record_id"]
    body = await request.json()

    attachment_id = body.get("attachment_id")
    raw_text = body.get("text")

    if not attachment_id and not raw_text:
        return JSONResponse({"error": "Provide either attachment_id or text"}, status_code=400)

    from sjifire.ops.events.parser import (
        parse_attendees_from_image,
        parse_attendees_from_pdf,
        parse_attendees_from_text,
    )

    if raw_text:
        attendees = await parse_attendees_from_text(raw_text)
        return JSONResponse({"attendees": attendees})

    # Parse from attachment
    async with EventStore() as store:
        rec = await store.get_by_id(record_id)
    if rec is None:
        return JSONResponse({"error": "Record not found"}, status_code=404)

    meta = next((a for a in rec.attachments if a.id == attachment_id), None)
    if meta is None:
        return JSONResponse({"error": "Attachment not found"}, status_code=404)

    try:
        async with AttachmentBlobStore() as blob_store:
            data, ct = await blob_store.download(meta.blob_path)
    except FileNotFoundError:
        return JSONResponse({"error": "Blob not found"}, status_code=404)

    if ct == "application/pdf":
        attendees = await parse_attendees_from_pdf(data)
    elif ct.startswith("image/"):
        attendees = await parse_attendees_from_image(data, ct)
    else:
        # Try as text
        text = data.decode("utf-8", errors="replace")
        attendees = await parse_attendees_from_text(text)

    return JSONResponse({"attendees": attendees})


# ---------------------------------------------------------------------------
# Attachment download / delete
# ---------------------------------------------------------------------------


async def download_attachment(request: Request) -> Response:
    """Download an event attachment."""
    user = await require_auth(request)
    if isinstance(user, Response):
        return user

    record_id = request.path_params["record_id"]
    att_id = request.path_params["att_id"]

    async with EventStore() as store:
        rec = await store.get_by_id(record_id)
    if rec is None:
        return JSONResponse({"error": "Record not found"}, status_code=404)

    meta = next((a for a in rec.attachments if a.id == att_id), None)
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
        headers={"Content-Disposition": f'inline; filename="{meta.filename}"'},
    )


async def delete_attachment(request: Request) -> Response:
    """Delete an event attachment."""
    user = await require_auth(request, group_id=_get_event_managers_group_id())
    if isinstance(user, Response):
        return user

    record_id = request.path_params["record_id"]
    att_id = request.path_params["att_id"]

    async with EventStore() as store:
        rec = await store.get_by_id(record_id)
        if rec is None:
            return JSONResponse({"error": "Record not found"}, status_code=404)

        meta = next((a for a in rec.attachments if a.id == att_id), None)
        if meta is None:
            return JSONResponse({"error": "Attachment not found"}, status_code=404)

        # Delete blob
        async with AttachmentBlobStore() as blob_store:
            await blob_store.delete(meta.blob_path)

        rec.attachments = [a for a in rec.attachments if a.id != att_id]
        rec = await store.upsert(rec)

    return JSONResponse({"ok": True, "attachment_count": len(rec.attachments)})
