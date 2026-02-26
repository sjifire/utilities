"""Tools for incident management.

Provides CRUD operations with role-based access control:
- Any authenticated user can create incidents
- Creator and personnel can view their incidents
- Editors (Entra group) can view all incidents and submit to NERIS
- Only creator and editors can edit incidents

NERIS-specific functions (parsing, diffing, patching, import/export) live in
``neris.py`` and are re-exported from this module for backward compatibility.
"""

import asyncio
import contextlib
import html
import logging
import re
from datetime import UTC, datetime

from sjifire.core.config import get_org_config, get_timezone, to_utc_iso
from sjifire.ops.auth import check_is_editor, get_current_user
from sjifire.ops.incidents.models import (
    ALARM_INFO_KEYS,
    FIRE_DETAIL_KEYS,
    HAZARD_INFO_KEYS,
    AlarmInfo,
    DispatchNote,
    EditEntry,
    FireDetail,
    HazardInfo,
    IncidentDocument,
    PersonnelAssignment,
    UnitAssignment,
)
from sjifire.ops.incidents.store import IncidentStore

logger = logging.getLogger(__name__)

_EDITABLE_STATUSES = {"draft", "in_progress", "ready_review"}
_LOCKED_STATUSES = {"submitted", "approved"}
_RESETTABLE_STATUSES = {"draft", "in_progress"}


def _extract_timestamps(responder_details: list[dict]) -> dict[str, str]:
    """Extract NERIS event timestamps from dispatch responder details.

    Maps iSpyFire responder status changes to NERIS timestamp fields:
    - time_reported → psap_answer (from call creation)
    - First "Paged" for SJF3/SJF2 → alarm_time (agency page)
    - First "Enroute" → first_unit_enroute
    - First "On Scene" → first_unit_arrived

    Timestamps are converted to UTC ISO format at extraction time so
    that naive local timestamps (from iSpyFire) are never stored
    without timezone context.

    Args:
        responder_details: List of responder status dicts from dispatch

    Returns:
        Dict of NERIS timestamp field → UTC ISO datetime string
    """
    timestamps: dict[str, str] = {}
    status_map = {
        "ENRT": "first_unit_enroute",
        "ARRVD": "first_unit_arrived",
    }

    for detail in responder_details:
        status = detail.get("status", "")
        time_str = detail.get("time_of_status_change", "")
        unit = detail.get("unit_number", "")
        if not status or not time_str:
            continue

        # Agency page (SJF3 or SJF2 paged) → alarm_time
        if status == "PAGED" and unit in ("SJF3", "SJF2"):
            if "alarm_time" not in timestamps:
                timestamps["alarm_time"] = to_utc_iso(str(time_str))
            continue

        neris_field = status_map.get(status)
        if neris_field and neris_field not in timestamps:
            timestamps[neris_field] = to_utc_iso(str(time_str))

    return timestamps


def _extract_unit_times(responder_details: list[dict]) -> dict[str, dict[str, str]]:
    """Extract per-unit timestamps from dispatch responder details.

    Timestamps are converted to UTC ISO format at extraction time.

    Returns a dict of unit_id → {dispatch, enroute, on_scene, cleared, ...}.
    """
    unit_times: dict[str, dict[str, str]] = {}
    status_map = {
        "PAGED": "dispatch",
        "ENRT": "enroute",
        "ARSTN": "staged",
        "ARRNL": "staged",
        "ARRVD": "on_scene",
        "CMPLT": "cleared",
        "RTQ": "in_quarters",
    }

    for detail in responder_details:
        unit = detail.get("unit_number", "")
        status = detail.get("status", "")
        time_str = detail.get("time_of_status_change", "")
        if not unit or not status or not time_str:
            continue
        # Skip agency paging units
        if unit in ("SJF3", "SJF2"):
            continue

        field = status_map.get(status)
        if not field:
            continue

        if unit not in unit_times:
            unit_times[unit] = {}
        # Keep earliest time for each field
        if field not in unit_times[unit]:
            unit_times[unit][field] = to_utc_iso(str(time_str))

    return unit_times


# Pattern for CAD comment timestamps like "18:56:01 02/02/2026 - M Rennick"
_CAD_TIMESTAMP_RE = re.compile(r"^(\d{1,2}:\d{2}:\d{2}\s+\d{1,2}/\d{1,2}/\d{4})\s*-\s*(.+)$")


def _parse_cad_comments(cad_comments: str, call_ts: str = "") -> list[DispatchNote]:
    """Split a CAD comments blob into individual timestamped notes.

    The iSpyFire ``cad_comments`` field joins all dispatcher comments
    with newlines.  Timestamped entries look like::

        18:56:01 02/02/2026 - M Rennick
        2 calls from on site. advise false alarm.
        18:57:30 02/02/2026 - M Rennick
        good codes from on site per alarm company

    Lines before the first timestamp are the initial caller narrative.

    Returns one ``DispatchNote`` per timestamp block, plus an initial
    note for the caller narrative (if present).
    """
    if not cad_comments:
        return []

    # iSpyFire stores HTML-encoded text (e.g. &#x27; for apostrophes)
    cad_comments = html.unescape(cad_comments)
    lines = cad_comments.split("\n")
    notes: list[DispatchNote] = []
    current_ts = call_ts
    current_lines: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        match = _CAD_TIMESTAMP_RE.match(line)
        if match:
            # Flush previous block
            if current_lines:
                notes.append(
                    DispatchNote(
                        timestamp=current_ts,
                        text=" ".join(current_lines),
                    )
                )
                current_lines = []

            # Parse timestamp: "18:56:01 02/02/2026" → ISO format
            raw_ts = match.group(1).strip()
            try:
                dt = datetime.strptime(raw_ts, "%H:%M:%S %m/%d/%Y")
                tz = get_timezone()
                dt = dt.replace(tzinfo=tz)
                current_ts = dt.isoformat()
            except ValueError:
                current_ts = raw_ts
        else:
            current_lines.append(line)

    # Flush final block
    if current_lines:
        notes.append(
            DispatchNote(
                timestamp=current_ts,
                text=" ".join(current_lines),
            )
        )

    return notes


def _extract_dispatch_notes(responder_details: list[dict]) -> list[DispatchNote]:
    """Extract individual NOTE entries from dispatch responder details.

    Filters to ``NOTE`` status entries and merges continuation lines
    (lines starting with ``+ ``) into the previous note's text.

    Args:
        responder_details: List of responder status dicts from dispatch

    Returns:
        List of DispatchNote objects sorted chronologically (earliest first)
    """
    raw_notes: list[dict] = []
    for detail in responder_details:
        if detail.get("status") != "NOTE":
            continue
        text = html.unescape(detail.get("radio_log", "")).strip()
        ts = detail.get("time_of_status_change", "")
        unit = detail.get("unit_number", "")
        if text:
            raw_notes.append({"timestamp": str(ts), "unit": unit, "text": text})

    # Sort chronologically (earliest first)
    raw_notes.sort(key=lambda n: n["timestamp"])

    # Merge continuation lines (text starting with "+ ")
    merged: list[DispatchNote] = []
    for note in raw_notes:
        if note["text"].startswith("+ ") and merged:
            # Append continuation text to previous note (strip the "+ " prefix)
            merged[-1].text += " " + note["text"][2:].strip()
        else:
            merged.append(DispatchNote(**note))

    return merged


async def _prefill_from_dispatch(incident_number: str) -> dict:
    """Look up dispatch data and return pre-fill fields for an incident.

    Both ``create_incident`` and ``reset_incident`` call this to populate
    address, coordinates, timestamps, and unit shells from dispatch records.

    Args:
        incident_number: Dispatch ID (e.g. "26-000944")

    Returns:
        Dict of pre-fill field values, or empty dict if dispatch not found
    """
    from sjifire.ops.dispatch.store import DispatchStore

    try:
        async with DispatchStore() as store:
            dispatch = await store.get_by_dispatch_id(incident_number)
    except Exception:
        logger.warning("Failed to look up dispatch for %s", incident_number, exc_info=True)
        return {}

    if dispatch is None:
        return {}

    prefill: dict = {}

    if dispatch.address:
        prefill["address"] = dispatch.address
    if dispatch.city:
        prefill["city"] = dispatch.city
    if dispatch.state:
        prefill["state"] = dispatch.state

    # Parse geo_location "lat,lon" string
    if dispatch.geo_location and "," in dispatch.geo_location:
        parts = dispatch.geo_location.split(",")
        try:
            prefill["latitude"] = float(parts[0].strip())
            prefill["longitude"] = float(parts[1].strip())
        except (ValueError, IndexError):
            pass

    # Extract incident-level timestamps from responder details
    ts = _extract_timestamps(dispatch.responder_details)
    if dispatch.time_reported:
        ts["psap_answer"] = to_utc_iso(dispatch.time_reported.isoformat())
    if ts:
        prefill["timestamps"] = ts

    # Extract per-unit timestamps to build unit shells, sorted by enroute time
    unit_times = _extract_unit_times(dispatch.responder_details)
    if unit_times:
        units = []
        for unit_id, times in unit_times.items():
            units.append(UnitAssignment(unit_id=unit_id, **times))
        # Sort by enroute time (earliest first), falling back to dispatch time
        units.sort(key=lambda u: u.enroute or u.dispatch or "\xff")
        prefill["units"] = units

    # Snapshot dispatch comments (plain string from iSpyFire JoinedComments).
    # iSpyFire HTML-encodes text (&#x27; for apostrophes, etc.) — decode here.
    if dispatch.cad_comments:
        prefill["dispatch_comments"] = html.unescape(dispatch.cad_comments)

    # Extract individual NOTE entries for NERIS dispatch.comments
    notes = _extract_dispatch_notes(dispatch.responder_details)

    # Parse cad_comments into individual timestamped notes.
    # The cad_comments blob contains multiple dispatcher entries
    # separated by timestamp lines; split them so each gets its
    # own NERIS dispatch.comment entry.
    if dispatch.cad_comments:
        caller_ts = ""
        if dispatch.time_reported:
            caller_ts = dispatch.time_reported.isoformat()
        cad_notes = _parse_cad_comments(dispatch.cad_comments, call_ts=caller_ts)
        notes = cad_notes + notes

    if notes:
        prefill["dispatch_notes"] = notes

    return prefill


async def _get_crew_for_incident(incident_dt: datetime) -> list[dict]:
    """Look up who was on duty at the time of an incident.

    Uses the schedule store directly (no auth wrapper) to fetch
    crew entries covering the incident datetime.

    Args:
        incident_dt: When the incident occurred

    Returns:
        List of crew dicts with name, position, section, start_time, end_time.
        Empty list if schedule data is unavailable.
    """
    from sjifire.ops.schedule.store import ScheduleStore

    try:
        async with ScheduleStore() as store:
            entries = await store.get_for_time(incident_dt)
    except Exception:
        logger.warning("Failed to look up schedule for %s", incident_dt, exc_info=True)
        return []

    return [
        {
            "name": e.name,
            "position": e.position,
            "section": e.section,
            "start_time": e.start_time,
            "end_time": e.end_time,
        }
        for e in entries
    ]


def _build_import_comparison(
    neris_prefill: dict,
    dispatch_prefill: dict,
    crew: list[dict],
    neris_record: dict | None = None,
) -> dict:
    """Compare NERIS, dispatch, and schedule data to find discrepancies.

    Returns a structured comparison that the assistant can present to the
    user, highlighting differences and gaps filled from each source.

    Args:
        neris_prefill: Parsed NERIS data (from ``_parse_neris_record``)
        dispatch_prefill: Parsed dispatch data (from ``_prefill_from_dispatch``)
        crew: On-duty crew list (from ``_get_crew_for_incident``)
        neris_record: Raw NERIS record for extra context (optional)

    Returns:
        Dict with ``sources``, ``discrepancies``, ``gaps_filled``,
        ``crew_on_duty``, and ``neris_data`` sections.
    """
    comparison: dict = {
        "sources": {
            "neris": bool(neris_prefill),
            "dispatch": bool(dispatch_prefill),
            "schedule": bool(crew),
        },
        "discrepancies": [],
        "gaps_filled": [],
        "crew_on_duty": crew,
    }

    discrepancies = comparison["discrepancies"]
    gaps = comparison["gaps_filled"]

    neris_ts = neris_prefill.get("timestamps", {})
    dispatch_ts = dispatch_prefill.get("timestamps", {})

    # Compare timestamps between NERIS and dispatch.
    # Convert both to local time for display so discrepancies are obvious.
    local_tz = get_timezone()

    def _to_local(iso_str: str) -> tuple[datetime | None, str]:
        """Parse ISO timestamp and return (aware datetime, local display string)."""
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            local_dt = dt.astimezone(local_tz)
            return local_dt, local_dt.strftime("%H:%M:%S %Z")
        except (ValueError, TypeError):
            return None, iso_str

    for ts_key, label in [
        ("psap_answer", "PSAP answer / call creation"),
        ("alarm_time", "Alarm (agency paged)"),
        ("first_unit_enroute", "First unit enroute"),
        ("first_unit_arrived", "First unit on scene"),
        ("incident_clear", "Incident clear"),
    ]:
        neris_val = neris_ts.get(ts_key)
        dispatch_val = dispatch_ts.get(ts_key)

        if neris_val and dispatch_val:
            neris_dt, neris_disp = _to_local(neris_val)
            dispatch_dt, dispatch_disp = _to_local(dispatch_val)
            # Compare actual times (>60s difference = discrepancy)
            if neris_dt and dispatch_dt:
                diff_s = abs((neris_dt - dispatch_dt).total_seconds())
                if diff_s > 60:
                    discrepancies.append(
                        {
                            "field": ts_key,
                            "label": label,
                            "neris": neris_disp,
                            "dispatch": dispatch_disp,
                            "diff": f"{int(diff_s // 60)}m {int(diff_s % 60)}s",
                            "used": "dispatch",
                        }
                    )
            elif neris_val != dispatch_val:
                discrepancies.append(
                    {
                        "field": ts_key,
                        "label": label,
                        "neris": neris_disp,
                        "dispatch": dispatch_disp,
                        "used": "dispatch",
                    }
                )
        elif dispatch_val and not neris_val:
            _, disp = _to_local(dispatch_val)
            gaps.append({"field": ts_key, "label": label, "source": "dispatch", "time": disp})
        elif neris_val and not dispatch_val:
            _, disp = _to_local(neris_val)
            gaps.append({"field": ts_key, "label": label, "source": "neris", "time": disp})

    # Compare addresses
    neris_addr = neris_prefill.get("address", "")
    dispatch_addr = dispatch_prefill.get("address", "")
    if neris_addr and dispatch_addr and neris_addr != dispatch_addr:
        discrepancies.append(
            {
                "field": "address",
                "label": "Incident address",
                "neris": neris_addr,
                "dispatch": dispatch_addr,
                "used": "neris",
                "note": "NERIS address may be corrected; dispatch address is from CAD.",
            }
        )

    # Compare unit counts
    neris_units = neris_prefill.get("units", [])
    dispatch_units = dispatch_prefill.get("units", [])
    if neris_units and dispatch_units:
        neris_unit_ids = [u.unit_id for u in neris_units]
        dispatch_unit_ids = [u.unit_id for u in dispatch_units]
        if set(neris_unit_ids) != set(dispatch_unit_ids):
            discrepancies.append(
                {
                    "field": "units",
                    "label": "Responding units",
                    "neris": neris_unit_ids,
                    "dispatch": dispatch_unit_ids,
                    "note": (
                        "NERIS and dispatch list different units. NERIS may use "
                        "NERIS-registered unit IDs (e.g. FD53055879S001U005) while "
                        "dispatch uses local codes (e.g. E31). Dispatch units are "
                        "used as the baseline."
                    ),
                }
            )
    elif dispatch_units and not neris_units:
        gaps.append(
            {
                "field": "units",
                "label": "Responding units",
                "source": "dispatch",
                "count": len(dispatch_units),
            }
        )

    # Note what NERIS provides that dispatch doesn't
    if neris_prefill.get("incident_type"):
        gaps.append(
            {
                "field": "incident_type",
                "label": "Incident type classification",
                "source": "neris",
                "value": neris_prefill["incident_type"],
            }
        )
    if neris_prefill.get("narrative"):
        gaps.append(
            {
                "field": "narrative",
                "label": "Outcome narrative",
                "source": "neris",
            }
        )

    # Crew from schedule
    if crew:
        gaps.append(
            {
                "field": "crew",
                "label": "On-duty crew roster",
                "source": "schedule",
                "count": len(crew),
            }
        )

    # Include dispatch enrichment data if available
    if dispatch_prefill.get("dispatch_comments"):
        gaps.append(
            {
                "field": "dispatch_comments",
                "label": "CAD comments / radio log",
                "source": "dispatch",
            }
        )

    # Stash NERIS-specific data the user might want to review or update later
    if neris_record:
        neris_dispatch = neris_record.get("dispatch") or {}
        status_info = neris_record.get("incident_status") or {}
        comparison["neris_data"] = {
            "neris_id": neris_record.get("neris_id", ""),
            "status": status_info.get("status", ""),
            "incident_number": neris_dispatch.get("incident_number", ""),
            "call_create": neris_dispatch.get("call_create", ""),
        }

    return comparison


async def _check_view_access(doc: IncidentDocument, user_email: str, is_editor: bool) -> bool:
    """Check if user can view this incident (live Graph API editor check)."""
    if doc.created_by == user_email or user_email in doc.personnel_emails():
        return True
    try:
        user = get_current_user()
        return await check_is_editor(user.user_id, fallback=is_editor, email=user.email)
    except RuntimeError:
        return is_editor


async def _check_edit_access(doc: IncidentDocument, user_email: str, is_editor: bool) -> bool:
    """Check if user can edit this incident (live Graph API editor check)."""
    if doc.created_by == user_email:
        return True
    try:
        user = get_current_user()
        return await check_is_editor(user.user_id, fallback=is_editor, email=user.email)
    except RuntimeError:
        return is_editor


def _parse_units(raw: list[dict]) -> list[UnitAssignment]:
    """Parse raw unit dicts (from tool args) into UnitAssignment objects."""
    units = []
    for u in raw:
        personnel = [
            PersonnelAssignment(
                name=p["name"] if isinstance(p, dict) else p,
                email=p.get("email") if isinstance(p, dict) else None,
                rank=p.get("rank", "") if isinstance(p, dict) else "",
                position=p.get("position", "") if isinstance(p, dict) else "",
                role=p.get("role", "") if isinstance(p, dict) else "",
            )
            for p in u.get("personnel", [])
        ]
        units.append(
            UnitAssignment(
                unit_id=u.get("unit_id", ""),
                response_mode=u.get("response_mode", ""),
                personnel=personnel,
                dispatch=u.get("dispatch", ""),
                enroute=u.get("enroute", ""),
                staged=u.get("staged", ""),
                on_scene=u.get("on_scene", ""),
                cleared=u.get("cleared", ""),
                canceled=u.get("canceled", ""),
                in_quarters=u.get("in_quarters", ""),
                comment=u.get("comment", ""),
            )
        )
    return units


async def create_incident(
    incident_number: str,
    incident_date: str,
    station: str,
    *,
    incident_type: str | None = None,
    address: str | None = None,
    crew: list[dict] | None = None,
    neris_id: str | None = None,
) -> dict:
    """Create a new draft incident report.

    Starts a new incident in "draft" status. The authenticated user is
    automatically recorded as the creator.

    Args:
        incident_number: Incident number (e.g., "26-000944")
        incident_date: Date of the incident in YYYY-MM-DD format
        station: Station code (e.g., "S31")
        incident_type: NERIS incident type code (optional)
        address: Incident address (optional)
        crew: List of crew members, each with "name", "email" (optional),
              "rank" (optional, snapshotted at incident time),
              "position" (optional), "unit" (optional),
              "role" (optional: "officer", "driver", or "officer/driver")
        neris_id: NERIS compound incident ID to import data from (optional)

    Returns:
        The created incident document with its ID
    """
    user = get_current_user()

    # Check for duplicate incident number
    async with IncidentStore() as store:
        existing = await store.get_by_number(incident_number)
    if existing is not None:
        return {
            "error": f"An incident report for {incident_number} already exists "
            f"(status: {existing.status}, created by {existing.created_by}). "
            f"Use get_incident to view it.",
            "existing_id": existing.id,
        }

    # Pre-fill from dispatch data (address, coordinates, timestamps, units)
    dispatch_prefill = await _prefill_from_dispatch(incident_number)

    # If a NERIS ID was provided, fetch NERIS data and build comparison
    neris_prefill: dict = {}
    neris_record: dict | None = None
    comparison: dict | None = None
    if neris_id:
        try:
            neris_record = await asyncio.to_thread(_get_neris_incident, neris_id)
        except Exception:
            logger.warning("Failed to fetch NERIS incident %s", neris_id, exc_info=True)
        if neris_record:
            neris_prefill = _parse_neris_record(neris_record, neris_id)

    # Merge: dispatch base, NERIS overlay (NERIS wins for shared keys)
    prefill = {**dispatch_prefill}
    if neris_prefill:
        # NERIS overwrites dispatch for keys it provides
        prefill.update(neris_prefill)
        # But for timestamps, dispatch is ground truth — NERIS fills gaps only
        dispatch_ts = dispatch_prefill.get("timestamps", {})
        neris_ts = neris_prefill.get("timestamps", {})
        merged_ts = {**neris_ts, **dispatch_ts}  # dispatch overwrites NERIS
        if merged_ts:
            prefill["timestamps"] = merged_ts

    # Fetch schedule data for cross-referencing when NERIS ID is present
    schedule_crew: list[dict] = []
    if neris_id:
        dt_for_schedule = datetime.strptime(incident_date, "%Y-%m-%d").replace(tzinfo=UTC)
        # Try to get more precise time from dispatch or NERIS
        for ts_source in (dispatch_prefill, neris_prefill):
            psap = ts_source.get("timestamps", {}).get("psap_answer")
            if psap:
                try:
                    dt_for_schedule = datetime.fromisoformat(psap)
                    break
                except ValueError:
                    pass  # Invalid ISO timestamp — try next source
        schedule_crew = await _get_crew_for_incident(dt_for_schedule)

        comparison = _build_import_comparison(
            neris_prefill, dispatch_prefill, schedule_crew, neris_record
        )

    # Build units from prefill, then overlay crew assignments
    units = prefill.get("units", [])
    if crew:
        # Group crew by unit
        crew_by_unit: dict[str, list[PersonnelAssignment]] = {}
        for c in crew:
            p = PersonnelAssignment(
                name=c["name"],
                email=c.get("email"),
                rank=c.get("rank", ""),
                position=c.get("position", ""),
                role=c.get("role", ""),
            )
            unit_id = c.get("unit", "")
            crew_by_unit.setdefault(unit_id, []).append(p)

        # Assign personnel to existing units or create new ones
        existing_unit_ids = {u.unit_id for u in units}
        for unit_id, personnel in crew_by_unit.items():
            if unit_id in existing_unit_ids:
                for u in units:
                    if u.unit_id == unit_id:
                        u.personnel = personnel
                        break
            elif unit_id:
                units.append(UnitAssignment(unit_id=unit_id, personnel=personnel))
            # Personnel with no unit get added to a catch-all
            else:
                for u in units:
                    if not u.personnel:
                        u.personnel = personnel
                        break
    elif schedule_crew and units:
        # Auto-assign schedule crew when no explicit crew provided
        _overlay_crew_from_schedule(units, schedule_crew)

    # Parse incident_date as datetime (start of day), then refine from
    # dispatch psap_answer if available — gives accurate incident time for
    # crew lookups and NERIS dispatch.call_create.
    dt = datetime.strptime(incident_date, "%Y-%m-%d").replace(tzinfo=UTC)
    psap = prefill.get("timestamps", {}).get("psap_answer")
    if psap:
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(psap)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=get_timezone())
            dt = parsed

    doc = IncidentDocument(
        incident_number=incident_number,
        incident_datetime=dt,
        incident_type=incident_type or prefill.get("incident_type"),
        location_use=prefill.get("location_use"),
        address=address if address is not None else prefill.get("address"),
        city=prefill.get("city", ""),
        state=prefill.get("state", ""),
        latitude=prefill.get("latitude"),
        longitude=prefill.get("longitude"),
        units=units,
        timestamps=prefill.get("timestamps", {}),
        narrative=prefill.get("narrative", ""),
        dispatch_comments=prefill.get("dispatch_comments", ""),
        dispatch_notes=prefill.get("dispatch_notes", []),
        neris_incident_id=prefill.get("neris_incident_id"),
        station=station,
        created_by=user.email,
    )

    async with IncidentStore() as store:
        created = await store.create(doc)

    logger.info("User %s created incident %s", user.email, created.id)
    result = created.model_dump(mode="json")
    if comparison:
        result["import_comparison"] = comparison
    return result


async def get_incident(incident_id: str) -> dict:
    """Get a single incident by ID.

    You can only view incidents you created, are crew on, or if you
    have officer privileges.

    Args:
        incident_id: The incident document ID

    Returns:
        The full incident document, or an error if not found or no access
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return {"error": "Incident not found"}

    if not await _check_view_access(doc, user.email, user.is_editor):
        return {"error": "You don't have access to this incident"}

    result = doc.model_dump(mode="json")
    # Suppress raw dispatch_comments blob when parsed dispatch_notes exist
    # to avoid showing duplicate information to the chat assistant.
    if result.get("dispatch_notes") and "dispatch_comments" in result:
        del result["dispatch_comments"]
    return result


async def list_incidents(
    status: str | None = None,
    station: str | None = None,
) -> dict:
    """List incidents you have access to.

    By default, shows only incomplete incidents (draft, in_progress,
    ready_review) sorted by oldest incident date first. Pass
    status="submitted" to see submitted incidents.

    Returns incidents you created or are assigned as crew. Officers
    see all incidents.

    Args:
        status: Filter by status: "draft", "in_progress", "ready_review",
                or "submitted". When omitted, shows all except submitted.
        station: Filter by station code (optional)

    Returns:
        List of incident summaries with id, number, date, status, and station
    """
    user = get_current_user()

    # When no status filter is specified, exclude submitted incidents
    # so incomplete work surfaces by default.
    exclude_status = "submitted" if status is None else None

    async with IncidentStore() as store:
        if user.is_editor:
            incidents = await store.list_by_status(status, exclude_status=exclude_status)
        else:
            incidents = await store.list_for_user(
                user.email, status=status, exclude_status=exclude_status
            )

    # Filter by station if requested
    if station:
        incidents = [d for d in incidents if d.station == station]

    summaries = [
        {
            "id": doc.id,
            "incident_number": doc.incident_number,
            "incident_datetime": doc.incident_datetime.isoformat(),
            "station": doc.station,
            "status": doc.status,
            "incident_type": doc.incident_type,
            "created_by": doc.created_by,
            "personnel_count": doc.personnel_count(),
            "neris_incident_id": doc.neris_incident_id,
        }
        for doc in incidents
    ]

    return {"incidents": summaries, "count": len(summaries)}


async def update_incident(
    incident_id: str,
    *,
    status: str | None = None,
    incident_type: str | None = None,
    location_use: str | None = None,
    address: str | None = None,
    city: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    crew: list[dict] | None = None,
    outcome_narrative: str | None = None,
    actions_taken_narrative: str | None = None,
    unit_responses: list[dict] | None = None,
    timestamps: dict[str, str] | None = None,
    internal_notes: str | None = None,
    # Actions
    action_taken: str | None = None,
    noaction_reason: str | None = None,
    action_codes: list[str] | None = None,
    # Fire-specific
    arrival_conditions: str | None = None,
    outside_fire_cause: str | None = None,
    outside_fire_acres: float | None = None,
    # Incident details
    additional_incident_types: list[str] | None = None,
    automatic_alarm: bool | None = None,
    narrative: str | None = None,
    # Location
    apt_suite: str | None = None,
    zip_code: str | None = None,
    county: str | None = None,
    # People
    people_present: bool | None = None,
    displaced_count: int | None = None,
    # Typed sub-models
    fire_detail: dict | None = None,
    alarm_info: dict | None = None,
    hazard_info: dict | None = None,
    # Flexible extras
    extras: dict | None = None,
) -> dict:
    """Update fields on an existing incident.

    Only the incident creator and officers can edit. Submitted incidents
    cannot be modified.

    Args:
        incident_id: The incident document ID
        status: New status (draft, in_progress, ready_review)
        incident_type: NERIS incident type code
        location_use: NERIS location use code (e.g., "RESIDENTIAL||MULTI_FAMILY_LOWRISE_DWELLING")
        address: Incident address
        city: City (defaults to Friday Harbor)
        latitude: GPS latitude
        longitude: GPS longitude
        crew: Replace crew list (each entry: name, email, rank, position, unit, role)
        outcome_narrative: What happened
        actions_taken_narrative: What actions were taken
        unit_responses: NERIS apparatus/unit response data (each entry can include
            staged, comment, and response_mode alongside standard timestamps)
        timestamps: Event timestamps (dispatch, on_scene, etc.)
        internal_notes: Internal notes (not sent to NERIS)
        action_taken: "ACTION" or "NOACTION"
        noaction_reason: "CANCELLED", "STAGED_STANDBY", or "NO_INCIDENT_FOUND"
        action_codes: NERIS action_tactic codes
        arrival_conditions: Fire condition on arrival (fire_condition_arrival value)
        outside_fire_cause: Cause of outside fire (fire_cause_out value)
        outside_fire_acres: Estimated acres burned (outside fire only)
        additional_incident_types: Up to 2 additional NERIS incident type codes
        automatic_alarm: Was this call initiated by an automatic alarm?
        narrative: Combined incident narrative (direct, takes precedence over compat params)
        apt_suite: Apartment or suite number
        zip_code: ZIP code
        county: County name
        people_present: Were people present at the incident location?
        displaced_count: Number of people displaced
        fire_detail: Fire detail fields (fire_cause_in, water_supply, etc.)
        alarm_info: Alarm info fields (smoke_alarm_presence, etc.)
        hazard_info: Hazard info fields (electric_hazards, csst_present, etc.)
        extras: Additional fields merged into existing extras dict

    Returns:
        The updated incident document, or an error
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}

        if not await _check_edit_access(doc, user.email, user.is_editor):
            return {"error": "You don't have permission to edit this incident"}

        if doc.status in _LOCKED_STATUSES:
            return {"error": f"Cannot modify a {doc.status} incident"}

        # Apply updates (only non-None values) and track changed fields
        fields_changed: list[str] = []

        if status is not None:
            if status == "submitted":
                return {"error": "Use submit_incident to submit"}
            if status not in _EDITABLE_STATUSES:
                valid = ", ".join(sorted(_EDITABLE_STATUSES))
                return {"error": f"Invalid status '{status}'. Must be one of: {valid}"}
            doc.status = status
            fields_changed.append("status")

        if incident_type is not None:
            doc.incident_type = incident_type
            fields_changed.append("incident_type")
        if location_use is not None:
            doc.location_use = location_use
            fields_changed.append("location_use")
        if address is not None:
            doc.address = address
            fields_changed.append("address")
        if city is not None:
            doc.city = city
            fields_changed.append("city")
        if latitude is not None:
            doc.latitude = latitude
            fields_changed.append("latitude")
        if longitude is not None:
            doc.longitude = longitude
            fields_changed.append("longitude")

        # crew param maps personnel into units
        if crew is not None:
            crew_by_unit: dict[str, list[PersonnelAssignment]] = {}
            for c in crew:
                p = PersonnelAssignment(
                    name=c["name"],
                    email=c.get("email"),
                    rank=c.get("rank", ""),
                    position=c.get("position", ""),
                    role=c.get("role", ""),
                )
                unit_id = c.get("unit", "")
                crew_by_unit.setdefault(unit_id, []).append(p)

            # Update existing units' personnel, create new units if needed
            existing_ids = {u.unit_id for u in doc.units}
            for unit_id, personnel in crew_by_unit.items():
                if unit_id in existing_ids:
                    for u in doc.units:
                        if u.unit_id == unit_id:
                            u.personnel = personnel
                            break
                elif unit_id:
                    doc.units.append(UnitAssignment(unit_id=unit_id, personnel=personnel))
            fields_changed.append("crew")

        # Narrative — direct param takes precedence, then compat params
        if narrative is not None:
            doc.narrative = narrative
            fields_changed.append("narrative")
        elif outcome_narrative is not None or actions_taken_narrative is not None:
            parts = []
            if outcome_narrative is not None:
                parts.append(outcome_narrative)
            elif doc.narrative:
                parts.append(doc.narrative)
            if actions_taken_narrative is not None:
                parts.append(actions_taken_narrative)
            doc.narrative = "\n\n".join(p for p in parts if p)
            fields_changed.append("narrative")

        if unit_responses is not None:
            doc.units = _parse_units(unit_responses)
            fields_changed.append("units")

        if timestamps is not None:
            # Filter out None values — the LLM may send null for timestamps
            clean_ts = {k: v for k, v in timestamps.items() if v is not None}
            doc.timestamps = {**doc.timestamps, **clean_ts}
            fields_changed.append("timestamps")
        if internal_notes is not None:
            doc.internal_notes = internal_notes
            fields_changed.append("internal_notes")

        # Actions
        if action_taken is not None:
            doc.action_taken = action_taken
            fields_changed.append("action_taken")
        if noaction_reason is not None:
            doc.noaction_reason = noaction_reason
            fields_changed.append("noaction_reason")
        if action_codes is not None:
            doc.action_codes = action_codes
            fields_changed.append("action_codes")

        # Fire-specific
        if arrival_conditions is not None:
            doc.arrival_conditions = arrival_conditions
            fields_changed.append("arrival_conditions")
        if outside_fire_cause is not None:
            doc.outside_fire_cause = outside_fire_cause
            fields_changed.append("outside_fire_cause")
        if outside_fire_acres is not None:
            doc.outside_fire_acres = outside_fire_acres
            fields_changed.append("outside_fire_acres")

        # Incident details
        if additional_incident_types is not None:
            doc.additional_incident_types = additional_incident_types
            fields_changed.append("additional_incident_types")
        if automatic_alarm is not None:
            doc.automatic_alarm = automatic_alarm
            fields_changed.append("automatic_alarm")

        # Location
        if apt_suite is not None:
            doc.apt_suite = apt_suite
            fields_changed.append("apt_suite")
        if zip_code is not None:
            doc.zip_code = zip_code
            fields_changed.append("zip_code")
        if county is not None:
            doc.county = county
            fields_changed.append("county")

        # People
        if people_present is not None:
            doc.people_present = people_present
            fields_changed.append("people_present")
        if displaced_count is not None:
            doc.displaced_count = displaced_count
            fields_changed.append("displaced_count")

        # Typed sub-models — merge into existing
        if fire_detail is not None:
            if doc.fire_detail is None:
                doc.fire_detail = FireDetail(**fire_detail)
            else:
                for k, v in fire_detail.items():
                    setattr(doc.fire_detail, k, v)
            fields_changed.append("fire_detail")

        if alarm_info is not None:
            if doc.alarm_info is None:
                doc.alarm_info = AlarmInfo(**alarm_info)
            else:
                for k, v in alarm_info.items():
                    setattr(doc.alarm_info, k, v)
            fields_changed.append("alarm_info")

        if hazard_info is not None:
            if doc.hazard_info is None:
                doc.hazard_info = HazardInfo(**hazard_info)
            else:
                for k, v in hazard_info.items():
                    setattr(doc.hazard_info, k, v)
            fields_changed.append("hazard_info")

        # Extras — route fire/alarm/hazard keys to sub-models, keep rest
        if extras is not None:
            # Extract keys that belong to typed sub-models
            fd_routed = {k: extras.pop(k) for k in list(extras) if k in FIRE_DETAIL_KEYS}
            ai_routed = {k: extras.pop(k) for k in list(extras) if k in ALARM_INFO_KEYS}
            hi_routed = {k: extras.pop(k) for k in list(extras) if k in HAZARD_INFO_KEYS}

            if fd_routed:
                if doc.fire_detail is None:
                    doc.fire_detail = FireDetail(**fd_routed)
                else:
                    for k, v in fd_routed.items():
                        setattr(doc.fire_detail, k, v)
                if "fire_detail" not in fields_changed:
                    fields_changed.append("fire_detail")

            if ai_routed:
                if doc.alarm_info is None:
                    doc.alarm_info = AlarmInfo(**ai_routed)
                else:
                    for k, v in ai_routed.items():
                        setattr(doc.alarm_info, k, v)
                if "alarm_info" not in fields_changed:
                    fields_changed.append("alarm_info")

            if hi_routed:
                if doc.hazard_info is None:
                    doc.hazard_info = HazardInfo(**hi_routed)
                else:
                    for k, v in hi_routed.items():
                        setattr(doc.hazard_info, k, v)
                if "hazard_info" not in fields_changed:
                    fields_changed.append("hazard_info")

            # Merge remaining extras
            if extras:
                doc.extras = {**doc.extras, **extras}
                fields_changed.append("extras")

        # Record edit history
        if fields_changed:
            doc.edit_history.append(
                EditEntry(
                    editor_email=user.email,
                    editor_name=user.name,
                    fields_changed=fields_changed,
                )
            )

        doc.updated_at = datetime.now(UTC)
        updated = await store.update(doc)

    logger.info("User %s updated incident %s: %s", user.email, incident_id, fields_changed)
    return updated.model_dump(mode="json")


async def submit_incident(incident_id: str) -> dict:
    """Validate and submit an incident to NERIS.

    .. deprecated::
        Use ``submit_to_neris`` instead, which transparently creates or
        updates the NERIS record.  This shim delegates to it.

    Args:
        incident_id: The incident document ID

    Returns:
        Submission result with NERIS incident ID on success, or
        validation errors if the data doesn't pass NERIS checks
    """
    from sjifire.ops.incidents import neris as _neris

    return await _neris.submit_to_neris(incident_id)


async def reset_incident(incident_id: str) -> dict:
    """Reset a draft incident so the user can start over.

    Clears all content fields (type, crew, narratives, unit responses,
    notes) and re-populates address/timestamps from dispatch data — the
    same state as initial creation. Identity fields (id, number, date,
    station, creator) are preserved.

    Guards:
    - Only the incident creator or officers can reset
    - Only "draft" or "in_progress" incidents can be reset
    - One reset per user per 24 hours

    Args:
        incident_id: The incident document ID

    Returns:
        The reset incident document, or an error
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}

        if not await _check_edit_access(doc, user.email, user.is_editor):
            return {"error": "You don't have permission to reset this incident"}

        if doc.status not in _RESETTABLE_STATUSES:
            return {
                "error": f"Cannot reset an incident in '{doc.status}' status. "
                f"Only draft or in_progress incidents can be reset."
            }

        # Pre-fill from dispatch (same as creation)
        prefill = await _prefill_from_dispatch(doc.incident_number)

        # Clear content fields (station is preserved as top-level field)
        doc.incident_type = None
        doc.additional_incident_types = []
        doc.automatic_alarm = None
        doc.arrival_conditions = None
        doc.outside_fire_cause = None
        doc.outside_fire_acres = None
        doc.units = prefill.get("units", [])
        doc.narrative = ""
        doc.action_taken = None
        doc.noaction_reason = None
        doc.action_codes = []
        doc.people_present = None
        doc.displaced_count = None
        doc.internal_notes = ""
        doc.fire_detail = None
        doc.alarm_info = None
        doc.hazard_info = None
        doc.extras = {}

        # Apply dispatch pre-fill
        doc.address = prefill.get("address")
        doc.city = prefill.get("city", "")
        doc.state = prefill.get("state", "")
        doc.latitude = prefill.get("latitude")
        doc.longitude = prefill.get("longitude")
        doc.timestamps = prefill.get("timestamps", {})
        doc.dispatch_comments = prefill.get("dispatch_comments", "")
        doc.dispatch_notes = prefill.get("dispatch_notes", [])

        # Reset status to draft
        doc.status = "draft"
        doc.updated_at = datetime.now(UTC)

        # Record reset in edit history
        doc.edit_history.append(
            EditEntry(
                editor_email=user.email,
                editor_name=user.name,
                fields_changed=["reset"],
            )
        )

        updated = await store.update(doc)

    # Clear chat conversation so the assistant starts fresh
    try:
        from sjifire.ops.chat.store import ConversationStore

        async with ConversationStore() as conv_store:
            deleted = await conv_store.delete_by_incident(incident_id)
            if deleted:
                logger.info("Cleared chat history for incident %s", incident_id)
    except Exception:
        logger.warning("Failed to clear chat history for %s", incident_id, exc_info=True)

    logger.info("User %s reset incident %s", user.email, incident_id)
    result = updated.model_dump(mode="json")
    if updated.neris_incident_id:
        result["_reimport_available"] = True
        result["_reimport_hint"] = (
            f"This incident has a linked NERIS record ({updated.neris_incident_id}). "
            "You can re-import data from NERIS using import_from_neris to "
            "pre-fill the report again."
        )
    return result


async def reopen_incident(incident_id: str) -> dict:
    """Reopen a submitted or approved incident, returning it to draft status.

    This does NOT clear content — it only changes the status so the report
    can be edited again.  Use ``reset_incident`` afterward if you also want
    to clear all fields and start from scratch.

    Guards:
    - Editors only (officers)
    - Only submitted or approved incidents can be reopened

    Args:
        incident_id: The incident document ID

    Returns:
        Confirmation with the updated status, or an error
    """
    user = get_current_user()

    if not await check_is_editor(user.user_id, fallback=user.is_editor, email=user.email):
        group = get_org_config().editor_group_name
        return {
            "error": "Only editors can reopen incidents. "
            f"Ask an administrator to add you to the {group} group."
        }

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}

        if doc.status not in _LOCKED_STATUSES:
            return {
                "error": f"Incident is in '{doc.status}' status — only submitted "
                "or approved incidents can be reopened."
            }

        previous_status = doc.status
        doc.status = "draft"
        doc.updated_at = datetime.now(UTC)

        doc.edit_history.append(
            EditEntry(
                editor_email=user.email,
                editor_name=user.name,
                fields_changed=[f"reopened (was {previous_status})"],
            )
        )

        updated = await store.update(doc)

    logger.info(
        "User %s reopened incident %s (%s → draft)",
        user.email,
        incident_id,
        previous_status,
    )
    return {
        "id": updated.id,
        "incident_number": updated.incident_number,
        "status": updated.status,
        "previous_status": previous_status,
        "message": f"Incident reopened (was {previous_status}). You can now edit it or reset it.",
    }


def _overlay_crew_from_schedule(
    units: list[UnitAssignment],
    crew: list[dict],
) -> None:
    """Best-effort assignment of on-duty crew to units.

    Does NOT overwrite existing personnel assignments. Only fills in units
    that have no personnel yet. Career crew from S31 are assigned to the
    first ``*31`` unit (typically E31). Other crew are left unassigned for
    the user to place.
    """
    # Find units with no personnel
    empty_units = [u for u in units if not u.personnel]
    if not empty_units:
        return

    # Career positions that ride together on S31 primary apparatus
    career_positions = {"Captain", "Lieutenant", "Apparatus Operator"}

    # Separate career S31 crew from others
    s31_crew = [
        c
        for c in crew
        if c.get("section", "").startswith("S31") or c.get("position") in career_positions
    ]

    # Find the first *31 unit with no personnel
    primary_31 = next((u for u in empty_units if u.unit_id.endswith("31")), None)
    if primary_31 and s31_crew:
        primary_31.personnel = [
            PersonnelAssignment(
                name=c["name"],
                position=c.get("position", ""),
                role="officer"
                if c.get("position") in ("Captain", "Lieutenant")
                else ("driver" if c.get("position") == "Apparatus Operator" else ""),
            )
            for c in s31_crew
        ]


# Re-export NERIS functions for backward compatibility.
# server.py registers MCP tools as incident_tools.import_from_neris etc.
from sjifire.ops.incidents.neris import (  # noqa: E402, F401
    _address_from_neris_location,
    _build_neris_creation_payload,
    _build_neris_diff,
    _build_neris_patch,
    _get_neris_incident,
    _list_neris_incidents,
    _neris_dispatch_to_cad_number,
    _parse_neris_record,
    _parse_timestamp,
    _patch_neris_incident,
    _prefill_from_neris,
    _submit_to_neris,
    _timestamps_equal,
    finalize_incident,
    get_neris_incident,
    import_from_neris,
    list_neris_incidents,
    submit_to_neris,
    update_neris_incident,
)
