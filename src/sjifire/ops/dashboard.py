"""Operations dashboard — session-start status board.

Returns on-duty crew, recent dispatch calls, and their incident report
status in a single call so Claude.ai can immediately orient the user.

Report status is cross-referenced from two sources:
1. Local IncidentDocument drafts in Cosmos DB (in-progress reports)
2. NERIS federal reporting system (submitted/approved reports)
"""

import asyncio
import contextlib
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from sjifire.core.config import get_org_config, get_timezone, local_now
from sjifire.core.schedule import position_sort_key, section_sort_key
from sjifire.ops.auth import get_current_user
from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.incidents import tools as incident_tools
from sjifire.ops.incidents.store import IncidentStore
from sjifire.ops.schedule import tools as schedule_tools

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
    return os.getenv("MCP_SERVER_URL", f"https://ops.{org.domain}")


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


def _get_section_labels() -> dict[str, str]:
    """Get section display labels from organization config."""
    return get_org_config().schedule_section_labels


# ---------------------------------------------------------------------------
# Cached open-calls poller (30-second TTL)
# ---------------------------------------------------------------------------

_OPEN_CALLS_TTL = 5  # seconds
_open_calls_cache: dict | None = None
_open_calls_ts: float = 0
_open_calls_lock = asyncio.Lock()


async def get_open_calls_cached() -> dict:
    """Return open dispatch calls with a 30-second server-side cache.

    Polls iSpyFire's lightweight open-calls endpoint and caches the
    result so multiple browser clients don't each trigger a fetch.
    """
    global _open_calls_cache, _open_calls_ts

    now = time.monotonic()
    if _open_calls_cache is not None and (now - _open_calls_ts) < _OPEN_CALLS_TTL:
        return _open_calls_cache

    async with _open_calls_lock:
        # Re-check after acquiring lock (another request may have refreshed)
        now = time.monotonic()
        if _open_calls_cache is not None and (now - _open_calls_ts) < _OPEN_CALLS_TTL:
            return _open_calls_cache

        try:
            async with DispatchStore() as store:
                docs = await store.fetch_open()

            ts = local_now()
            hour = ts.hour % 12 or 12
            updated_time = f"{hour}:{ts.strftime('%M')} {'AM' if ts.hour < 12 else 'PM'}"

            result = {
                "open_calls": len(docs),
                "updated_time": updated_time,
                "calls": [
                    {
                        "dispatch_id": d.long_term_call_id,
                        "nature": d.nature,
                        "address": d.address,
                    }
                    for d in docs
                ],
            }
        except Exception:
            logger.exception("Failed to fetch open calls")
            # Return stale cache if available, otherwise empty
            if _open_calls_cache is not None:
                return _open_calls_cache
            result = {"open_calls": 0, "updated_time": "", "calls": []}

        _open_calls_cache = result
        _open_calls_ts = time.monotonic()
        return result


# ---------------------------------------------------------------------------
# Shift timing helpers
# ---------------------------------------------------------------------------


def _compute_shift_end(raw_crew: list[dict], crew_date: str) -> str:
    """Compute shift end as ISO datetime string from raw crew data."""
    end_times = [c.get("end_time", "") for c in raw_crew if c.get("end_time")]
    if not end_times or not crew_date:
        return ""
    most_common_end = max(set(end_times), key=end_times.count)
    try:
        tz = get_timezone()
        crew_dt = datetime.strptime(crew_date, "%Y-%m-%d").date()
        end_parts = most_common_end.split(":")
        end_h, end_m = int(end_parts[0]), int(end_parts[1]) if len(end_parts) > 1 else 0
        shift_end_dt = datetime(crew_dt.year, crew_dt.month, crew_dt.day, end_h, end_m, tzinfo=tz)
        start_times = [c.get("start_time", "") for c in raw_crew if c.get("start_time")]
        if start_times:
            most_common_start = max(set(start_times), key=start_times.count)
            start_h = int(most_common_start.split(":")[0])
            if end_h <= start_h:
                shift_end_dt += timedelta(days=1)
        return shift_end_dt.isoformat()
    except (ValueError, IndexError):
        return ""


def _compute_shift_start(raw_crew: list[dict], crew_date: str) -> str:
    """Compute shift start as ISO datetime string from raw crew data."""
    start_times = [c.get("start_time", "") for c in raw_crew if c.get("start_time")]
    if not start_times or not crew_date:
        return ""
    most_common_start = max(set(start_times), key=start_times.count)
    try:
        tz = get_timezone()
        crew_dt = datetime.strptime(crew_date, "%Y-%m-%d").date()
        sp = most_common_start.split(":")
        s_h, s_m = int(sp[0]), int(sp[1]) if len(sp) > 1 else 0
        return datetime(crew_dt.year, crew_dt.month, crew_dt.day, s_h, s_m, tzinfo=tz).isoformat()
    except (ValueError, IndexError):
        return ""


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
        raw_position = c["position"]
        contact = contacts.get(c["name"].lower(), {})
        crew.append(
            {
                "name": c["name"],
                "position": "AO" if raw_position == "Apparatus Operator" else raw_position,
                "_sort_key": position_sort_key(raw_position),
                "section": c["section"],
                "shift": f"{c.get('start_time', '')}-{c.get('end_time', '')}",
                "email": contact.get("email", ""),
                "mobile": contact.get("mobile", ""),
            }
        )

    # Group by section, sort sections by priority, positions by seniority
    section_members: dict[str, list] = {}
    for c in crew:
        sec = c["section"]
        if sec not in section_members:
            section_members[sec] = []
        section_members[sec].append(c)
    for members in section_members.values():
        members.sort(key=lambda c: c["_sort_key"])

    ordered_keys = sorted(section_members, key=section_sort_key)
    sections = [
        {"key": k, "label": _get_section_labels().get(k, k), "members": section_members[k]}
        for k in ordered_keys
    ]

    # Rebuild flat crew list in section + position order (for overview tab)
    crew = [c for s in sections for c in s["members"]]

    return crew, sections


def _build_template_context(
    dashboard_data: dict,
    incidents_data: dict,
    *,
    contacts: dict[str, dict] | None = None,
) -> dict:
    """Transform raw API data into template variables."""
    # Parse timestamp and convert to Pacific
    ts_str = dashboard_data.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_str).astimezone(get_timezone())
    except (ValueError, TypeError):
        ts = local_now()

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

    # Shift end — ISO datetime for client-side relative formatting
    end_times = [c.get("end_time", "") for c in raw_current_crew if c.get("end_time")]
    shift_end = ""
    crew_date = on_duty.get("date", "")
    if end_times and crew_date:
        most_common_end = max(set(end_times), key=end_times.count)
        try:
            tz = get_timezone()
            crew_dt = datetime.strptime(crew_date, "%Y-%m-%d").date()
            end_parts = most_common_end.split(":")
            end_h, end_m = int(end_parts[0]), int(end_parts[1]) if len(end_parts) > 1 else 0
            shift_end_dt = datetime(
                crew_dt.year, crew_dt.month, crew_dt.day, end_h, end_m, tzinfo=tz
            )
            # If shift wraps to next day (end <= start), add one day
            start_times = [c.get("start_time", "") for c in raw_current_crew if c.get("start_time")]
            if start_times:
                most_common_start = max(set(start_times), key=start_times.count)
                start_h = int(most_common_start.split(":")[0])
                if end_h <= start_h:
                    shift_end_dt += timedelta(days=1)
            shift_end = shift_end_dt.isoformat()
        except (ValueError, IndexError):
            pass

    # Chief officer last name
    chief_officer = ""
    for c in crew:
        if c["section"] == "Chief Officer":
            parts = c["name"].split()
            chief_officer = parts[-1] if parts else ""
            break

    # Recent calls — dispatch calls with cross-referenced reports.
    # NERIS-only entries (legacy reports with 26SJ/numeric IDs that
    # don't match any dispatch call) are excluded from the dashboard.
    raw_calls = dashboard_data.get("recent_calls", [])
    if isinstance(raw_calls, dict):
        raw_calls = []  # error case
    recent_calls = []
    open_calls = 0
    neris_count = 0
    local_draft_count = 0

    for call in raw_calls:
        # Skip NERIS-only entries (legacy reports with no dispatch match)
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

        # Completeness for local drafts (e.g., "3/5")
        completeness = report.get("completeness") if report else None

        recent_calls.append(
            {
                "id": dispatch_id,
                "nature": nature,
                "address": call.get("address") or "",
                "ic": ic_display,
                "summary": call.get("analysis_summary", ""),
                "outcome": call.get("analysis_outcome", ""),
                "short_dsc": call.get("short_dsc", ""),
                "date": call_date,
                "time": call_time,
                "severity": severity,
                "icon": icon,
                "has_report": report is not None,
                "report_source": report_source,
                "neris_id": neris_id,
                "incident_id": report.get("incident_id", "") if report else "",
                "completeness": completeness,
                "report_label": report_label,
                "report_prompt": report_prompt,
                "report_status": report.get("status", "").replace("_", " ") if report else "",
            }
        )

    # Crew date range (e.g., "Feb 12-13")
    crew_date_range = ""
    if crew_date:
        try:
            d = datetime.strptime(crew_date, "%Y-%m-%d")
            next_d = d + timedelta(days=1)
            crew_date_range = f"{d.strftime('%b')} {d.day}-{next_d.day}"
        except ValueError:
            pass

    missing_reports = len(recent_calls) - neris_count - local_draft_count

    # Upcoming crew — embedded in the on_duty response by get_on_duty_crew()
    upcoming_platoon = ""
    upcoming_crew: list[dict] = []
    upcoming_sections: list[dict] = []
    upcoming_date_range = ""
    upcoming_shift_starts = ""
    upcoming_data = on_duty.get("upcoming")
    if upcoming_data and isinstance(upcoming_data, dict):
        upcoming_platoon = upcoming_data.get("platoon", "")
        raw_upcoming_crew = upcoming_data.get("crew", [])
        upcoming_crew, upcoming_sections = _build_crew_list(raw_upcoming_crew, contacts)
        up_date = upcoming_data.get("date", "")
        if up_date:
            try:
                d = datetime.strptime(up_date, "%Y-%m-%d")
                next_d = d + timedelta(days=1)
                upcoming_date_range = f"{d.strftime('%b')} {d.day}-{next_d.day}"
            except ValueError:
                pass
        start_times = [c.get("start_time", "") for c in raw_upcoming_crew if c.get("start_time")]
        upcoming_shift_starts = ""
        if start_times and up_date:
            most_common_start = max(set(start_times), key=start_times.count)
            try:
                tz = get_timezone()
                up_dt = datetime.strptime(up_date, "%Y-%m-%d").date()
                sp = most_common_start.split(":")
                s_h, s_m = int(sp[0]), int(sp[1]) if len(sp) > 1 else 0
                upcoming_shift_starts = datetime(
                    up_dt.year, up_dt.month, up_dt.day, s_h, s_m, tzinfo=tz
                ).isoformat()
            except (ValueError, IndexError):
                upcoming_shift_starts = most_common_start.replace(":", "")

    # Fastest turnout stat
    ft = dashboard_data.get("fastest_turnout")
    fastest_turnout = (
        {
            "display": ft["display"],
            "unit": ft["unit"],
            "nature": ft["nature"],
            "date": ft["date"],
            "time": ft.get("time", ""),
            "dispatch_id": ft["dispatch_id"],
        }
        if ft
        else None
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
        "shift_end": shift_end,
        "upcoming_platoon": upcoming_platoon,
        "upcoming_crew": upcoming_crew,
        "upcoming_sections": upcoming_sections,
        "upcoming_date_range": upcoming_date_range,
        "upcoming_shift_starts": upcoming_shift_starts,
        "fastest_turnout": fastest_turnout,
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
# Tools
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


async def render_for_browser(*, show_reports: bool = False) -> str:
    """Render dashboard HTML shell. Data loaded client-side via Alpine.js."""
    template = _jinja_env.get_template("dashboard.html")
    return template.render(active_page="dashboard", show_reports=show_reports)


async def render_kiosk() -> str:
    """Render the kiosk HTML page for station bay monitors."""
    template = _jinja_env.get_template("kiosk.html")
    azure_maps_key = os.getenv("AZURE_MAPS_KEY", "")
    return template.render(azure_maps_key=azure_maps_key)


# ---------------------------------------------------------------------------
# Kiosk data — adaptive caching for open calls + crew
# ---------------------------------------------------------------------------

# Track when each call was first seen (call_id -> monotonic timestamp)
_kiosk_call_first_seen: dict[str, float] = {}

# Recently-completed calls kept on kiosk in "archived" mode.
# Maps dispatch_id -> {"data": <enriched call dict>, "completed_at": <ISO str>}
_kiosk_archived_calls: dict[str, dict] = {}

# Default hours to keep archived calls on the kiosk display.
_KIOSK_ARCHIVE_HOURS = 12

# Kiosk cache
_kiosk_cache: dict | None = None
_kiosk_cache_ts: float = 0
_kiosk_cache_lock = asyncio.Lock()


def _kiosk_cache_ttl() -> float:
    """Determine cache TTL based on active call state.

    - No active calls, no archived: 5s
    - Active call, first 5 min: 2s (data changing rapidly)
    - Archived call, first 2 min: 3s (recently completed, update promptly)
    - Otherwise: 5s (data stabilized)
    """
    now = time.monotonic()

    # Active calls take priority
    for first_seen in _kiosk_call_first_seen.values():
        if (now - first_seen) < 300:  # 5 minutes
            return 2.0

    if _kiosk_call_first_seen:
        return 5.0

    # Recently archived calls — slightly faster refresh for the first 2 min
    for entry in _kiosk_archived_calls.values():
        if (now - entry["mono_ts"]) < 120:
            return 3.0

    return 5.0


async def get_kiosk_data() -> dict:
    """Fetch enriched open calls + on-duty crew for the kiosk display.

    Uses adaptive server-side caching: faster refresh during the first
    5 minutes of a new call, slower once data stabilizes.
    """
    global _kiosk_cache, _kiosk_cache_ts

    now = time.monotonic()
    ttl = _kiosk_cache_ttl()
    if _kiosk_cache is not None and (now - _kiosk_cache_ts) < ttl:
        return _kiosk_cache

    async with _kiosk_cache_lock:
        # Re-check after acquiring lock
        now = time.monotonic()
        ttl = _kiosk_cache_ttl()
        if _kiosk_cache is not None and (now - _kiosk_cache_ts) < ttl:
            return _kiosk_cache

        result = await _fetch_kiosk_data()
        _kiosk_cache = result
        _kiosk_cache_ts = time.monotonic()
        return result


def _find_previous_call(dispatch_id: str) -> dict | None:
    """Find a call's enriched data from the last kiosk cache snapshot."""
    if _kiosk_cache is None:
        return None
    for call in _kiosk_cache.get("calls", []):
        if call.get("dispatch_id") == dispatch_id and not call.get("archived"):
            # Deep copy so archived snapshot is independent of future cache updates
            return dict(call)
    return None


async def _fetch_kiosk_data() -> dict:
    """Fetch open calls (enriched) and schedule in parallel."""
    open_calls_result, schedule_result = await asyncio.gather(
        _fetch_open_calls_enriched(),
        _fetch_schedule_for_kiosk(),
        return_exceptions=True,
    )

    result: dict = {"timestamp": datetime.now(UTC).isoformat()}

    # Open calls
    if isinstance(open_calls_result, BaseException):
        logger.exception("Kiosk: open calls fetch failed", exc_info=open_calls_result)
        result["calls"] = []
    else:
        result["calls"] = open_calls_result

    # Update first-seen tracking and detect newly-completed calls
    active_calls = [c for c in result["calls"] if not c.get("archived")]
    current_ids = {c["dispatch_id"] for c in active_calls}
    now_mono = time.monotonic()
    now_utc = datetime.now(UTC)

    # Archive calls that just disappeared from the open list
    for old_id in list(_kiosk_call_first_seen):
        if old_id not in current_ids:
            # Look up the last known enriched data from the previous cache
            prev_call = _find_previous_call(old_id)
            if prev_call:
                prev_call["archived"] = True
                prev_call["completed_at"] = now_utc.isoformat()
                _kiosk_archived_calls[old_id] = {
                    "data": prev_call,
                    "completed_at": now_utc.isoformat(),
                    "mono_ts": now_mono,
                }
                logger.info("Kiosk: archived completed call %s", old_id)
            del _kiosk_call_first_seen[old_id]

    for call_id in current_ids:
        if call_id not in _kiosk_call_first_seen:
            _kiosk_call_first_seen[call_id] = now_mono

    # Determine archive window
    archive_seconds = _KIOSK_ARCHIVE_HOURS * 3600

    # Option A: any active call clears all archived calls
    if current_ids and _kiosk_archived_calls:
        logger.info(
            "Kiosk: clearing %d archived call(s) — active call present",
            len(_kiosk_archived_calls),
        )
        _kiosk_archived_calls.clear()

    # Time-based expiry for archived calls (when no active calls)
    for aid in list(_kiosk_archived_calls):
        entry = _kiosk_archived_calls[aid]
        if (now_mono - entry["mono_ts"]) > archive_seconds:
            logger.info("Kiosk: expired archived call %s (age > %dh)", aid, _KIOSK_ARCHIVE_HOURS)
            del _kiosk_archived_calls[aid]

    # Append archived calls to the response (after active calls)
    for entry in _kiosk_archived_calls.values():
        result["calls"].append(entry["data"])

    # Schedule
    if isinstance(schedule_result, BaseException):
        logger.exception("Kiosk: schedule fetch failed", exc_info=schedule_result)
        result["schedule"] = {}
    else:
        result["schedule"] = schedule_result

    # Build crew list using existing helper
    raw_crew = result["schedule"].get("crew", [])
    crew, sections = _build_crew_list(raw_crew)
    result["crew"] = crew
    result["sections"] = sections
    result["platoon"] = result["schedule"].get("platoon", "")

    # Shift end timing
    crew_date = result["schedule"].get("date", "")
    result["shift_end"] = _compute_shift_end(raw_crew, crew_date)

    # Upcoming crew
    upcoming = result["schedule"].get("upcoming")
    if upcoming and isinstance(upcoming, dict):
        raw_upcoming = upcoming.get("crew", [])
        up_crew, up_sections = _build_crew_list(raw_upcoming)
        result["upcoming_crew"] = up_crew
        result["upcoming_sections"] = up_sections
        result["upcoming_platoon"] = upcoming.get("platoon", "")
        up_date = upcoming.get("date", "")
        result["upcoming_shift_starts"] = _compute_shift_start(raw_upcoming, up_date)
    else:
        result["upcoming_crew"] = []
        result["upcoming_sections"] = []
        result["upcoming_platoon"] = ""
        result["upcoming_shift_starts"] = ""

    return result


async def _fetch_schedule_for_kiosk() -> dict:
    """Fetch schedule without requiring auth context (for kiosk display).

    Replicates the core logic of ``get_on_duty_crew()`` from schedule
    tools, but skips the ``get_current_user()`` call since the kiosk
    authenticates via signed token instead of Entra ID.
    """
    from sjifire.core.schedule import resolve_duty_date
    from sjifire.ops.schedule.store import ScheduleStore
    from sjifire.ops.schedule.tools import (
        _build_crew_list as _build_schedule_crew,
    )
    from sjifire.ops.schedule.tools import (
        _detect_shift_change_hour_from_cache,
        _ensure_cache,
    )

    now = local_now()
    dt = now.date()
    needed = [
        (dt - timedelta(days=1)).isoformat(),
        dt.isoformat(),
        (dt + timedelta(days=1)).isoformat(),
    ]

    async with ScheduleStore() as store:
        cached = await _ensure_cache(store, needed)

    shift_change_hour = _detect_shift_change_hour_from_cache(cached)
    effective_hour = now.hour if shift_change_hour is not None else None
    duty_date, upcoming_date = resolve_duty_date(dt, shift_change_hour, effective_hour)

    day = cached.get(duty_date.isoformat())
    if day is None:
        return {"crew": [], "platoon": ""}

    crew = _build_schedule_crew(day, include_admin=False)
    result: dict = {
        "date": duty_date.isoformat(),
        "platoon": day.platoon,
        "crew": crew,
    }

    if upcoming_date is not None:
        upcoming_day = cached.get(upcoming_date.isoformat())
        if upcoming_day:
            result["upcoming"] = {
                "date": upcoming_date.isoformat(),
                "platoon": upcoming_day.platoon,
                "crew": _build_schedule_crew(upcoming_day, include_admin=False),
            }

    return result


async def _fetch_open_calls_enriched() -> list[dict]:
    """Fetch open calls from iSpyFire and enrich each one.

    For each call: parse geo_location, add severity/icon,
    query site history, return full to_dict().
    """
    async with DispatchStore() as store:
        docs = await store.fetch_open()

        enriched = []
        for doc in docs:
            call_data = doc.to_dict()

            # Parse geo_location into lat/lon
            lat, lon = None, None
            geo = doc.geo_location or ""
            if geo:
                parts = geo.replace(" ", "").split(",")
                if len(parts) == 2:
                    with contextlib.suppress(ValueError):
                        lat, lon = float(parts[0]), float(parts[1])

            call_data["dispatch_id"] = doc.long_term_call_id
            call_data["latitude"] = lat
            call_data["longitude"] = lon
            call_data["severity"] = _get_severity(doc.nature)
            call_data["icon"] = _get_icon(doc.nature)

            # Site history (max 5)
            if doc.address:
                try:
                    history = await store.list_by_address(
                        doc.address, exclude_id=doc.id, max_items=5
                    )
                    call_data["site_history"] = [
                        {
                            "dispatch_id": h.long_term_call_id,
                            "nature": h.nature,
                            "date": h.time_reported.isoformat() if h.time_reported else "",
                        }
                        for h in history
                    ]
                except Exception:
                    logger.warning("Site history lookup failed for %s", doc.address)
                    call_data["site_history"] = []
            else:
                call_data["site_history"] = []

            enriched.append(call_data)

        return enriched


async def get_dashboard_data(*, call_limit: int = 15) -> dict:
    """Fetch all data and return template context for client-side refresh.

    Uses cached NERIS data (Cosmos DB) instead of hitting the NERIS API,
    making this endpoint fast for browser polling.
    """
    dashboard_data, incidents_data = await asyncio.gather(
        _get_dashboard_cached(call_limit=call_limit),
        incident_tools.list_incidents(),
        return_exceptions=True,
    )
    if isinstance(dashboard_data, BaseException):
        logger.exception("Dashboard data fetch failed", exc_info=dashboard_data)
        dashboard_data = {"timestamp": datetime.now(UTC).isoformat(), "user": {}}
    if isinstance(incidents_data, BaseException):
        logger.warning("Incidents unavailable: %s", incidents_data)
        incidents_data = {"incidents": []}
    return _build_template_context(dashboard_data, incidents_data)


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
    (
        calls_result,
        schedule_result,
        incidents_result,
        neris_result,
        turnout_result,
    ) = await asyncio.gather(
        _fetch_recent_calls(),
        _fetch_schedule(),
        _fetch_incidents(user.email, user.is_editor),
        _read_neris_cache(),
        _fetch_fastest_enroute(),
        return_exceptions=True,
    )

    elapsed = (datetime.now(UTC) - t0).total_seconds()
    labels = ["dispatch", "schedule", "incidents", "neris_cache", "turnout"]
    statuses = [
        "err" if isinstance(r, BaseException) else "ok"
        for r in (calls_result, schedule_result, incidents_result, neris_result, turnout_result)
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
            "is_editor": user.is_editor,
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
                "short_dsc": call.analysis.short_dsc,
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
                "short_dsc": "",
                "report": nr,
                "is_completed": True,
            }
            for nr in neris_lookup.values()
        )

        result["recent_calls"] = recent_calls
        result["call_count"] = len(calls_result)  # dispatch calls only

    # --- Fastest turnout ---
    if isinstance(turnout_result, BaseException):
        logger.warning("Dashboard: turnout fetch failed: %s", turnout_result)
    elif turnout_result:
        result["fastest_turnout"] = turnout_result

    return result


async def _get_dashboard_cached(*, call_limit: int = 15) -> dict:
    """Like ``get_dashboard()`` but reads NERIS from cache (Cosmos only).

    Used by ``get_dashboard_data()`` (browser endpoint) to avoid the
    ~2 s NERIS API call on every page load.
    """
    user = get_current_user()
    t0 = datetime.now(UTC)

    (
        calls_result,
        schedule_result,
        incidents_result,
        neris_result,
        turnout_result,
    ) = await asyncio.gather(
        _fetch_recent_calls(limit=call_limit),
        _fetch_schedule(),
        _fetch_incidents(user.email, user.is_editor),
        _read_neris_cache(),
        _fetch_fastest_enroute(),
        return_exceptions=True,
    )

    elapsed = (datetime.now(UTC) - t0).total_seconds()
    labels = ["dispatch", "schedule", "incidents", "neris_cache", "turnout"]
    statuses = [
        "err" if isinstance(r, BaseException) else "ok"
        for r in (calls_result, schedule_result, incidents_result, neris_result, turnout_result)
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
            "is_editor": user.is_editor,
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
                "short_dsc": call.analysis.short_dsc,
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
                "short_dsc": "",
                "report": nr,
                "is_completed": True,
            }
            for nr in neris_lookup.values()
        )

        result["recent_calls"] = recent_calls
        result["call_count"] = len(calls_result)

    # --- Fastest turnout ---
    if isinstance(turnout_result, BaseException):
        logger.warning("Dashboard cached: turnout fetch failed: %s", turnout_result)
    elif turnout_result:
        result["fastest_turnout"] = turnout_result

    return result


async def _fetch_recent_calls(*, limit: int = 15):
    """Fetch recent dispatch calls from Cosmos DB."""
    async with DispatchStore() as store:
        return await store.list_recent(limit=limit)


async def _fetch_fastest_enroute(*, unit: str = "E31", limit: int = 200) -> dict | None:
    """Find the fastest enroute time (page → enroute) for a unit in recent calls.

    Returns dict with seconds, display, call nature/date/id, or None if no data.
    """
    async with DispatchStore() as store:
        calls = await store.list_recent(limit=limit)

    best: dict | None = None
    best_seconds = float("inf")

    for call in calls:
        if not call.analysis or not call.analysis.unit_times:
            continue
        alarm = call.analysis.alarm_time
        if not alarm:
            continue

        for ut in call.analysis.unit_times:
            if ut.unit != unit or not ut.enroute:
                continue
            # Use unit paged time if available, otherwise alarm time
            paged = ut.paged or alarm
            try:
                t_paged = datetime.fromisoformat(paged)
                t_enroute = datetime.fromisoformat(ut.enroute)
                delta = (t_enroute - t_paged).total_seconds()
            except (ValueError, TypeError):
                continue
            if delta <= 0 or delta > 600:
                # Skip bogus data (negative or > 10 min)
                continue
            if delta < best_seconds:
                best_seconds = delta
                mins = int(delta) // 60
                secs = int(delta) % 60
                call_date = ""
                call_time = ""
                if call.time_reported:
                    d = call.time_reported.astimezone(get_timezone())
                    call_date = f"{d.strftime('%b')} {d.day}"
                    call_time = d.strftime("%H:%M")
                best = {
                    "unit": unit,
                    "seconds": int(delta),
                    "display": f"{mins}:{secs:02d}",
                    "nature": call.nature,
                    "date": call_date,
                    "time": call_time,
                    "dispatch_id": call.long_term_call_id,
                }

    return best


async def _fetch_schedule():
    """Fetch today's on-duty crew via the existing schedule tool."""
    return await schedule_tools.get_on_duty_crew()


async def _fetch_incidents(user_email: str, is_editor: bool) -> dict[str, dict]:
    """Fetch non-submitted incidents and build dispatch_id -> report info lookup."""
    async with IncidentStore() as store:
        if is_editor:
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


async def _read_neris_cache() -> dict:
    """Read NERIS reports from Cosmos DB cache (read-only).

    The cache is populated by the Container Apps Job (``ops-tasks neris-sync``).
    The dashboard never touches the NERIS API directly.
    """
    from sjifire.ops.neris.store import NerisReportStore

    async with NerisReportStore() as store:
        return await store.list_as_lookup()
