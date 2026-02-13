"""MCP dashboard tool — session-start status board.

Returns on-duty crew, recent dispatch calls, and their incident report
status in a single call so Claude.ai can immediately orient the user.

Report status is cross-referenced from two sources:
1. Local IncidentDocument drafts in Cosmos DB (in-progress reports)
2. NERIS federal reporting system (submitted/approved reports)
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from sjifire.mcp.auth import get_current_user
from sjifire.mcp.dispatch.store import DispatchStore
from sjifire.mcp.incidents import tools as incident_tools
from sjifire.mcp.incidents.store import IncidentStore
from sjifire.mcp.schedule import tools as schedule_tools

logger = logging.getLogger(__name__)

# Path to docs directory — try source tree first, then /app (Docker).
_SRC_DOCS = Path(__file__).resolve().parents[3] / "docs"
_APP_DOCS = Path("/app/docs")
_DOCS_DIR = _SRC_DOCS if _SRC_DOCS.is_dir() else _APP_DOCS


def _read_doc(filename: str) -> str:
    """Read a doc file, trying the docs dir then neris/ subdirectory."""
    for subdir in ("", "neris"):
        path = _DOCS_DIR / subdir / filename if subdir else _DOCS_DIR / filename
        if path.exists():
            return path.read_text()
    return ""


async def start_session() -> dict:
    """Start an operations dashboard session with live data.

    Call this when a user asks for the dashboard, status board, operations
    overview, or wants to see what's going on. Returns live data, a React
    component template, and rendering instructions so you can generate an
    interactive dashboard artifact in one step.
    """
    dashboard_data, incidents_data = await asyncio.gather(
        get_dashboard(),
        incident_tools.list_incidents(),
    )

    # Load instructions and template from docs at call time
    project_instructions = _read_doc("claude-project-instructions.md")
    dashboard_instructions = _read_doc("mcp-start-session.md")
    template = _read_doc("dashboard-prototype.jsx") or (
        "// Dashboard prototype not found — generate from scratch."
    )

    return {
        "project_instructions": project_instructions,
        "dashboard_instructions": dashboard_instructions,
        "template": template,
        "dashboard": dashboard_data,
        "incidents": incidents_data,
    }


def _normalize_incident_number(number: str) -> str:
    """Normalize incident number for cross-referencing.

    Dispatch IDs use "26-001980", NERIS uses "26001980".
    Stripping non-alphanumeric characters makes them match.
    """
    return number.replace("-", "")


async def get_dashboard() -> dict:
    """Get a status board with on-duty crew and recent calls.

    Returns who is on duty today, the most recent dispatch calls, and
    whether each call has an incident report (local draft or NERIS).
    Designed to be called at the start of every Claude.ai session so
    users see an instant overview.

    Returns:
        Dict with ``user``, ``on_duty``, ``recent_calls``,
        and ``call_count`` keys.
    """
    user = get_current_user()

    # Fetch all four data sources in parallel.  return_exceptions=True
    # so a single failure doesn't block the others.
    calls_result, schedule_result, incidents_result, neris_result = await asyncio.gather(
        _fetch_recent_calls(),
        _fetch_schedule(),
        _fetch_incidents(user.email, user.is_officer),
        _fetch_neris_reports(),
        return_exceptions=True,
    )

    result: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "user": {
            "email": user.email,
            "name": user.name,
            "is_officer": user.is_officer,
        },
    }

    # --- Schedule section ---
    if isinstance(schedule_result, BaseException):
        logger.exception("Dashboard: schedule fetch failed", exc_info=schedule_result)
        result["on_duty"] = {"error": str(schedule_result)}
    else:
        result["on_duty"] = schedule_result

    # --- Build incident lookup (local drafts) ---
    incident_lookup: dict[str, dict] = {}
    if isinstance(incidents_result, BaseException):
        logger.exception("Dashboard: incidents fetch failed", exc_info=incidents_result)
    else:
        incident_lookup = incidents_result

    # --- Build NERIS report lookup ---
    neris_lookup: dict[str, dict] = {}
    if isinstance(neris_result, BaseException):
        logger.exception("Dashboard: NERIS fetch failed", exc_info=neris_result)
    else:
        neris_lookup = dict(neris_result["lookup"])  # copy so we can pop matched

    # --- Unified recent calls list ---
    if isinstance(calls_result, BaseException):
        logger.exception("Dashboard: dispatch fetch failed", exc_info=calls_result)
        result["recent_calls"] = {"error": str(calls_result)}
        result["call_count"] = 0
    else:
        recent_calls = []
        for call in calls_result:
            entry: dict = {
                "dispatch_id": call.long_term_call_id,
                "date": call.time_reported.isoformat() if call.time_reported else None,
                "nature": call.nature,
                "address": call.address,
            }
            # Cross-reference: local draft takes priority, then NERIS
            normalized = _normalize_incident_number(call.long_term_call_id)
            report = incident_lookup.get(call.long_term_call_id)
            if report is None:
                report = neris_lookup.pop(normalized, None)
            else:
                # Still consume the NERIS entry so it doesn't appear twice
                neris_lookup.pop(normalized, None)
            entry["report"] = report
            recent_calls.append(entry)

        # Append NERIS reports that didn't match any dispatch call
        recent_calls.extend(
            {
                "dispatch_id": nr.get("incident_number", ""),
                "date": nr.get("call_create"),
                "nature": nr.get("incident_type", ""),
                "address": None,
                "report": nr,
            }
            for nr in neris_lookup.values()
        )

        result["recent_calls"] = recent_calls
        result["call_count"] = len(recent_calls)

    return result


async def _fetch_recent_calls():
    """Fetch recent dispatch calls from Cosmos DB."""
    async with DispatchStore() as store:
        return await store.list_recent(limit=15)


async def _fetch_schedule():
    """Fetch today's on-duty crew via the existing schedule tool."""
    return await schedule_tools.get_on_duty_crew()


async def _fetch_incidents(user_email: str, is_officer: bool) -> dict[str, dict]:
    """Fetch non-submitted incidents and build dispatch_id → report info lookup."""
    async with IncidentStore() as store:
        if is_officer:
            incidents = await store.list_by_status(exclude_status="submitted", max_items=50)
        else:
            incidents = await store.list_for_user(
                user_email, exclude_status="submitted", max_items=50
            )

    lookup: dict[str, dict] = {}
    for doc in incidents:
        lookup[doc.incident_number] = {
            "source": "local",
            "status": doc.status,
            "completeness": doc.completeness(),
            "incident_id": doc.id,
        }
    return lookup


def _list_neris_reports() -> dict:
    """Fetch NERIS incidents (blocking, for thread pool)."""
    from sjifire.neris.client import NerisClient

    with NerisClient() as client:
        incidents = client.get_all_incidents()

    lookup: dict[str, dict] = {}
    reports: list[dict] = []

    for inc in incidents:
        dispatch = inc.get("dispatch", {})
        types = inc.get("incident_types", [])
        status_info = inc.get("incident_status", {})

        neris_id = inc.get("neris_id", "")
        incident_number = dispatch.get("incident_number", "")
        status = status_info.get("status", "")
        incident_type = types[0].get("type", "") if types else ""
        call_create = dispatch.get("call_create", "")

        summary = {
            "source": "neris",
            "neris_id": neris_id,
            "incident_number": incident_number,
            "status": status,
            "incident_type": incident_type,
            "call_create": call_create,
        }

        reports.append(summary)

        # Build lookup by normalized incident number for cross-referencing
        normalized = _normalize_incident_number(incident_number)
        lookup[normalized] = summary

    return {"lookup": lookup, "reports": reports}


async def _fetch_neris_reports() -> dict:
    """Fetch NERIS reports via thread pool (blocking API)."""
    return await asyncio.to_thread(_list_neris_reports)
