"""Dispatch call enrichment pipeline.

Combines LLM analysis with deterministic data sources (on-duty schedule)
to produce a fully enriched ``DispatchAnalysis``.

- LLM: reads radio logs, extracts IC unit code, summary, actions, outcome
- Code: schedule lookups, IC name resolution (seniority), unit timing
"""

import logging
import re
from datetime import timedelta

from sjifire.core.config import get_org_config
from sjifire.core.schedule import position_sort_key
from sjifire.ops.dispatch.models import (
    CrewOnDuty,
    DispatchAnalysis,
    DispatchCallDocument,
    UnitTiming,
)
from sjifire.ops.schedule.models import ScheduleEntryCache

logger = logging.getLogger(__name__)


async def enrich_dispatch(doc: DispatchCallDocument) -> DispatchAnalysis:
    """Full enrichment pipeline: LLM analysis + deterministic data.

    Combines:
    1. On-duty crew roster (everyone on shift at call time)
    2. LLM analysis with crew context (summary, actions, outcome)
    3. IC name resolution from code (most senior officer in IC chain)
    4. Unit timing extraction (paged/enroute/arrived for SJF3 units)

    Args:
        doc: Dispatch call document to analyze and enrich

    Returns:
        Enriched DispatchAnalysis with timing, crew roster, and IC name
    """
    from sjifire.ops.dispatch.analysis import analyze_dispatch

    # Fetch on-duty crew first so we can give the LLM name context
    entries = await _get_on_duty_entries(doc)
    crew = _build_crew_roster(entries)
    crew_context = _format_crew_context(crew)

    # LLM analysis with crew roster included in the prompt
    analysis = await analyze_dispatch(doc, crew_context)

    # Attach the crew roster
    analysis.on_duty_crew = crew

    # Deterministic: resolve IC unit code to person name via schedule.
    # Only overwrite the LLM's answer if we find a match — preserves
    # the LLM's name as fallback when the schedule lookup can't match.
    resolved_name = _resolve_ic_name(analysis, entries)
    if resolved_name:
        analysis.incident_commander_name = resolved_name

    # Deterministic: extract SJF3 unit timing from responder_details
    _extract_unit_times(doc, analysis)

    return analysis


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------


async def _get_on_duty_entries(
    doc: DispatchCallDocument,
) -> list[ScheduleEntryCache]:
    """Fetch schedule entries for everyone on duty at the call time.

    Ensures the schedule cache is populated (fetching from Aladtec if
    needed) before querying, so older calls get IC names resolved.
    """
    if doc.time_reported is None:
        return []

    try:
        from sjifire.ops.schedule.store import ScheduleStore
        from sjifire.ops.schedule.tools import _ensure_cache

        dt = doc.time_reported
        today_str = dt.strftime("%Y-%m-%d")
        yesterday_str = (dt - timedelta(days=1)).strftime("%Y-%m-%d")

        async with ScheduleStore() as store:
            await _ensure_cache(store, [yesterday_str, today_str])
            return await store.get_for_time(dt)
    except Exception:
        logger.debug("Schedule unavailable for %s", doc.time_reported, exc_info=True)
        return []


def _build_crew_roster(entries: list[ScheduleEntryCache]) -> list[CrewOnDuty]:
    """Build the on-duty crew list from schedule entries.

    Returns:
        Crew roster sorted by position seniority.
    """
    roster = [CrewOnDuty(name=e.name, position=e.position, section=e.section) for e in entries]
    roster.sort(key=lambda c: position_sort_key(c.position))
    return roster


def _format_crew_context(crew: list[CrewOnDuty]) -> str:
    """Format crew roster as text for inclusion in the LLM prompt."""
    if not crew:
        return ""

    lines = ["On-duty crew at call time:"]
    for c in crew:
        pos = f" — {c.position}" if c.position else ""
        lines.append(f"  {c.name}{pos} ({c.section})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# IC name resolution (deterministic, code-based)
# ---------------------------------------------------------------------------


def _resolve_ic_name(
    analysis: DispatchAnalysis,
    entries: list[ScheduleEntryCache],
) -> str:
    """Resolve IC to the most senior officer in the command chain.

    Extracts all unit codes from the IC field (e.g. "BN31 → L31"),
    maps each to a schedule section, and returns the most senior
    officer found. Falls back to empty string.
    """
    if not entries:
        return ""

    ic_codes = _extract_ic_chain(analysis.incident_commander)
    if not ic_codes:
        return ""

    # Collect schedule entries for all IC units
    ic_entries: list[ScheduleEntryCache] = []
    for code in ic_codes:
        ic_entries.extend(_entries_for_unit(code, entries))

    return _most_senior_officer(ic_entries)


def _extract_ic_chain(ic_field: str) -> list[str]:
    """Extract all IC unit codes from the analysis field.

    Handles "BN31", "E31 → BN31", "OPS31 → BN31 → L31", etc.
    """
    if not ic_field:
        return []

    parts = re.split(r"\s*(?:→|->)\s*", ic_field)
    return [p.strip() for p in parts if p.strip()]


def _unit_to_schedule_candidates(unit_code: str) -> list[str]:
    """Return candidate schedule section prefixes for a unit code.

    Chief officer prefixes (BN, CH, OPS) map to "Chief Officer" section.
    Everything else tries the full unit code first (e.g. "FB31"), then
    falls back to "S{station}".

    Args:
        unit_code: Unit code from dispatch, e.g. "BN31", "E31", "FB31"

    Returns:
        List of candidate section prefixes to try (in priority order),
        or empty list for unrecognized unit codes.
    """
    m = re.match(r"^([A-Z]+)(\d+)$", unit_code.upper())
    if not m:
        return []

    prefix, station = m.group(1), m.group(2)

    if prefix in get_org_config().chief_unit_prefixes:
        return ["Chief Officer"]

    full = f"{prefix}{station}"
    station_section = f"S{station}"
    if full == station_section:
        return [station_section]
    return [full, station_section]


def _entries_for_unit(
    unit_code: str,
    entries: list[ScheduleEntryCache],
) -> list[ScheduleEntryCache]:
    """Find schedule entries matching a unit code.

    Tries each candidate section prefix from ``_unit_to_schedule_candidates``
    and returns entries from the first matching section.
    """
    candidates = _unit_to_schedule_candidates(unit_code)
    for candidate in candidates:
        matched = [e for e in entries if e.section.lower().startswith(candidate.lower())]
        if matched:
            return matched
    return []


def _most_senior_officer(entries: list[ScheduleEntryCache]) -> str:
    """Find the most senior officer from a list of schedule entries.

    Uses OFFICER_POSITIONS for filtering and position_sort_key for ordering.
    """
    officer_positions = get_org_config().officer_positions
    officers = [
        e for e in entries if any(pos.lower() in e.position.lower() for pos in officer_positions)
    ]
    if not officers:
        return ""

    officers.sort(key=lambda e: position_sort_key(e.position))
    return officers[0].name


# ---------------------------------------------------------------------------
# Unit timing extraction (deterministic, from responder_details)
# ---------------------------------------------------------------------------

# Status codes from iSpyFire responder_details → timing field names
_STATUS_TO_FIELD: dict[str, str] = {
    "PAGED": "paged",
    "ENRT": "enroute",
    "ARRVD": "arrived",
    "CMPLT": "completed",
    "RTQ": "in_quarters",
}


def _extract_unit_times(
    doc: DispatchCallDocument,
    analysis: DispatchAnalysis,
) -> None:
    """Extract SJF unit timing from responder_details.

    Populates ``analysis.alarm_time``, ``analysis.first_enroute``,
    and ``analysis.unit_times`` by scanning responder_details for
    PAGED, ENRT, ARRVD, and CMPLT statuses from SJF units.
    """
    if not doc.responder_details:
        return

    # Collect first PAGED/ENRT/ARRVD/CMPLT per unit
    unit_data: dict[str, dict[str, str]] = {}

    for entry in doc.responder_details:
        if not entry.get("agency_code", "").startswith("SJF"):
            continue

        unit = entry.get("unit_number", "")
        status = entry.get("status", "")
        ts = entry.get("time_of_status_change", "")
        if not unit or not ts:
            continue

        if unit not in unit_data:
            unit_data[unit] = {}

        field = _STATUS_TO_FIELD.get(status)
        if field is None:
            continue

        # Keep the earliest timestamp per status per unit
        existing = unit_data[unit].get(field, "")
        if not existing or ts < existing:
            unit_data[unit][field] = ts

    if not unit_data:
        return

    # Build per-unit timing list, sorted by enroute time (earliest first).
    # Exclude agency-level entries (SJF3, etc.) — those are page/complete
    # markers, not responding units. Their times feed alarm_time below.
    timings = [
        UnitTiming(
            unit=unit,
            paged=times.get("paged", ""),
            enroute=times.get("enroute", ""),
            arrived=times.get("arrived", ""),
            completed=times.get("completed", ""),
            in_quarters=times.get("in_quarters", ""),
        )
        for unit, times in unit_data.items()
        if not unit.startswith("SJF")
    ]
    timings.sort(key=lambda t: t.enroute or t.arrived or "~")
    analysis.unit_times = timings

    # alarm_time = earliest PAGED across all entries (including agency-level SJF3)
    all_paged = [ts["paged"] for ts in unit_data.values() if ts.get("paged")]
    analysis.alarm_time = min(all_paged) if all_paged else ""

    # first_enroute = earliest ENRT across responding units (not agency entries)
    enroute_times = [t.enroute for t in timings if t.enroute]
    analysis.first_enroute = min(enroute_times) if enroute_times else ""
