"""Tools for incident management.

Provides CRUD operations with role-based access control:
- Any authenticated user can create incidents
- Creator and personnel can view their incidents
- Editors (Entra group) can view all incidents and submit to NERIS
- Only creator and editors can edit incidents

NERIS interaction is only through this module (no separate NERIS tools).
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sjifire.core.config import get_org_config, get_timezone
from sjifire.ops.auth import get_current_user
from sjifire.ops.incidents.models import (
    EditEntry,
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

    Args:
        responder_details: List of responder status dicts from dispatch

    Returns:
        Dict of NERIS timestamp field → ISO datetime string
    """
    timestamps: dict[str, str] = {}
    status_map = {
        "Enroute": "first_unit_enroute",
        "On Scene": "first_unit_arrived",
    }

    for detail in responder_details:
        status = detail.get("status", "")
        time_str = detail.get("time_of_status_change", "")
        unit = detail.get("unit", "")
        if not status or not time_str:
            continue

        # Agency page (SJF3 or SJF2 paged) → alarm_time
        if status in ("Dispatch", "Dispatched", "Paged") and unit in ("SJF3", "SJF2"):
            if "alarm_time" not in timestamps:
                timestamps["alarm_time"] = str(time_str)
            continue

        neris_field = status_map.get(status)
        if neris_field and neris_field not in timestamps:
            timestamps[neris_field] = str(time_str)

    return timestamps


def _extract_unit_times(responder_details: list[dict]) -> dict[str, dict[str, str]]:
    """Extract per-unit timestamps from dispatch responder details.

    Returns a dict of unit_id → {dispatch, enroute, on_scene, cleared, ...}.
    """
    unit_times: dict[str, dict[str, str]] = {}
    status_map = {
        "Dispatch": "dispatch",
        "Dispatched": "dispatch",
        "Enroute": "enroute",
        "On Scene": "on_scene",
        "Complete": "cleared",
        "Returning": "cleared",
        "In Quarters": "in_quarters",
        "Cancelled": "canceled",
    }

    for detail in responder_details:
        unit = detail.get("unit", "")
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
            unit_times[unit][field] = str(time_str)

    return unit_times


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
        ts["psap_answer"] = dispatch.time_reported.isoformat()
    if ts:
        prefill["timestamps"] = ts

    # Extract per-unit timestamps to build unit shells
    unit_times = _extract_unit_times(dispatch.responder_details)
    if unit_times:
        units = []
        for unit_id, times in unit_times.items():
            units.append(UnitAssignment(unit_id=unit_id, **times))
        prefill["units"] = units

    # Snapshot dispatch comments (plain string from iSpyFire JoinedComments)
    if dispatch.cad_comments:
        prefill["dispatch_comments"] = dispatch.cad_comments

    return prefill


def _address_from_neris_location(loc: dict) -> str:
    """Assemble a street address from NERIS granular location fields.

    NERIS locations have separate fields for number, direction prefix,
    street name, and street suffix. This combines them into a single
    address string like "94 Zepher Ln" or "1632 San Juan Rd".
    """
    parts: list[str] = []
    number = loc.get("complete_number") or loc.get("number") or ""
    if number:
        parts.append(str(number).strip())
    for field in ("street_prefix_direction", "street", "street_postfix"):
        val = loc.get(field)
        if val:
            parts.append(str(val).strip())
    return " ".join(parts)


def _parse_neris_record(record: dict, neris_id: str) -> dict:
    """Extract pre-fill fields from a NERIS incident record.

    This is the pure-parsing half of the NERIS prefill pipeline.
    ``_prefill_from_neris`` fetches the record and delegates here.

    Args:
        record: Full NERIS incident record dict
        neris_id: NERIS compound ID for the record

    Returns:
        Dict of pre-fill field values
    """
    prefill: dict = {"neris_incident_id": neris_id}

    # Incident type — first primary type
    types = record.get("incident_types") or []
    if types:
        prefill["incident_type"] = types[0].get("type", "")

    # Outcome narrative
    base = record.get("base") or {}
    if base.get("outcome_narrative"):
        prefill["narrative"] = base["outcome_narrative"]

    # Location use (e.g. "RESIDENTIAL||MANUFACTURED_MOBILE_HOME")
    location_use_obj = base.get("location_use") or {}
    use_type = location_use_obj.get("use_type") or ""
    if use_type:
        prefill["location_use"] = use_type

    # Location — prefer base.location (corrected), fall back to dispatch
    loc = base.get("location") or {}
    if not loc:
        dispatch = record.get("dispatch") or {}
        loc = dispatch.get("location") or {}
    if loc:
        addr = _address_from_neris_location(loc)
        if addr:
            prefill["address"] = addr
        city = loc.get("incorporated_municipality") or ""
        if city:
            prefill["city"] = city
        state = loc.get("state") or ""
        if state:
            prefill["state"] = state

    # Unit responses from dispatch
    dispatch = record.get("dispatch") or {}
    neris_units = dispatch.get("unit_responses") or []
    if neris_units:
        units = []
        for u in neris_units:
            unit = UnitAssignment(
                unit_id=u.get("reported_unit_id") or u.get("unit_neris_id", ""),
                response_mode=u.get("response_mode") or "",
                dispatch=u.get("dispatch") or "",
                enroute=u.get("enroute_to_scene") or "",
                on_scene=u.get("on_scene") or "",
                cleared=u.get("unit_clear") or "",
            )
            units.append(unit)
        prefill["units"] = units

    # Timestamps — from dispatch-level fields and earliest unit times
    timestamps: dict[str, str] = {}
    if dispatch.get("call_create"):
        timestamps["psap_answer"] = dispatch["call_create"]
    if dispatch.get("incident_clear"):
        timestamps["incident_clear"] = dispatch["incident_clear"]

    # Earliest unit enroute/on_scene across all units
    for field, ts_key in [
        ("enroute_to_scene", "first_unit_enroute"),
        ("on_scene", "first_unit_arrived"),
    ]:
        times = [u[field] for u in neris_units if u.get(field)]
        if times:
            timestamps[ts_key] = min(times)

    if timestamps:
        prefill["timestamps"] = timestamps

    # --- Actions & tactics (top-level in NERIS response) ---
    actions_tactics = record.get("actions_tactics") or {}
    action_noaction = actions_tactics.get("action_noaction") or {}
    action_type = action_noaction.get("type")  # "ACTION" or "NOACTION"
    if action_type:
        prefill["action_taken"] = action_type
    if action_type == "NOACTION":
        noaction_type = action_noaction.get("noaction_type")
        if noaction_type:
            prefill["noaction_reason"] = noaction_type
    elif action_type == "ACTION":
        actions = action_noaction.get("actions") or []
        if actions:
            prefill["action_codes"] = [a for a in actions if isinstance(a, str) and a]

    # --- Additional incident types ---
    if len(types) > 1:
        prefill["additional_incident_types"] = [
            t.get("type", "") for t in types[1:] if t.get("type")
        ]

    # --- Fire detail (top-level in NERIS response) ---
    extras: dict = {}
    fire_detail = record.get("fire_detail") or {}
    if fire_detail:
        location_detail = fire_detail.get("location_detail") or {}
        arrival = location_detail.get("arrival_condition")
        if arrival:
            prefill["arrival_conditions"] = arrival
        for fd_key, extras_key in (
            ("damage_type", "fire_bldg_damage"),
            ("room_of_origin_type", "room_of_origin"),
            ("floor_of_origin", "floor_of_origin"),
            ("cause", "fire_cause_in"),
        ):
            val = location_detail.get(fd_key)
            if val is not None:
                extras[extras_key] = val

        # Outside fire: location_detail.type == "OUTSIDE"
        if location_detail.get("type") == "OUTSIDE":
            cause = location_detail.get("cause")
            if cause:
                prefill["outside_fire_cause"] = cause
            acres = location_detail.get("acres_burned")
            if acres is not None:
                prefill["outside_fire_acres"] = acres

        water_supply = fire_detail.get("water_supply")
        if water_supply:
            extras["water_supply"] = water_supply
        investigation = fire_detail.get("investigation_needed")
        if investigation:
            extras["fire_investigation"] = investigation
        inv_types = fire_detail.get("investigation_types")
        if inv_types:
            extras["fire_investigation_types"] = inv_types

    # --- Alarms & suppression (top-level in NERIS response) ---
    for alarm_section, extras_key in (
        ("smoke_alarm", "smoke_alarm_presence"),
        ("fire_alarm", "fire_alarm_presence"),
        ("fire_suppression", "sprinkler_presence"),
    ):
        alarm = record.get(alarm_section) or {}
        presence = alarm.get("presence") or {}
        ptype = presence.get("type")
        if ptype:
            # Map NERIS presence types to our format
            # NERIS uses: PRESENT, NOT_PRESENT, NOT_APPLICABLE
            extras[extras_key] = ptype

    # --- Hazards (top-level in NERIS response) ---
    electric_hazards = record.get("electric_hazards") or []
    if electric_hazards:
        extras["electric_hazards"] = True
    powergen = record.get("powergen_hazards") or []
    for pg in powergen:
        pg_type = pg.get("type") if isinstance(pg, dict) else None
        if pg_type:
            if "SOLAR" in pg_type.upper():
                extras["solar_present"] = "YES"
            elif "BATTERY" in pg_type.upper() or "ESS" in pg_type.upper():
                extras["battery_ess_present"] = "YES"
            elif "GENERATOR" in pg_type.upper():
                extras["generator_present"] = "YES"
    csst = record.get("csst_hazard") or {}
    if csst:
        extras["csst_present"] = "YES" if csst.get("lightning_suspected") else "UNKNOWN"

    # --- People & occupancy (in base) ---
    people_present = base.get("people_present")
    if people_present is not None:
        prefill["people_present"] = people_present
    displaced = base.get("displacement_count")
    if displaced is not None:
        prefill["displaced_count"] = displaced

    # --- Impediment narrative (in base) ---
    impediment = base.get("impediment_narrative")
    if impediment:
        extras["impediment_narrative"] = impediment

    # --- Medical details (top-level in NERIS response) ---
    medical_details = record.get("medical_details") or []
    if medical_details:
        extras["patient_count"] = len(medical_details)
        for i, med in enumerate(medical_details):
            prefix = "" if len(medical_details) == 1 else f"patient_{i + 1}_"
            care = med.get("patient_care_evaluation")
            if care:
                extras[f"{prefix}care_disposition"] = care
            transport = med.get("transport_disposition")
            if transport:
                extras[f"{prefix}transport_disposition"] = transport
            status = med.get("patient_status")
            if status:
                extras[f"{prefix}patient_status"] = status

    # --- Tactic timestamps (top-level in NERIS response) ---
    tactic_ts = record.get("tactic_timestamps") or {}
    for ts_key in (
        "command_established",
        "water_on_fire",
        "fire_under_control",
        "fire_knocked_down",
        "suppression_complete",
        "primary_search_begin",
        "primary_search_complete",
        "extrication_complete",
    ):
        val = tactic_ts.get(ts_key)
        if val:
            timestamps[ts_key] = val if isinstance(val, str) else str(val)

    # --- Casualty/rescue data (top-level in NERIS response) ---
    casualty_rescues = record.get("casualty_rescues") or []
    for cr in casualty_rescues:
        rescue = cr.get("rescue") or {}
        if rescue:
            for rk in ("rescue_mode", "rescue_actions", "rescue_impediment", "rescue_elevation"):
                val = rescue.get(rk)
                if val is not None:
                    extras[rk] = val

    if extras:
        prefill["extras"] = extras

    return prefill


async def _prefill_from_neris(neris_id: str) -> dict:
    """Fetch a NERIS incident and return pre-fill fields for a local draft.

    Extracts incident type, narrative, location, unit responses, and
    timestamps from the NERIS record. Returns an empty dict on any error
    so creation can proceed with dispatch data alone.
    """
    try:
        record = await asyncio.to_thread(_get_neris_incident, neris_id)
    except Exception:
        logger.warning("Failed to fetch NERIS incident %s for prefill", neris_id, exc_info=True)
        return {}

    if not record:
        return {}

    return _parse_neris_record(record, neris_id)


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


def _check_view_access(doc: IncidentDocument, user_email: str, is_editor: bool) -> bool:
    """Check if user can view this incident."""
    return is_editor or doc.created_by == user_email or user_email in doc.personnel_emails()


def _check_edit_access(doc: IncidentDocument, user_email: str, is_editor: bool) -> bool:
    """Check if user can edit this incident."""
    return is_editor or doc.created_by == user_email


def _parse_units(raw: list[dict]) -> list[UnitAssignment]:
    """Parse raw unit dicts (from tool args) into UnitAssignment objects."""
    units = []
    for u in raw:
        personnel = [
            PersonnelAssignment(
                name=p["name"],
                email=p.get("email"),
                rank=p.get("rank", ""),
                position=p.get("position", ""),
                role=p.get("role", ""),
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

    # Parse incident_date as datetime (start of day)
    dt = datetime.strptime(incident_date, "%Y-%m-%d").replace(tzinfo=UTC)

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
        neris_incident_id=prefill.get("neris_incident_id"),
        extras={"station": station},
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

    if not _check_view_access(doc, user.email, user.is_editor):
        return {"error": "You don't have access to this incident"}

    return doc.model_dump(mode="json")


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

    # Filter by station in extras if requested
    if station:
        incidents = [d for d in incidents if d.extras.get("station") == station]

    summaries = [
        {
            "id": doc.id,
            "incident_number": doc.incident_number,
            "incident_datetime": doc.incident_datetime.isoformat(),
            "station": doc.extras.get("station", ""),
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
        extras: Additional fields merged into existing extras dict

    Returns:
        The updated incident document, or an error
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}

        if not _check_edit_access(doc, user.email, user.is_editor):
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

        # Extras — merge into existing
        if extras is not None:
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

    Only officers can submit incidents. The incident must be in
    "ready_review" status. This validates the data with NERIS first,
    then submits if validation passes.

    Args:
        incident_id: The incident document ID

    Returns:
        Submission result with NERIS incident ID on success, or
        validation errors if the data doesn't pass NERIS checks
    """
    user = get_current_user()

    if not user.is_editor:
        group = get_org_config().editor_group_name
        return {
            "error": "You are not authorized to submit incidents to NERIS. "
            f"Ask an administrator to add you to the {group} group in Entra ID."
        }

    # NERIS submission is not yet enabled — district entity ID and API
    # credentials are pending vendor enrollment. Remove this guard once
    # NERIS_ENTITY_ID and NERIS_CLIENT_ID/SECRET are configured.
    return {
        "status": "not_available",
        "message": (
            "NERIS submission is not yet enabled. The incident report has been "
            "saved locally and can be submitted once NERIS API credentials are "
            "configured. Contact the system administrator to complete NERIS "
            "vendor enrollment."
        ),
        "incident_id": incident_id,
    }


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

        if not _check_edit_access(doc, user.email, user.is_editor):
            return {"error": "You don't have permission to reset this incident"}

        if doc.status not in _RESETTABLE_STATUSES:
            return {
                "error": f"Cannot reset an incident in '{doc.status}' status. "
                f"Only draft or in_progress incidents can be reset."
            }

        # Preserve identity fields
        station = doc.extras.get("station", "")

        # Pre-fill from dispatch (same as creation)
        prefill = await _prefill_from_dispatch(doc.incident_number)

        # Clear content fields
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
        doc.extras = {"station": station} if station else {}

        # Apply dispatch pre-fill
        doc.address = prefill.get("address")
        doc.city = prefill.get("city", "")
        doc.state = prefill.get("state", "")
        doc.latitude = prefill.get("latitude")
        doc.longitude = prefill.get("longitude")
        doc.timestamps = prefill.get("timestamps", {})
        doc.dispatch_comments = prefill.get("dispatch_comments", "")

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


async def import_from_neris(
    neris_id: str,
    *,
    incident_id: str | None = None,
    station: str = "S31",
) -> dict:
    """Import a NERIS record, cross-referencing with dispatch and schedule data.

    Creates a new local incident if ``incident_id`` is not provided, or
    updates an existing one. In both cases fetches the NERIS record,
    looks up the corresponding dispatch call, and pulls the on-duty crew
    schedule. Returns the merged incident document together with a
    ``comparison`` section that highlights discrepancies between the
    three data sources and notes which gaps were filled from where.

    The merge strategy:
    - **Dispatch timestamps** are ground truth (real-time CAD data)
    - **NERIS** provides incident classification, narrative, and corrected
      location
    - **Schedule** provides crew assignments

    After reviewing the report, the user can choose to update NERIS with
    any corrections identified during the review.

    Args:
        neris_id: NERIS compound incident ID
            (e.g., "FD53055879|26001980|1770500761")
        incident_id: Existing incident document ID to import into.
            When omitted a new draft is created from the NERIS data.
        station: Station code for new incidents (default "S31",
            ignored when importing into an existing incident)

    Returns:
        The incident document with an ``import_comparison`` key showing
        discrepancies, gaps filled, and crew on duty. Or an error dict.
    """
    user = get_current_user()

    # ── 1. Fetch the full NERIS record ──
    try:
        neris_record = await asyncio.to_thread(_get_neris_incident, neris_id)
    except ValueError as e:
        logger.warning("NERIS credentials not configured: %s", e)
        return {"error": "NERIS API credentials are not configured. Contact an administrator."}
    except Exception:
        logger.warning("Failed to fetch NERIS incident %s", neris_id, exc_info=True)
        return {
            "error": f"Failed to fetch NERIS record '{neris_id}'. The NERIS API may be unavailable."
        }

    if not neris_record:
        return {"error": f"NERIS incident not found: {neris_id}. Verify the NERIS ID is correct."}

    neris_prefill = _parse_neris_record(neris_record, neris_id)
    # Stash NERIS status for downstream hints (not persisted in the document)
    neris_status_info = neris_record.get("incident_status") or {}
    neris_prefill["_neris_status"] = neris_status_info.get("status", "")

    # ── 2. Derive dispatch number and date from NERIS record ──
    neris_dispatch = neris_record.get("dispatch") or {}
    neris_incident_number = neris_dispatch.get("incident_number", "")
    neris_call_create = neris_dispatch.get("call_create", "")

    # ── 3. Fetch dispatch data ──
    # Determine incident number: from existing doc, or from NERIS record
    if incident_id:
        async with IncidentStore() as store:
            doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}
        if not _check_edit_access(doc, user.email, user.is_editor):
            return {"error": "You don't have permission to edit this incident"}
        if doc.status in _LOCKED_STATUSES:
            return {"error": f"Cannot modify a {doc.status} incident"}

        dispatch_number = doc.incident_number
    else:
        dispatch_number = neris_incident_number

    dispatch_prefill: dict = {}
    if dispatch_number:
        dispatch_prefill = await _prefill_from_dispatch(dispatch_number)

    # ── 4. Determine incident datetime for schedule lookup ──
    incident_dt: datetime | None = None
    if incident_id and doc is not None:
        incident_dt = doc.incident_datetime
    elif neris_call_create:
        with contextlib.suppress(ValueError):
            incident_dt = datetime.fromisoformat(neris_call_create)

    # ── 5. Fetch schedule data ──
    crew: list[dict] = []
    if incident_dt:
        crew = await _get_crew_for_incident(incident_dt)

    # ── 6. Build comparison ──
    comparison = _build_import_comparison(neris_prefill, dispatch_prefill, crew, neris_record)

    # ── 7. Merge data and create/update the incident ──
    if incident_id and doc is not None:
        # Update existing incident
        return await _apply_neris_import_to_existing(
            doc, neris_prefill, dispatch_prefill, crew, comparison, user
        )
    else:
        # Create new incident from NERIS
        return await _create_incident_from_neris(
            neris_prefill,
            dispatch_prefill,
            crew,
            comparison,
            neris_incident_number,
            neris_call_create,
            station,
            user,
        )


async def _apply_neris_import_to_existing(
    doc: IncidentDocument,
    neris_prefill: dict,
    dispatch_prefill: dict,
    crew: list[dict],
    comparison: dict,
    user,
) -> dict:
    """Apply merged NERIS + dispatch + schedule data to an existing incident."""
    fields_changed: list[str] = []

    # NERIS ID
    doc.neris_incident_id = neris_prefill.get("neris_incident_id", doc.neris_incident_id)
    fields_changed.append("neris_incident_id")

    # Incident type from NERIS (dispatch doesn't have this)
    if "incident_type" in neris_prefill:
        doc.incident_type = neris_prefill["incident_type"]
        fields_changed.append("incident_type")

    # Location use from NERIS
    if "location_use" in neris_prefill:
        doc.location_use = neris_prefill["location_use"]
        fields_changed.append("location_use")

    # Narrative from NERIS
    if "narrative" in neris_prefill:
        doc.narrative = neris_prefill["narrative"]
        fields_changed.append("narrative")

    # Address — prefer NERIS (may be corrected), fall back to dispatch
    if "address" in neris_prefill:
        doc.address = neris_prefill["address"]
        fields_changed.append("address")
    elif "address" in dispatch_prefill and not doc.address:
        doc.address = dispatch_prefill["address"]
        fields_changed.append("address")
    if "city" in neris_prefill:
        doc.city = neris_prefill["city"]
    elif "city" in dispatch_prefill and not doc.city:
        doc.city = dispatch_prefill["city"]
    if "state" in neris_prefill:
        doc.state = neris_prefill["state"]
    elif "state" in dispatch_prefill and not doc.state:
        doc.state = dispatch_prefill["state"]

    # Coordinates from dispatch (NERIS doesn't provide these)
    if "latitude" in dispatch_prefill and doc.latitude is None:
        doc.latitude = dispatch_prefill["latitude"]
        fields_changed.append("latitude")
    if "longitude" in dispatch_prefill and doc.longitude is None:
        doc.longitude = dispatch_prefill["longitude"]
        fields_changed.append("longitude")

    # Units — prefer dispatch (local unit codes), but keep NERIS response_mode
    if "units" in dispatch_prefill:
        neris_modes = {}
        for u in neris_prefill.get("units", []):
            if u.response_mode:
                neris_modes[u.unit_id] = u.response_mode
        existing_personnel = {u.unit_id: u.personnel for u in doc.units}
        for u in dispatch_prefill["units"]:
            if u.unit_id in existing_personnel:
                u.personnel = existing_personnel[u.unit_id]
        doc.units = dispatch_prefill["units"]
        fields_changed.append("units")
    elif "units" in neris_prefill:
        existing_personnel = {u.unit_id: u.personnel for u in doc.units}
        for u in neris_prefill["units"]:
            if u.unit_id in existing_personnel:
                u.personnel = existing_personnel[u.unit_id]
        doc.units = neris_prefill["units"]
        fields_changed.append("units")

    # Timestamps — dispatch is ground truth, NERIS fills gaps
    merged_ts = {**doc.timestamps}
    neris_ts = neris_prefill.get("timestamps", {})
    dispatch_ts = dispatch_prefill.get("timestamps", {})
    # Dispatch timestamps overwrite everything (ground truth)
    merged_ts.update(dispatch_ts)
    # NERIS timestamps fill remaining gaps only
    for k, v in neris_ts.items():
        if k not in merged_ts:
            merged_ts[k] = v
    if merged_ts != doc.timestamps:
        doc.timestamps = merged_ts
        fields_changed.append("timestamps")

    # Dispatch comments
    if "dispatch_comments" in dispatch_prefill and not doc.dispatch_comments:
        doc.dispatch_comments = dispatch_prefill["dispatch_comments"]
        fields_changed.append("dispatch_comments")

    # Actions & tactics from NERIS
    if "action_taken" in neris_prefill and not doc.action_taken:
        doc.action_taken = neris_prefill["action_taken"]
        fields_changed.append("action_taken")
    if "noaction_reason" in neris_prefill and not doc.noaction_reason:
        doc.noaction_reason = neris_prefill["noaction_reason"]
        fields_changed.append("noaction_reason")
    if "action_codes" in neris_prefill and not doc.action_codes:
        doc.action_codes = neris_prefill["action_codes"]
        fields_changed.append("action_codes")

    # Additional incident types
    if "additional_incident_types" in neris_prefill and not doc.additional_incident_types:
        doc.additional_incident_types = neris_prefill["additional_incident_types"]
        fields_changed.append("additional_incident_types")

    # Fire-specific first-class fields
    if "arrival_conditions" in neris_prefill and not doc.arrival_conditions:
        doc.arrival_conditions = neris_prefill["arrival_conditions"]
        fields_changed.append("arrival_conditions")
    if "outside_fire_cause" in neris_prefill and not doc.outside_fire_cause:
        doc.outside_fire_cause = neris_prefill["outside_fire_cause"]
        fields_changed.append("outside_fire_cause")
    if "outside_fire_acres" in neris_prefill and doc.outside_fire_acres is None:
        doc.outside_fire_acres = neris_prefill["outside_fire_acres"]
        fields_changed.append("outside_fire_acres")

    # People & occupancy
    if "people_present" in neris_prefill and doc.people_present is None:
        doc.people_present = neris_prefill["people_present"]
        fields_changed.append("people_present")
    if "displaced_count" in neris_prefill and doc.displaced_count is None:
        doc.displaced_count = neris_prefill["displaced_count"]
        fields_changed.append("displaced_count")
    if "automatic_alarm" in neris_prefill and doc.automatic_alarm is None:
        doc.automatic_alarm = neris_prefill["automatic_alarm"]
        fields_changed.append("automatic_alarm")

    # Extras (fire module details, alarms, hazards, medical, rescue)
    neris_extras = neris_prefill.get("extras", {})
    if neris_extras:
        merged_extras = {**doc.extras}
        for k, v in neris_extras.items():
            if k not in merged_extras:
                merged_extras[k] = v
        if merged_extras != doc.extras:
            doc.extras = merged_extras
            fields_changed.append("extras")

    # Assign crew from schedule to units
    if crew and doc.units:
        _overlay_crew_from_schedule(doc.units, crew)
        fields_changed.append("crew")

    # Record edit history
    doc.edit_history.append(
        EditEntry(
            editor_email=user.email,
            editor_name=user.name,
            fields_changed=["neris_import"],
        )
    )

    doc.updated_at = datetime.now(UTC)

    async with IncidentStore() as store:
        updated = await store.update(doc)

    logger.info(
        "User %s imported NERIS data into incident %s: %s",
        user.email,
        doc.id,
        fields_changed,
    )
    result = updated.model_dump(mode="json")
    result["import_comparison"] = comparison
    return result


async def _create_incident_from_neris(
    neris_prefill: dict,
    dispatch_prefill: dict,
    crew: list[dict],
    comparison: dict,
    neris_incident_number: str,
    neris_call_create: str,
    station: str,
    user,
) -> dict:
    """Create a new incident from merged NERIS + dispatch + schedule data."""
    # Derive incident number — prefer dispatch format, fall back to NERIS
    incident_number = neris_incident_number
    if not incident_number:
        return {
            "error": "Cannot determine incident number from NERIS record. "
            "The NERIS dispatch section may be incomplete."
        }

    # Check for duplicate
    async with IncidentStore() as store:
        existing = await store.get_by_number(incident_number)
    if existing is not None:
        return {
            "error": f"An incident report for {incident_number} already exists "
            f"(status: {existing.status}, created by {existing.created_by}). "
            f"Use import_from_neris with incident_id='{existing.id}' to "
            f"re-import NERIS data into the existing report.",
            "existing_id": existing.id,
        }

    # Parse incident date
    incident_dt = datetime.now(UTC)
    if neris_call_create:
        with contextlib.suppress(ValueError):
            incident_dt = datetime.fromisoformat(neris_call_create)

    # Merge: dispatch base, NERIS overlay
    # Start with dispatch data as the base
    merged: dict = {**dispatch_prefill}
    # NERIS overwrites for fields it provides
    for key in (
        "incident_type", "narrative", "address", "city", "state",
        "neris_incident_id", "location_use", "action_taken", "noaction_reason",
        "action_codes", "additional_incident_types", "arrival_conditions",
        "outside_fire_cause", "outside_fire_acres", "people_present",
        "displaced_count", "automatic_alarm",
    ):
        if key in neris_prefill:
            merged[key] = neris_prefill[key]
    # For coordinates, dispatch is the only source
    # For timestamps, dispatch is ground truth; NERIS fills gaps
    dispatch_ts = dispatch_prefill.get("timestamps", {})
    neris_ts = neris_prefill.get("timestamps", {})
    merged_ts = {**neris_ts, **dispatch_ts}  # dispatch overwrites NERIS
    if merged_ts:
        merged["timestamps"] = merged_ts

    # Units: prefer dispatch (local codes), fall back to NERIS
    if "units" not in merged and "units" in neris_prefill:
        merged["units"] = neris_prefill["units"]

    units = merged.get("units", [])

    # Overlay crew from schedule
    if crew and units:
        _overlay_crew_from_schedule(units, crew)

    # Merge extras: station + any NERIS extras (fire module, alarms, hazards, etc.)
    merged_extras = {"station": station}
    neris_extras = neris_prefill.get("extras", {})
    merged_extras.update(neris_extras)

    doc = IncidentDocument(
        incident_number=incident_number,
        incident_datetime=incident_dt,
        incident_type=merged.get("incident_type"),
        location_use=merged.get("location_use"),
        address=merged.get("address"),
        city=merged.get("city", ""),
        state=merged.get("state", ""),
        latitude=merged.get("latitude"),
        longitude=merged.get("longitude"),
        units=units,
        timestamps=merged.get("timestamps", {}),
        narrative=merged.get("narrative", ""),
        dispatch_comments=merged.get("dispatch_comments", ""),
        neris_incident_id=merged.get("neris_incident_id"),
        action_taken=merged.get("action_taken"),
        noaction_reason=merged.get("noaction_reason"),
        action_codes=merged.get("action_codes", []),
        additional_incident_types=merged.get("additional_incident_types", []),
        arrival_conditions=merged.get("arrival_conditions"),
        outside_fire_cause=merged.get("outside_fire_cause"),
        outside_fire_acres=merged.get("outside_fire_acres"),
        people_present=merged.get("people_present"),
        displaced_count=merged.get("displaced_count"),
        automatic_alarm=merged.get("automatic_alarm"),
        extras=merged_extras,
        created_by=user.email,
    )

    async with IncidentStore() as store:
        created = await store.create(doc)

    logger.info("User %s created incident %s from NERIS import", user.email, created.id)
    result = created.model_dump(mode="json")
    result["import_comparison"] = comparison

    # Hint for the chat assistant when the NERIS record is already approved
    neris_status = neris_prefill.get("_neris_status", "")
    if neris_status == "APPROVED":
        result["neris_approved"] = True
        result["finalize_hint"] = (
            "This NERIS record is already APPROVED. You can lock the local report "
            "by calling finalize_incident to prevent further local edits."
        )

    return result


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


async def list_neris_incidents() -> dict:
    """List incidents from the NERIS federal reporting system.

    Returns incidents submitted to NERIS for this fire department.
    Officers only.

    Returns:
        List of NERIS incident summaries with incident number, date,
        status, and type information
    """
    user = get_current_user()

    if not user.is_editor:
        group = get_org_config().editor_group_name
        return {
            "error": "You are not authorized to view or edit NERIS reports. "
            f"Ask an administrator to add you to the {group} group in Entra ID."
        }

    try:
        result = await asyncio.to_thread(_list_neris_incidents)
    except Exception as e:
        logger.exception("Failed to list NERIS incidents")
        return {"error": f"Failed to list NERIS incidents: {e}"}

    return result


def _list_neris_incidents() -> dict:
    """Fetch incidents from NERIS (blocking, for thread pool)."""
    from sjifire.ops.tasks.neris_sync import fetch_neris_summaries

    summaries = fetch_neris_summaries()
    return {"incidents": summaries, "count": len(summaries)}


async def get_neris_incident(neris_incident_id: str) -> dict:
    """Get a single incident from the NERIS federal reporting system.

    Retrieves the full incident record from NERIS by its compound ID.
    Officers only.

    Args:
        neris_incident_id: The NERIS incident ID
            (e.g., "FD53055879|26SJ0020|1770457554")

    Returns:
        The full NERIS incident data, or an error if not found
    """
    user = get_current_user()

    if not user.is_editor:
        group = get_org_config().editor_group_name
        return {
            "error": "You are not authorized to view or edit NERIS reports. "
            f"Ask an administrator to add you to the {group} group in Entra ID."
        }

    try:
        result = await asyncio.to_thread(_get_neris_incident, neris_incident_id)
    except Exception as e:
        logger.exception("Failed to get NERIS incident %s", neris_incident_id)
        return {"error": f"Failed to get NERIS incident: {e}"}

    if result is None:
        return {"error": f"NERIS incident not found: {neris_incident_id}"}

    return result


def _get_neris_incident(neris_incident_id: str) -> dict | None:
    """Fetch a single incident from NERIS (blocking, for thread pool)."""
    from sjifire.neris.client import NerisClient

    with NerisClient() as client:
        return client.get_incident(neris_incident_id)


async def finalize_incident(incident_id: str) -> dict:
    """Lock a locally-imported NERIS incident based on its current NERIS status.

    Fetches the current NERIS record status and sets the local incident to
    ``approved`` (if NERIS status is APPROVED) or ``submitted`` (otherwise).
    The incident must have a ``neris_incident_id`` and be in an editable
    status (not already locked).

    Only editors can finalize incidents.

    Args:
        incident_id: The incident document ID

    Returns:
        The updated incident document, or an error
    """
    user = get_current_user()

    if not user.is_editor:
        group = get_org_config().editor_group_name
        return {
            "error": "You are not authorized to finalize incidents. "
            f"Ask an administrator to add you to the {group} group in Entra ID."
        }

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}

        if not doc.neris_incident_id:
            return {
                "error": "Cannot finalize — this incident has no NERIS ID. "
                "Import from NERIS first using import_from_neris."
            }

        if doc.status in _LOCKED_STATUSES:
            return {
                "error": f"Incident is already {doc.status} and locked. "
                "No further changes can be made locally."
            }

        # Fetch current NERIS status
        try:
            neris_record = await asyncio.to_thread(_get_neris_incident, doc.neris_incident_id)
        except Exception:
            logger.warning(
                "Failed to fetch NERIS status for %s", doc.neris_incident_id, exc_info=True
            )
            return {"error": "Failed to fetch NERIS status. Try again later."}

        if not neris_record:
            return {"error": f"NERIS record not found: {doc.neris_incident_id}"}

        neris_status = (neris_record.get("incident_status") or {}).get("status", "")
        new_status = "approved" if neris_status == "APPROVED" else "submitted"

        doc.status = new_status
        doc.updated_at = datetime.now(UTC)
        doc.edit_history.append(
            EditEntry(
                editor_email=user.email,
                editor_name=user.name,
                fields_changed=["finalized"],
            )
        )

        updated = await store.update(doc)

    logger.info(
        "User %s finalized incident %s → %s (NERIS status: %s)",
        user.email,
        incident_id,
        new_status,
        neris_status,
    )
    return updated.model_dump(mode="json")


def _submit_to_neris(payload: dict) -> dict:  # pragma: no cover
    """Submit incident payload to NERIS (blocking, for thread pool).

    Returns dict with neris_id on success or error on failure.
    """
    from sjifire.neris.client import NerisClient

    try:
        with NerisClient() as client:
            result = client.api.create_incident(
                neris_id_entity=client.entity_id,
                body=payload,
            )
            neris_id = result.get("neris_id") or result.get("id", "")
            return {"neris_id": neris_id}
    except Exception as e:
        logger.exception("NERIS submission failed")
        return {"error": f"NERIS submission failed: {e}", "details": str(e)}
