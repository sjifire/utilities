"""SJI Fire District Operations Server.

Operations platform (dashboard, incident reports, chat assistant) that also
serves MCP tools for Claude.ai. Authenticated via Entra ID.

Run locally::

    uv run ops-server

Or with uvicorn::

    uv run uvicorn sjifire.ops.server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import contextlib
import logging
import os
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute

from sjifire.core.config import get_org_config
from sjifire.ops import dashboard
from sjifire.ops.attachments import tools as attachment_tools
from sjifire.ops.attachments.routes import (
    delete_attachment_route,
    download_attachment_route,
    list_attachments_route,
    upload_attachment_route,
)
from sjifire.ops.auth import get_easyauth_user, set_current_user
from sjifire.ops.chat.centrifugo import connect_proxy, rpc_proxy, subscribe_proxy, websocket_proxy
from sjifire.ops.chat.routes import (
    chat_page,
    conversation_history,
    create_report,
    debug_context,
    general_chat_history,
    print_report,
    reopen_report,
)
from sjifire.ops.dispatch import tools as dispatch_tools
from sjifire.ops.events import routes as event_routes
from sjifire.ops.incidents import tools as incident_tools
from sjifire.ops.neris import tools as neris_tools
from sjifire.ops.personnel import tools as personnel_tools
from sjifire.ops.prompts import register_prompts, register_resources
from sjifire.ops.schedule import tools as schedule_tools

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Logging — module-level so it runs on import (uvicorn reimports for the app)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Silence Azure SDK HTTP-level noise (request/response headers)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.cosmos._cosmos_http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ORG = get_org_config()
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", f"https://ops.{ORG.domain}")
TENANT_ID = os.getenv("ENTRA_MCP_API_TENANT_ID") or os.getenv("MS_GRAPH_TENANT_ID", "")
API_CLIENT_ID = os.getenv("ENTRA_MCP_API_CLIENT_ID", "")
API_CLIENT_SECRET = os.getenv("ENTRA_MCP_API_CLIENT_SECRET", "")

# ---------------------------------------------------------------------------
# MCP Server — conditional auth
# ---------------------------------------------------------------------------

if API_CLIENT_ID:
    # Production: OAuth proxy with Entra ID delegation
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

    from sjifire.ops.oauth_provider import EntraOAuthProvider

    provider = EntraOAuthProvider(TENANT_ID, API_CLIENT_ID, MCP_SERVER_URL, API_CLIENT_SECRET)

    # Allow the custom domain through the SDK's DNS rebinding protection
    server_host = urlparse(MCP_SERVER_URL).hostname or "localhost"

    mcp = FastMCP(
        ORG.company_name,
        stateless_http=True,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=MCP_SERVER_URL,
            resource_server_url=f"{MCP_SERVER_URL}/mcp",
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["mcp.access"],
                default_scopes=["mcp.access"],
            ),
            required_scopes=["mcp.access"],
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[server_host],
            allowed_origins=["https://claude.ai", "https://claude.com"],
        ),
    )
else:
    # Dev mode: no auth — works with mcp dev inspector
    provider = None
    mcp = FastMCP(
        ORG.company_name,
        stateless_http=True,
    )
    logger.warning("No ENTRA_MCP_API_CLIENT_ID — running without auth (dev mode)")

    # In dev mode, inject a synthetic user so tools that call
    # get_current_user() still work (e.g., with ``mcp dev`` inspector).
    from sjifire.ops.auth import UserContext

    _DEV_USER = UserContext(
        email="dev@localhost",
        name="Dev User",
        user_id="00000000-0000-0000-0000-000000000000",
        groups=frozenset(),
    )

    class _DevAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            set_current_user(_DEV_USER)
            return await call_next(request)

    # Attached after streamable_http_app() is called below

# Register incident tools
mcp.tool()(incident_tools.create_incident)
mcp.tool()(incident_tools.get_incident)
mcp.tool()(incident_tools.list_incidents)
mcp.tool()(incident_tools.update_incident)
mcp.tool()(incident_tools.submit_to_neris)
mcp.tool()(incident_tools.reset_incident)
mcp.tool()(incident_tools.reopen_incident)
mcp.tool()(incident_tools.import_from_neris)
mcp.tool()(incident_tools.finalize_incident)
mcp.tool()(incident_tools.update_neris_incident)
mcp.tool()(incident_tools.list_neris_incidents)
mcp.tool()(incident_tools.get_neris_incident)

# Register personnel tools
mcp.tool()(personnel_tools.get_personnel)

# Register schedule tools
mcp.tool()(schedule_tools.get_on_duty_crew)

# Register NERIS reference tools
mcp.tool()(neris_tools.list_neris_value_sets)
mcp.tool()(neris_tools.get_neris_values)

# Register dashboard tools
mcp.tool()(dashboard.get_dashboard)
mcp.tool()(dashboard.start_session)
mcp.tool()(dashboard.refresh_dashboard)

# Register attachment tools
mcp.tool()(attachment_tools.upload_attachment)
mcp.tool()(attachment_tools.list_attachments)
mcp.tool()(attachment_tools.get_attachment)
mcp.tool()(attachment_tools.delete_attachment)

# Register dispatch tools
mcp.tool()(dispatch_tools.list_dispatch_calls)
mcp.tool()(dispatch_tools.get_dispatch_call)
mcp.tool()(dispatch_tools.get_open_dispatch_calls)
mcp.tool()(dispatch_tools.search_dispatch_calls)

# Register prompts and resources
register_prompts(mcp)
register_resources(mcp)


# ---------------------------------------------------------------------------
# Custom routes (bypass auth — used for OAuth callback and health)
# ---------------------------------------------------------------------------


@mcp.custom_route("/callback", methods=["GET"])
async def entra_callback(request: Request) -> Response:
    """Handle Entra ID redirect after user login."""
    if provider is None:
        return JSONResponse({"error": "Auth not configured"}, status_code=501)
    return await provider.handle_callback(request)


@mcp.custom_route("/", methods=["GET"])
async def root_redirect(request: Request) -> Response:
    """Redirect root to the dashboard."""
    return RedirectResponse("/dashboard")


@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard_page(request: Request) -> Response:
    """Serve the authenticated browser dashboard (EasyAuth SSO)."""
    user = get_easyauth_user(request)
    if user:
        set_current_user(user)
    elif provider is not None:
        return RedirectResponse("/.auth/login/aad?post_login_redirect_uri=/dashboard")
    # In dev mode, _DevAuthMiddleware already set the user
    html = await dashboard.render_for_browser()
    return Response(
        html,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@mcp.custom_route("/dashboard/data", methods=["GET"])
async def dashboard_data(request: Request) -> Response:
    """Return dashboard data as JSON for client-side refresh."""
    user = get_easyauth_user(request)
    if user:
        set_current_user(user)
    elif provider is not None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await dashboard.get_dashboard_data()
    return JSONResponse(data)


@mcp.custom_route("/api/open-calls", methods=["GET"])
async def open_calls_api(request: Request) -> Response:
    """Return open dispatch calls (30-second cached)."""
    user = get_easyauth_user(request)
    if user:
        set_current_user(user)
    elif provider is not None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await dashboard.get_open_calls_cached()
    return JSONResponse(data)


@mcp.custom_route("/dashboard/logout", methods=["GET"])
async def dashboard_logout(request: Request) -> Response:
    """Log out of the dashboard and redirect back."""
    return RedirectResponse("/.auth/logout?post_logout_redirect_uri=/dashboard")


@mcp.custom_route("/kiosk", methods=["GET"])
async def kiosk_page(request: Request) -> Response:
    """Serve the kiosk display page (token-authenticated, no EasyAuth)."""
    from sjifire.ops.kiosk.store import validate_token

    token = request.query_params.get("token", "")
    if not token or validate_token(token) is None:
        return JSONResponse({"error": "Invalid or missing token"}, status_code=401)

    html = await dashboard.render_kiosk()
    return Response(html, media_type="text/html")


@mcp.custom_route("/kiosk/data", methods=["GET"])
async def kiosk_data(request: Request) -> Response:
    """Return kiosk data as JSON (token-authenticated).

    test_mode=true or test_mode=1: Synthetic cycling scenario (test_data.py)
    test_mode=2: Real pipeline with iSpyFire fixture data (from files)
    """
    from sjifire.ops.kiosk.store import validate_token

    token = request.query_params.get("token", "")
    if not token or validate_token(token) is None:
        return JSONResponse({"error": "Invalid or missing token"}, status_code=401)

    test_mode = request.query_params.get("test_mode", "").lower()

    if test_mode in ("true", "1"):
        from sjifire.ops.kiosk.test_data import get_test_kiosk_data

        data = get_test_kiosk_data()
        # Overlay real crew data from today's schedule (fall back to test crew)
        try:
            schedule = await dashboard._fetch_schedule_for_kiosk()
            raw_crew = schedule.get("crew", [])
            crew, sections = dashboard._build_crew_list(raw_crew)
            if crew:
                data["crew"] = crew
                data["sections"] = sections
                data["platoon"] = schedule.get("platoon", "")
                crew_date = schedule.get("date", "")
                data["shift_end"] = dashboard._compute_shift_end(raw_crew, crew_date)
                upcoming = schedule.get("upcoming")
                if upcoming and isinstance(upcoming, dict):
                    raw_up = upcoming.get("crew", [])
                    up_crew, up_sec = dashboard._build_crew_list(raw_up)
                    data["upcoming_crew"] = up_crew
                    data["upcoming_sections"] = up_sec
                    data["upcoming_platoon"] = upcoming.get("platoon", "")
                    data["upcoming_shift_starts"] = dashboard._compute_shift_start(
                        raw_up, upcoming.get("date", "")
                    )
        except Exception:
            logger.debug("Could not overlay real crew in test mode", exc_info=True)
        return JSONResponse(data)

    if test_mode == "2":
        from sjifire.ops.kiosk.replay_data import get_replay_kiosk_data

        data = get_replay_kiosk_data()
        # Overlay real crew data from today's schedule (fall back to test crew)
        try:
            schedule = await dashboard._fetch_schedule_for_kiosk()
            raw_crew = schedule.get("crew", [])
            crew, sections = dashboard._build_crew_list(raw_crew)
            if crew:
                data["crew"] = crew
                data["sections"] = sections
                data["platoon"] = schedule.get("platoon", "")
                crew_date = schedule.get("date", "")
                data["shift_end"] = dashboard._compute_shift_end(raw_crew, crew_date)
                upcoming = schedule.get("upcoming")
                if upcoming and isinstance(upcoming, dict):
                    raw_up = upcoming.get("crew", [])
                    up_crew, up_sec = dashboard._build_crew_list(raw_up)
                    data["upcoming_crew"] = up_crew
                    data["upcoming_sections"] = up_sec
                    data["upcoming_platoon"] = upcoming.get("platoon", "")
                    data["upcoming_shift_starts"] = dashboard._compute_shift_start(
                        raw_up, upcoming.get("date", "")
                    )
        except Exception:
            logger.debug("Could not overlay real crew in test mode", exc_info=True)
        return JSONResponse(data)

    data = await dashboard.get_kiosk_data()
    return JSONResponse(data)


@mcp.custom_route("/events", methods=["GET"])
async def _events_page_view(request: Request) -> Response:
    """Redirect to the dashboard events tab."""
    return await event_routes.events_page(request)


@mcp.custom_route("/events/data", methods=["GET"])
async def _events_data_view(request: Request) -> Response:
    """Return combined calendar events + event records as JSON."""
    return await event_routes.events_data(request)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(
        {
            "status": "ok",
            "service": "sjifire-ops",
            "version": os.getenv("BUILD_VERSION", "dev"),
        }
    )


# ---------------------------------------------------------------------------
# ASGI App assembly
# ---------------------------------------------------------------------------

app = mcp.streamable_http_app()

# Chat routes — Starlette Route directly because @mcp.custom_route
# doesn't support path parameters like {incident_id}.
# Order matters: exact paths before parameterized paths.
app.routes.insert(0, Route("/reports/{incident_id}/debug-context", debug_context))
app.routes.insert(0, Route("/reports/{incident_id}/conversation", conversation_history))
app.routes.insert(0, Route("/reports/{incident_id}/print", print_report))
app.routes.insert(0, Route("/reports/{incident_id}/reopen", reopen_report, methods=["POST"]))
app.routes.insert(0, Route("/reports/{incident_id}", chat_page))
app.routes.insert(0, Route("/reports/new", create_report, methods=["GET", "POST"]))


async def _reports_redirect(request: Request) -> Response:
    """Redirect /reports to dashboard reports tab."""
    return RedirectResponse("/dashboard#reports")


app.routes.insert(0, Route("/reports", _reports_redirect))

# Attachment routes — exact paths before parameterized paths.
app.routes.insert(
    0,
    Route(
        "/reports/{incident_id}/attachments/{attachment_id}",
        download_attachment_route,
        methods=["GET"],
    ),
)
app.routes.insert(
    0,
    Route(
        "/reports/{incident_id}/attachments/{attachment_id}",
        delete_attachment_route,
        methods=["DELETE"],
    ),
)
app.routes.insert(
    0,
    Route(
        "/reports/{incident_id}/attachments",
        upload_attachment_route,
        methods=["POST"],
    ),
)
app.routes.insert(
    0,
    Route(
        "/reports/{incident_id}/attachments",
        list_attachments_route,
        methods=["GET"],
    ),
)

# Events routes — parameterized paths via Starlette Route
app.routes.insert(
    0,
    Route(
        "/events/records/{record_id}/attachments/{att_id}",
        event_routes.download_attachment,
        methods=["GET"],
    ),
)
app.routes.insert(
    0,
    Route(
        "/events/records/{record_id}/attachments/{att_id}",
        event_routes.delete_attachment,
        methods=["DELETE"],
    ),
)
app.routes.insert(
    0,
    Route("/events/records/{record_id}/upload", event_routes.upload_file, methods=["POST"]),
)
app.routes.insert(
    0,
    Route("/events/records/{record_id}/parse", event_routes.parse_attendees, methods=["POST"]),
)
app.routes.insert(
    0,
    Route("/events/records/{record_id}", event_routes.get_record, methods=["GET"]),
)
app.routes.insert(
    0,
    Route("/events/records/{record_id}", event_routes.update_record, methods=["PATCH"]),
)
app.routes.insert(
    0,
    Route("/events/records", event_routes.create_record, methods=["POST"]),
)


# Events personnel endpoint for typeahead
async def _events_personnel(request: Request) -> Response:
    """Return personnel list for event attendee typeahead."""
    user = get_easyauth_user(request)
    if user:
        set_current_user(user)
    elif provider is not None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from sjifire.ops.personnel.tools import get_personnel

    personnel = await get_personnel()
    return JSONResponse(personnel)


app.routes.insert(0, Route("/events/personnel", _events_personnel, methods=["GET"]))

# General chat routes (not tied to an incident)
app.routes.insert(0, Route("/chat/history", general_chat_history))

# Centrifugo routes — WebSocket proxy + auth callbacks + RPC proxy
app.routes.insert(0, WebSocketRoute("/connection/websocket", websocket_proxy))
app.routes.insert(0, Route("/centrifugo/rpc", rpc_proxy, methods=["POST"]))
app.routes.insert(0, Route("/centrifugo/connect", connect_proxy, methods=["POST"]))
app.routes.insert(0, Route("/centrifugo/subscribe", subscribe_proxy, methods=["POST"]))

# Test-only route: seed in-memory stores with fixture data
if os.getenv("TESTING") == "1":

    async def _test_seed(request: Request) -> JSONResponse:
        """Populate in-memory stores with test fixture data.

        Only available when TESTING=1.  Accepts JSON with optional keys:
        ``dispatch_calls`` (list of Cosmos-serialized DispatchCallDocument dicts),
        ``schedule`` (list of Cosmos-serialized DayScheduleCache dicts),
        ``incidents`` (list of Cosmos-serialized IncidentDocument dicts),
        and ``is_editor`` (bool to toggle editor mode for the dev user).
        """
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.incidents.store import IncidentStore
        from sjifire.ops.schedule.store import ScheduleStore

        body = await request.json()
        seeded: dict[str, int | bool] = {}

        for call_data in body.get("dispatch_calls", []):
            DispatchStore._memory[call_data["id"]] = call_data
            seeded["dispatch_calls"] = seeded.get("dispatch_calls", 0) + 1

        for sched_data in body.get("schedule", []):
            ScheduleStore._memory[sched_data["date"]] = sched_data
            seeded["schedule"] = seeded.get("schedule", 0) + 1

        for inc_data in body.get("incidents", []):
            IncidentStore._memory[inc_data["id"]] = inc_data
            seeded["incidents"] = seeded.get("incidents", 0) + 1

        # Toggle editor mode for the dev user
        if body.get("is_editor"):
            import sjifire.ops.auth as auth_mod

            editor_group_id = "test-editor-group"
            auth_mod._EDITOR_GROUP_ID = editor_group_id

            global _DEV_USER
            _DEV_USER = UserContext(
                email="dev@localhost",
                name="Dev User",
                user_id="00000000-0000-0000-0000-000000000000",
                groups=frozenset({editor_group_id}),
            )
            seeded["is_editor"] = True

        # Clear dashboard + kiosk caches so seeded data is picked up immediately
        dashboard._open_docs_cache = None
        dashboard._open_docs_ts = 0
        dashboard._kiosk_cache = None
        dashboard._kiosk_cache_ts = 0

        return JSONResponse({"seeded": seeded})

    app.routes.insert(0, Route("/test/seed", _test_seed, methods=["POST"]))

# Dev mode: inject synthetic user context on every request
if provider is None:
    app.add_middleware(_DevAuthMiddleware)

# CORS for Claude.ai cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://claude.ai", "https://claude.com"],
    allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "MCP-Protocol-Version"],
    expose_headers=["MCP-Protocol-Version"],
)


# ---------------------------------------------------------------------------
# Background dispatch sync
# ---------------------------------------------------------------------------

_bg_sync_task: asyncio.Task | None = None


async def _start_dispatch_sync() -> None:
    global _bg_sync_task
    from sjifire.ops.dispatch.sync import dispatch_sync_loop

    _bg_sync_task = asyncio.create_task(dispatch_sync_loop())
    logger.info("Started background dispatch sync")


async def _stop_dispatch_sync() -> None:
    global _bg_sync_task
    if _bg_sync_task:
        _bg_sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _bg_sync_task
        _bg_sync_task = None
        logger.info("Stopped background dispatch sync")


app.add_event_handler("startup", _start_dispatch_sync)
app.add_event_handler("shutdown", _stop_dispatch_sync)


# ---------------------------------------------------------------------------
# Old-domain redirect: mcp.sjifire.org → ops.sjifire.org
# ---------------------------------------------------------------------------


class _OldDomainRedirectMiddleware(BaseHTTPMiddleware):
    """301 redirect requests on the old mcp.* hostname to ops.*."""

    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        if host.startswith("mcp."):
            new_url = str(request.url).replace("://mcp.", "://ops.", 1)
            return RedirectResponse(new_url, status_code=301)
        return await call_next(request)


app.add_middleware(_OldDomainRedirectMiddleware)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the ops server with uvicorn."""
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")

    logger.info("Starting SJI Fire ops server on %s:%d", host, port)
    uvicorn.run(
        "sjifire.ops.server:app",
        host=host,
        port=port,
        log_level="info",
    )
