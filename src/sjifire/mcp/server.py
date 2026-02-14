"""SJI Fire District MCP Server.

Remote MCP server for Claude.ai that provides incident reporting,
schedule lookup, and personnel tools. Authenticated via Entra ID.

Run locally::

    uv run mcp-server

Or with uvicorn::

    uv run uvicorn sjifire.mcp.server:app --host 0.0.0.0 --port 8000
"""

import logging
import os
from urllib.parse import urlparse

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from sjifire.core.config import get_org_config
from sjifire.mcp import dashboard
from sjifire.mcp.auth import get_easyauth_user, set_current_user
from sjifire.mcp.chat.routes import (
    chat_page,
    chat_stream,
    conversation_history,
    create_report,
    reports_list,
)
from sjifire.mcp.dispatch import tools as dispatch_tools
from sjifire.mcp.incidents import tools as incident_tools
from sjifire.mcp.neris import tools as neris_tools
from sjifire.mcp.personnel import tools as personnel_tools
from sjifire.mcp.prompts import register_prompts, register_resources
from sjifire.mcp.schedule import tools as schedule_tools

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

load_dotenv()

ORG = get_org_config()
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", f"https://mcp.{ORG.domain}")
TENANT_ID = os.getenv("ENTRA_MCP_API_TENANT_ID") or os.getenv("MS_GRAPH_TENANT_ID", "")
API_CLIENT_ID = os.getenv("ENTRA_MCP_API_CLIENT_ID", "")
API_CLIENT_SECRET = os.getenv("ENTRA_MCP_API_CLIENT_SECRET", "")

# ---------------------------------------------------------------------------
# MCP Server — conditional auth
# ---------------------------------------------------------------------------

if API_CLIENT_ID:
    # Production: OAuth proxy with Entra ID delegation
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

    from sjifire.mcp.oauth_provider import EntraOAuthProvider

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
    from starlette.middleware.base import BaseHTTPMiddleware

    from sjifire.mcp.auth import UserContext

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
mcp.tool()(incident_tools.submit_incident)
mcp.tool()(incident_tools.reset_incident)
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
    return Response(html, media_type="text/html")


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


@mcp.custom_route("/dashboard/logout", methods=["GET"])
async def dashboard_logout(request: Request) -> Response:
    """Log out of the dashboard and redirect back."""
    return RedirectResponse("/.auth/logout?post_logout_redirect_uri=/dashboard")


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(
        {
            "status": "ok",
            "service": "sjifire-mcp",
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
app.routes.insert(0, Route("/reports/{incident_id}/chat", chat_stream, methods=["POST"]))
app.routes.insert(0, Route("/reports/{incident_id}/conversation", conversation_history))
app.routes.insert(0, Route("/reports/{incident_id}", chat_page))
app.routes.insert(0, Route("/reports/new", create_report, methods=["POST"]))
app.routes.insert(0, Route("/reports", reports_list))

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
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server with uvicorn."""
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")

    logger.info("Starting SJI Fire MCP server on %s:%d", host, port)
    uvicorn.run(
        "sjifire.mcp.server:app",
        host=host,
        port=port,
        log_level="info",
    )
