"""MCP dashboard tool — session-start status board.

Returns on-duty crew, recent dispatch calls, and their incident report
status in a single call so Claude.ai can immediately orient the user.

Report status is cross-referenced from two sources:
1. Local IncidentDocument drafts in Cosmos DB (in-progress reports)
2. NERIS federal reporting system (submitted/approved reports)
"""

import asyncio
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from sjifire.core.config import get_org_config, get_timezone
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

# Jinja2 template environment for dashboard HTML shell.
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _server_url() -> str:
    org = get_org_config()
    return os.getenv("MCP_SERVER_URL", f"https://mcp.{org.domain}")


def _get_severity(nature: str) -> str:
    """Map call nature to severity level for display styling."""
    n = nature.upper()
    if "CPR" in n or "ALS" in n or "ACCIDENT" in n:
        return "high"
    if "FIRE" in n and "ALARM" not in n:
        return "medium"
    return "low"


def _get_icon(nature: str) -> str:
    """Map call nature to an emoji icon."""
    if "CPR" in nature or "ALS" in nature:
        return "\U0001f691"
    if "Accident" in nature:
        return "\U0001f697"
    if "Structure" in nature:
        return "\U0001f525"
    if "Chimney" in nature:
        return "\U0001f3e0"
    if "Alarm" in nature:
        return "\U0001f514"
    if "Vehicle" in nature:
        return "\U0001f692"
    if "Animal" in nature:
        return "\U0001f43e"
    if "Burn" in nature:
        return "\U0001f50d"
    return "\U0001f4df"


_SECTION_ORDER = ["S31", "Chief Officer", "FB31", "Support"]
_SECTION_LABELS = {
    "S31": "Station 31",
    "Chief Officer": "Chief Officer",
    "FB31": "Fireboat 31 Standby",
    "Support": "Support Standby",
}


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def _build_crew_list(
    raw_crew: list[dict],
    contact_lookup: dict[str, dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Build crew list and grouped sections from raw schedule data.

    Returns (crew, sections) where crew is a flat list of member dicts
    and sections is a list of {"key", "label", "members"} groups.
    """
    contacts = contact_lookup or {}
    crew = []
    for c in raw_crew:
        contact = contacts.get(c["name"].lower(), {})
        crew.append(
            {
                "name": c["name"],
                "position": c["position"],
                "section": c["section"],
                "shift": f"{c.get('start_time', '')}-{c.get('end_time', '')}",
                "email": contact.get("email", ""),
                "mobile": contact.get("mobile", ""),
            }
        )

    # Group by section (preserve order)
    section_members: dict[str, list] = {}
    seen_sections: list[str] = []
    for c in crew:
        sec = c["section"]
        if sec not in section_members:
            section_members[sec] = []
            seen_sections.append(sec)
        section_members[sec].append(c)

    sections = [
        {"key": k, "label": _SECTION_LABELS.get(k, k), "members": section_members[k]}
        for k in _SECTION_ORDER
        if k in section_members
    ]
    sections.extend(
        {"key": k, "label": k, "members": section_members[k]}
        for k in seen_sections
        if k not in _SECTION_ORDER
    )

    return crew, sections


def _build_template_context(
    dashboard_data: dict,
    incidents_data: dict,
    *,
    upcoming: dict | None = None,
    contacts: dict[str, dict] | None = None,
) -> dict:
    """Transform raw API data into template variables."""
    # Parse timestamp and convert to Pacific
    ts_str = dashboard_data.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_str).astimezone(get_timezone())
    except (ValueError, TypeError):
        ts = datetime.now(get_timezone())

    date_display = f"{ts.strftime('%A')}, {ts.strftime('%B')} {ts.day}, {ts.year}"
    hour = ts.hour % 12 or 12
    updated_time = f"{hour}:{ts.strftime('%M')} {'AM' if ts.hour < 12 else 'PM'}"
    is_business_hours = 8 <= ts.hour < 18

    # User info
    user = dashboard_data.get("user", {})

    # On-duty crew
    on_duty = dashboard_data.get("on_duty", {})
    platoon = on_duty.get("platoon", "")
    raw_current_crew = on_duty.get("crew", [])
    crew, sections = _build_crew_list(raw_current_crew, contacts)

    unique_crew_count = len({c["name"] for c in crew})

    # Shift end time for "until HH:MM" badge
    end_times = [c.get("end_time", "") for c in raw_current_crew if c.get("end_time")]
    shift_until = max(set(end_times), key=end_times.count).replace(":", "") if end_times else ""

    # Chief officer last name
    chief_officer = ""
    for c in crew:
        if c["section"] == "Chief Officer":
            parts = c["name"].split()
            chief_officer = parts[-1] if parts else ""
            break

    # Recent calls — separate dispatch calls from NERIS-only entries.
    # get_dashboard() appends unmatched NERIS reports (address=None) to
    # the dispatch calls list; we filter them out so stats and tables
    # only reflect actual dispatch activity.
    raw_calls = dashboard_data.get("recent_calls", [])
    if isinstance(raw_calls, dict):
        raw_calls = []  # error case
    recent_calls = []
    open_calls = 0
    neris_count = 0
    local_draft_count = 0

    for call in raw_calls:
        # Skip NERIS-only entries (no matching dispatch call)
        if call.get("address") is None:
            continue

        nature = call.get("nature", "")
        severity = _get_severity(nature)
        icon = _get_icon(nature)

        # Parse date/time and convert to Pacific
        date_str = call.get("date")
        call_date = ""
        call_time = ""
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str)
                if dt.tzinfo:
                    dt = dt.astimezone(get_timezone())
                call_date = f"{dt.strftime('%b')} {dt.day}"
                call_time = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                call_date = str(date_str)

        report = call.get("report")
        report_source = report.get("source", "") if report else ""
        if report_source == "neris":
            neris_count += 1
        elif report_source == "local":
            local_draft_count += 1
        if not call.get("is_completed", True):
            open_calls += 1

        dispatch_id = call.get("dispatch_id", "")
        neris_id = report.get("neris_id", "") if report else ""

        # Report label and prompt depend on source
        if report_source == "neris":
            report_label = "View NERIS Record"
            report_prompt = (
                f"Show NERIS record {neris_id}" if neris_id else f"Show report for {dispatch_id}"
            )
        elif report_source == "local":
            report_label = "View Draft"
            report_prompt = f"Show incident {dispatch_id}"
        else:
            report_label = "Start Report"
            report_prompt = f"Start a report for {dispatch_id}"

        ic_unit = call.get("incident_commander", "")
        ic_name = call.get("incident_commander_name", "")
        if ic_name and ic_unit:
            ic_display = f"{ic_name} ({ic_unit})"
        elif ic_name:
            ic_display = ic_name
        else:
            ic_display = ic_unit

        recent_calls.append(
            {
                "id": dispatch_id,
                "nature": nature,
                "address": call.get("address") or "",
                "ic": ic_display,
                "summary": call.get("analysis_summary", ""),
                "outcome": call.get("analysis_outcome", ""),
                "date": call_date,
                "time": call_time,
                "severity": severity,
                "icon": icon,
                "has_report": report is not None,
                "report_source": report_source,
                "neris_id": neris_id,
                "report_label": report_label,
                "report_prompt": report_prompt,
                "report_status": report.get("status", "").replace("_", " ") if report else "",
            }
        )

    # Crew date range (e.g., "Feb 12-13")
    crew_date = on_duty.get("date", "")
    crew_date_range = ""
    if crew_date:
        try:
            d = datetime.strptime(crew_date, "%Y-%m-%d")
            next_d = d + timedelta(days=1)
            crew_date_range = f"{d.strftime('%b')} {d.day}-{next_d.day}"
        except ValueError:
            pass

    missing_reports = len(recent_calls) - neris_count - local_draft_count

    # Upcoming crew (browser-only enrichment)
    upcoming_platoon = ""
    upcoming_crew: list[dict] = []
    upcoming_sections: list[dict] = []
    upcoming_date_range = ""
    upcoming_shift_starts = ""
    if upcoming and isinstance(upcoming, dict) and "error" not in upcoming:
        upcoming_platoon = upcoming.get("platoon", "")
        raw_upcoming_crew = upcoming.get("crew", [])
        upcoming_crew, upcoming_sections = _build_crew_list(raw_upcoming_crew, contacts)
        up_date = upcoming.get("date", "")
        if up_date:
            try:
                d = datetime.strptime(up_date, "%Y-%m-%d")
                next_d = d + timedelta(days=1)
                upcoming_date_range = f"{d.strftime('%b')} {d.day}-{next_d.day}"
            except ValueError:
                pass
        start_times = [c.get("start_time", "") for c in raw_upcoming_crew if c.get("start_time")]
        upcoming_shift_starts = (
            max(set(start_times), key=start_times.count).replace(":", "") if start_times else ""
        )

    return {
        "date_display": date_display,
        "updated_time": updated_time,
        "is_business_hours": is_business_hours,
        "user_name": user.get("name", ""),
        "platoon": platoon,
        "crew": crew,
        "unique_crew_count": unique_crew_count,
        "chief_officer": chief_officer,
        "open_calls": open_calls,
        "recent_calls": recent_calls,
        "neris_count": neris_count,
        "local_draft_count": local_draft_count,
        "missing_reports": max(missing_reports, 0),
        "sections": sections,
        "crew_date_range": crew_date_range,
        "shift_until": shift_until,
        "upcoming_platoon": upcoming_platoon,
        "upcoming_crew": upcoming_crew,
        "upcoming_sections": upcoming_sections,
        "upcoming_date_range": upcoming_date_range,
        "upcoming_shift_starts": upcoming_shift_starts,
    }


def _build_summary(ctx: dict) -> str:
    """Build a concise markdown summary for Claude to present as text."""
    lines: list[str] = []

    # Header
    lines.append(f"**{ctx['date_display']}** — Updated {ctx['updated_time']}")

    # Status line
    parts: list[str] = []
    oc = ctx["open_calls"]
    if oc:
        parts.append(f"{oc} Active Call{'s' if oc > 1 else ''}")
    else:
        parts.append("No Active Calls")
    parts.append(f"{ctx['unique_crew_count']} On Duty ({ctx['platoon']})")
    if ctx["chief_officer"]:
        parts.append(f"Chief: {ctx['chief_officer']}")
    lines.append(" | ".join(parts))
    lines.append("")

    # Recent calls
    rc = ctx["recent_calls"]
    missing = ctx["missing_reports"]
    hdr = f"**Recent Calls** — {len(rc)} calls"
    if missing:
        hdr += f", {missing} missing report{'s' if missing != 1 else ''}"
    lines.append(hdr)

    for c in rc[:8]:
        neris_id = c.get("neris_id", "")
        source = c.get("report_source", "")
        if source == "neris":
            status = f"NERIS `{neris_id}`" if neris_id else "NERIS"
        elif source == "local":
            status = f"Draft ({c.get('report_status', 'draft')})"
        else:
            status = "No report"
        ic_part = f" IC: {c['ic']}" if c.get("ic") else ""
        summary_part = f" — {c['summary']}" if c.get("summary") else ""
        lines.append(
            f"- {c['icon']} **{c['id']}** {c['nature']} — "
            f"{c['address']} ({c['date']} {c['time']}){ic_part}{summary_part} *{status}*"
        )
    if len(rc) > 8:
        lines.append(f"- *...and {len(rc) - 8} more*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


async def start_session() -> dict:
    """Start an operations dashboard session.

    Call this when a user asks for the dashboard, status board, operations
    overview, or wants to see what's going on.  Returns a text summary
    for fast display and a browser URL for the full visual dashboard.
    """
    t0 = datetime.now(UTC)

    dashboard_data, incidents_data = await asyncio.gather(
        get_dashboard(),
        incident_tools.list_incidents(),
    )

    ctx = _build_template_context(dashboard_data, incidents_data)
    summary = _build_summary(ctx)

    elapsed = (datetime.now(UTC) - t0).total_seconds()
    logger.info("start_session completed in %.1fs", elapsed)

    instructions = (_DOCS_DIR / "mcp-start-session.md").read_text().strip()

    return {
        "summary": summary,
        "dashboard_url": f"{_server_url()}/dashboard",
        "instructions": instructions,
    }


async def refresh_dashboard() -> dict:
    """Refresh dashboard data.

    Call this when the user says "refresh" or "update".  Returns a fresh
    text summary and browser URL.  Faster than ``start_session`` because
    it skips the instructions payload.
    """
    t0 = datetime.now(UTC)

    dashboard_data, incidents_data = await asyncio.gather(
        get_dashboard(),
        incident_tools.list_incidents(),
    )

    ctx = _build_template_context(dashboard_data, incidents_data)
    summary = _build_summary(ctx)

    elapsed = (datetime.now(UTC) - t0).total_seconds()
    logger.info("refresh_dashboard completed in %.1fs", elapsed)

    return {
        "summary": summary,
        "dashboard_url": f"{_server_url()}/dashboard",
    }


async def render_for_browser() -> str:
    """Render dashboard HTML shell. Data loaded client-side via Alpine.js."""
    template = _jinja_env.get_template("dashboard.html")
    return template.render()


async def get_dashboard_data() -> dict:
    """Fetch all data and return template context for client-side refresh.

    Uses cached NERIS data (Cosmos DB) instead of hitting the NERIS API,
    making this endpoint fast for browser polling.
    """
    dashboard_data, incidents_data, upcoming = await asyncio.gather(
        _get_dashboard_cached(),
        incident_tools.list_incidents(),
        _fetch_upcoming_schedule(),
        return_exceptions=True,
    )
    if isinstance(dashboard_data, BaseException):
        logger.exception("Dashboard data fetch failed", exc_info=dashboard_data)
        dashboard_data = {"timestamp": datetime.now(UTC).isoformat(), "user": {}}
    if isinstance(incidents_data, BaseException):
        logger.warning("Incidents unavailable: %s", incidents_data)
        incidents_data = {"incidents": []}
    if isinstance(upcoming, BaseException):
        logger.warning("Upcoming schedule unavailable: %s", upcoming)
        upcoming = None
    return _build_template_context(dashboard_data, incidents_data, upcoming=upcoming)


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
    t0 = datetime.now(UTC)

    # Fetch all four data sources in parallel.  return_exceptions=True
    # so a single failure doesn't block the others.
    calls_result, schedule_result, incidents_result, neris_result = await asyncio.gather(
        _fetch_recent_calls(),
        _fetch_schedule(),
        _fetch_incidents(user.email, user.is_officer),
        _fetch_neris_reports(),
        return_exceptions=True,
    )

    elapsed = (datetime.now(UTC) - t0).total_seconds()
    labels = ["dispatch", "schedule", "incidents", "neris"]
    statuses = [
        "err" if isinstance(r, BaseException) else "ok"
        for r in (calls_result, schedule_result, incidents_result, neris_result)
    ]
    logger.info(
        "get_dashboard fetched in %.1fs (%s)",
        elapsed,
        ", ".join(f"{name}={s}" for name, s in zip(labels, statuses, strict=True)),
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
                "incident_commander": call.analysis.incident_commander,
                "incident_commander_name": call.analysis.incident_commander_name,
                "analysis_summary": call.analysis.summary,
                "analysis_outcome": call.analysis.outcome,
                "is_completed": call.is_completed,
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
                "incident_commander": "",
                "incident_commander_name": "",
                "analysis_summary": "",
                "analysis_outcome": "",
                "report": nr,
                "is_completed": True,
            }
            for nr in neris_lookup.values()
        )

        result["recent_calls"] = recent_calls
        result["call_count"] = len(calls_result)  # dispatch calls only

    return result


async def _get_dashboard_cached() -> dict:
    """Like ``get_dashboard()`` but reads NERIS from cache (Cosmos only).

    Used by ``get_dashboard_data()`` (browser endpoint) to avoid the
    ~2 s NERIS API call on every page load.
    """
    user = get_current_user()
    t0 = datetime.now(UTC)

    calls_result, schedule_result, incidents_result, neris_result = await asyncio.gather(
        _fetch_recent_calls(),
        _fetch_schedule(),
        _fetch_incidents(user.email, user.is_officer),
        _fetch_neris_cache(),
        return_exceptions=True,
    )

    elapsed = (datetime.now(UTC) - t0).total_seconds()
    labels = ["dispatch", "schedule", "incidents", "neris_cache"]
    statuses = [
        "err" if isinstance(r, BaseException) else "ok"
        for r in (calls_result, schedule_result, incidents_result, neris_result)
    ]
    logger.info(
        "_get_dashboard_cached fetched in %.1fs (%s)",
        elapsed,
        ", ".join(f"{name}={s}" for name, s in zip(labels, statuses, strict=True)),
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
        logger.exception("Dashboard cached: schedule fetch failed", exc_info=schedule_result)
        result["on_duty"] = {"error": str(schedule_result)}
    else:
        result["on_duty"] = schedule_result

    # --- Build incident lookup (local drafts) ---
    incident_lookup: dict[str, dict] = {}
    if isinstance(incidents_result, BaseException):
        logger.exception("Dashboard cached: incidents fetch failed", exc_info=incidents_result)
    else:
        incident_lookup = incidents_result

    # --- Build NERIS report lookup ---
    neris_lookup: dict[str, dict] = {}
    if isinstance(neris_result, BaseException):
        logger.exception("Dashboard cached: NERIS cache read failed", exc_info=neris_result)
    else:
        neris_lookup = dict(neris_result["lookup"])

    # --- Unified recent calls list ---
    if isinstance(calls_result, BaseException):
        logger.exception("Dashboard cached: dispatch fetch failed", exc_info=calls_result)
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
                "incident_commander": call.analysis.incident_commander,
                "incident_commander_name": call.analysis.incident_commander_name,
                "analysis_summary": call.analysis.summary,
                "analysis_outcome": call.analysis.outcome,
                "is_completed": call.is_completed,
            }
            normalized = _normalize_incident_number(call.long_term_call_id)
            report = incident_lookup.get(call.long_term_call_id)
            if report is None:
                report = neris_lookup.pop(normalized, None)
            else:
                neris_lookup.pop(normalized, None)
            entry["report"] = report
            recent_calls.append(entry)

        recent_calls.extend(
            {
                "dispatch_id": nr.get("incident_number", ""),
                "date": nr.get("call_create"),
                "nature": nr.get("incident_type", ""),
                "address": None,
                "incident_commander": "",
                "incident_commander_name": "",
                "analysis_summary": "",
                "analysis_outcome": "",
                "report": nr,
                "is_completed": True,
            }
            for nr in neris_lookup.values()
        )

        result["recent_calls"] = recent_calls
        result["call_count"] = len(calls_result)

    return result


async def _fetch_recent_calls():
    """Fetch recent dispatch calls from Cosmos DB."""
    async with DispatchStore() as store:
        return await store.list_recent(limit=15)


async def _fetch_schedule():
    """Fetch today's on-duty crew via the existing schedule tool."""
    return await schedule_tools.get_on_duty_crew()


async def _fetch_incidents(user_email: str, is_officer: bool) -> dict[str, dict]:
    """Fetch non-submitted incidents and build dispatch_id -> report info lookup."""
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
    """Fetch NERIS reports via thread pool (blocking API).

    Also writes summaries to NerisReportStore as a side effect,
    so the cache can serve them without hitting the API.
    """
    from sjifire.mcp.neris.store import NerisReportStore

    result = await asyncio.to_thread(_list_neris_reports)

    try:
        async with NerisReportStore() as store:
            await store.bulk_upsert(result["reports"])
    except Exception:
        logger.warning("Failed to write NERIS cache", exc_info=True)

    return result


async def _fetch_neris_cache() -> dict:
    """Read cached NERIS report summaries from Cosmos DB (no API call)."""
    from sjifire.mcp.neris.store import NerisReportStore

    async with NerisReportStore() as store:
        return await store.list_as_lookup()


async def _fetch_upcoming_schedule() -> dict:
    """Fetch tomorrow's on-duty crew via the schedule tool."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    return await schedule_tools.get_on_duty_crew(target_date=tomorrow)
