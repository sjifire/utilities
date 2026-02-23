"""HTTP route handlers for the chat-based incident reporting UI.

Routes:
- POST /reports/new                        → Create new report (redirect)
- GET  /reports/{incident_id}              → Chat page (HTML)
- GET  /reports/{incident_id}/conversation → Conversation history (JSON)
- GET  /reports/{incident_id}/print        → Print report (HTML)
- GET  /chat/history                       → Dashboard chat history (JSON)

Chat message sending is handled via Centrifugo RPC proxy (see centrifugo.py).
"""

import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from sjifire.core.config import local_now
from sjifire.ops.auth import UserContext, check_is_editor, get_easyauth_user, set_current_user
from sjifire.ops.chat.store import ConversationStore
from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.incidents.store import IncidentStore

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)


def _forbidden_page() -> Response:
    """Render a styled 403 page for non-editors."""
    template = _jinja_env.get_template("forbidden.html")
    html = template.render()
    return Response(html, status_code=403, media_type="text/html")


def _get_user(request: Request) -> UserContext | None:
    """Extract authenticated user from request."""
    user = get_easyauth_user(request)
    if user:
        set_current_user(user)
    return user


async def create_report(request: Request) -> Response:
    """Create a new incident and redirect to the chat UI."""
    if request.method == "GET":
        return RedirectResponse("/dashboard#reports", status_code=303)

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

    # Only editors (or dev mode) can create reports
    if not is_dev and not await check_is_editor(user.user_id, fallback=user.is_editor):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

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

    is_editor = is_dev or (
        user is not None and await check_is_editor(user.user_id, fallback=user.is_editor)
    )
    if not is_editor:
        return _forbidden_page()

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

    # Only editors (or dev mode) can access reports
    is_editor = is_dev or (
        user is not None and await check_is_editor(user.user_id, fallback=user.is_editor)
    )
    if not is_editor:
        return _forbidden_page()

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
        incident_date=doc.incident_datetime.strftime("%Y-%m-%d") if doc.incident_datetime else "",
        incident_status=doc.status,
        completeness=doc.completeness() if not doc.neris_incident_id else None,
        dispatch=dispatch_context,
        user_email=user.email if user else "",
        user_name=user.name if user else "",
    )
    return Response(html, media_type="text/html")


async def conversation_history(request: Request) -> Response:
    """Return the conversation history as JSON."""
    user = _get_user(request)

    import os

    is_dev = not os.getenv("ENTRA_MCP_API_CLIENT_ID")

    if not user and not is_dev:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    is_editor = is_dev or (
        user is not None and await check_is_editor(user.user_id, fallback=user.is_editor)
    )
    if not is_editor:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    incident_id = request.path_params["incident_id"]

    async with ConversationStore() as store:
        conversation = await store.get_by_incident(incident_id)

    if conversation is None:
        return JSONResponse({"messages": [], "turn_count": 0})

    messages = []
    for msg in conversation.messages:
        if not msg.content and not msg.tool_use:
            continue  # Skip tool-result-only messages in display
        entry = {
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
        # Include image download URLs for blob-backed chat images
        if msg.images:
            entry["images"] = [
                f"/reports/{incident_id}/attachments/{ref['attachment_id']}" for ref in msg.images
            ]
        messages.append(entry)

    return JSONResponse(
        {
            "messages": messages,
            "turn_count": conversation.turn_count,
            "total_input_tokens": conversation.total_input_tokens,
            "total_output_tokens": conversation.total_output_tokens,
        }
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
