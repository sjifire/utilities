"""HTTP route handlers for the chat-based incident reporting UI.

Routes:
- GET  /reports                            → Reports list page (HTML)
- POST /reports/new                        → Create new report (redirect)
- GET  /reports/{incident_id}              → Chat page (HTML)
- GET  /reports/{incident_id}/conversation → Conversation history (JSON)
- POST /reports/{incident_id}/chat         → Streaming chat (SSE)
"""

import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from sjifire.core.config import local_now
from sjifire.ops.auth import UserContext, get_easyauth_user, set_current_user
from sjifire.ops.chat.engine import stream_chat, stream_general_chat
from sjifire.ops.chat.store import ConversationStore
from sjifire.ops.dashboard import get_dashboard_data
from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.incidents.store import IncidentStore

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)


def _get_user(request: Request) -> UserContext | None:
    """Extract authenticated user from request."""
    user = get_easyauth_user(request)
    if user:
        set_current_user(user)
    return user


async def reports_list(request: Request) -> Response:
    """Serve the reports list page — dispatch calls with report status."""
    user = _get_user(request)

    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return RedirectResponse("/.auth/login/aad?post_login_redirect_uri=/reports")

    # In dev mode, user may be set by middleware
    if not user:
        from sjifire.ops.auth import _current_user

        user = _current_user.get()

    # Reuse the dashboard data pipeline — dispatch calls cross-referenced
    # with local incidents and NERIS records.  Fetch more calls than the
    # dashboard overview (which only shows 15).
    data = await get_dashboard_data(call_limit=100)

    template = _jinja_env.get_template("reports.html")
    html = template.render(
        calls=data.get("recent_calls", []),
        neris_count=data.get("neris_count", 0),
        local_draft_count=data.get("local_draft_count", 0),
        missing_reports=data.get("missing_reports", 0),
        date_display=data.get("date_display", ""),
        updated_time=data.get("updated_time", ""),
        open_calls=data.get("open_calls", 0),
        today=local_now().date().isoformat(),
        active_page="reports",
    )
    return Response(html, media_type="text/html")


async def create_report(request: Request) -> Response:
    """Create a new incident and redirect to the chat UI."""
    user = _get_user(request)

    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # In dev mode, user may be set by middleware
    if not user:
        from sjifire.ops.auth import _current_user

        user = _current_user.get()
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"error": "Invalid form data"}, status_code=400)

    incident_number = str(form.get("incident_number", "")).strip()
    incident_date = str(form.get("incident_date", "")).strip()
    station = str(form.get("station", "")).strip() or "S31"
    neris_id = str(form.get("neris_id", "")).strip() or None

    if not incident_number or not incident_date:
        return JSONResponse({"error": "Incident number and date are required"}, status_code=400)

    # Use the existing create_incident tool function
    from sjifire.ops.incidents import tools as incident_tools

    result = await incident_tools.create_incident(
        incident_number=incident_number,
        incident_date=incident_date,
        station=station,
        neris_id=neris_id,
    )

    if "error" in result:
        # If duplicate, redirect to the existing report
        if "existing_id" in result:
            return RedirectResponse(f"/reports/{result['existing_id']}", status_code=303)
        return JSONResponse(result, status_code=400)

    return RedirectResponse(f"/reports/{result['id']}", status_code=303)


async def print_report(request: Request) -> Response:
    """Serve a print-optimized incident report."""
    user = _get_user(request)

    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return RedirectResponse("/.auth/login/aad?post_login_redirect_uri=" + str(request.url.path))

    incident_id = request.path_params["incident_id"]

    set_current_user(user) if user else None
    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return JSONResponse({"error": "Incident not found"}, status_code=404)

    template = _jinja_env.get_template("print_report.html")
    html = template.render(
        doc=doc.model_dump(mode="json"),
        now=local_now().strftime("%b %d, %Y %H:%M"),
    )
    return Response(html, media_type="text/html")


async def chat_page(request: Request) -> Response:
    """Serve the chat UI page for an incident."""
    user = _get_user(request)

    # Check if we're in dev mode (no EasyAuth)
    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return RedirectResponse("/.auth/login/aad?post_login_redirect_uri=" + str(request.url.path))

    incident_id = request.path_params["incident_id"]

    # Verify incident exists and user has access
    set_current_user(user) if user else None
    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return JSONResponse({"error": "Incident not found"}, status_code=404)

    # Fetch dispatch data for the summary panel
    dispatch_context: dict = {}
    try:
        async with DispatchStore() as dstore:
            dispatch = await dstore.get_by_dispatch_id(doc.incident_number)
        if dispatch:
            dispatch_context = {
                "nature": dispatch.nature,
                "address": dispatch.address,
                "time_reported": (
                    dispatch.time_reported.strftime("%b %d, %Y %H:%M")
                    if dispatch.time_reported
                    else ""
                ),
                "responding_units": dispatch.responding_units,
                "ic": (
                    dispatch.analysis.incident_commander_name
                    or dispatch.analysis.incident_commander
                ),
                "short_dsc": dispatch.analysis.short_dsc,
                "summary": dispatch.analysis.summary,
            }
    except Exception:
        logger.debug("Failed to load dispatch data for %s", doc.incident_number, exc_info=True)

    template = _jinja_env.get_template("chat.html")
    html = template.render(
        incident_id=incident_id,
        incident_number=doc.incident_number,
        incident_status=doc.status,
        completeness=doc.completeness() if not doc.neris_incident_id else None,
        dispatch=dispatch_context,
    )
    return Response(html, media_type="text/html")


async def conversation_history(request: Request) -> Response:
    """Return the conversation history as JSON."""
    user = _get_user(request)

    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    incident_id = request.path_params["incident_id"]

    async with ConversationStore() as store:
        conversation = await store.get_by_incident(incident_id)

    if conversation is None:
        return JSONResponse({"messages": [], "turn_count": 0})

    messages = [
        {
            "role": msg.role,
            "content": msg.content,
            "tool_use": msg.tool_use,
            "tool_results": [
                {"name": tr.get("name", "tool"), "summary": _result_summary(tr)}
                for tr in (msg.tool_results or [])
            ]
            if msg.tool_results
            else None,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
        }
        for msg in conversation.messages
        if msg.content or msg.tool_use  # Skip tool-result-only messages in display
    ]

    return JSONResponse(
        {
            "messages": messages,
            "turn_count": conversation.turn_count,
            "total_input_tokens": conversation.total_input_tokens,
            "total_output_tokens": conversation.total_output_tokens,
        }
    )


async def chat_stream(request: Request) -> Response:
    """Handle a chat message and stream the response as SSE."""
    user = _get_user(request)

    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # In dev mode, user may be set by middleware
    if not user:
        from sjifire.ops.auth import _current_user

        user = _current_user.get()
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    incident_id = request.path_params["incident_id"]

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)

    if len(message) > 5000:
        return JSONResponse({"error": "Message too long (max 5000 chars)"}, status_code=400)

    # Parse optional image attachments
    images: list[dict] | None = None
    raw_images = body.get("images")
    if raw_images:
        allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        if not isinstance(raw_images, list) or len(raw_images) > 3:
            return JSONResponse({"error": "Maximum 3 images allowed"}, status_code=400)
        images = []
        for img in raw_images:
            if not isinstance(img, dict):
                return JSONResponse({"error": "Invalid image format"}, status_code=400)
            media_type = img.get("media_type", "")
            data = img.get("data", "")
            if media_type not in allowed_types:
                return JSONResponse(
                    {"error": f"Unsupported image type: {media_type}"}, status_code=400
                )
            if len(data) > 2_000_000:  # ~1.5MB decoded
                return JSONResponse({"error": "Image too large (max ~1.5MB)"}, status_code=400)
            images.append({"media_type": media_type, "data": data})

    async def event_generator():
        async for event in stream_chat(incident_id, message, user, images=images):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def general_chat_stream_endpoint(request: Request) -> Response:
    """Handle a general chat message and stream the response as SSE."""
    user = _get_user(request)

    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    if not user:
        from sjifire.ops.auth import _current_user

        user = _current_user.get()
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)

    if len(message) > 5000:
        return JSONResponse({"error": "Message too long (max 5000 chars)"}, status_code=400)

    context = body.get("context")

    async def event_generator():
        async for event in stream_general_chat(message, user, context=context):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def general_chat_history(request: Request) -> Response:
    """Return the general conversation history as JSON."""
    user = _get_user(request)

    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    if not user:
        from sjifire.ops.auth import _current_user

        user = _current_user.get()
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    conversation_key = f"general:{user.email}"

    async with ConversationStore() as store:
        conversation = await store.get_by_incident(conversation_key)

    if conversation is None:
        return JSONResponse({"messages": [], "turn_count": 0})

    messages = [
        {
            "role": msg.role,
            "content": msg.content,
            "tool_use": msg.tool_use,
            "tool_results": [
                {"name": tr.get("name", "tool"), "summary": _result_summary(tr)}
                for tr in (msg.tool_results or [])
            ]
            if msg.tool_results
            else None,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
        }
        for msg in conversation.messages
        if msg.content or msg.tool_use
    ]

    return JSONResponse(
        {
            "messages": messages,
            "turn_count": conversation.turn_count,
        }
    )


def _result_summary(tool_result: dict) -> str:
    """Extract a brief summary from a tool result for display."""
    content = tool_result.get("content", "")
    if isinstance(content, str):
        try:
            data = json.loads(content)
            if "error" in data:
                return f"Error: {data['error']}"
            return content[:200]
        except (json.JSONDecodeError, TypeError):
            return str(content)[:200]
    return str(content)[:200]
