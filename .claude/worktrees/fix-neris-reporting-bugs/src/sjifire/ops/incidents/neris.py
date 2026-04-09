"""NERIS-specific incident functions.

Handles parsing, diffing, patching, and import/export of NERIS incident records.
These functions are re-exported from ``tools.py`` for backward compatibility so
that callers using ``incident_tools.import_from_neris`` etc. continue to work.
"""

import asyncio
import contextlib
import html
import logging
from datetime import UTC, datetime

from sjifire.core.config import get_org_config, get_timezone, to_local_display, to_utc_iso
from sjifire.ops.auth import check_is_editor, get_current_user
from sjifire.ops.incidents.models import (
    AlarmInfo,
    DispatchNote,
    EditEntry,
    FireDetail,
    HazardInfo,
    IncidentDocument,
    UnitAssignment,
)
from sjifire.ops.incidents.neris_models import (
    NerisRecord,
    NerisUnitResponse,
)
from sjifire.ops.incidents.store import IncidentStore

logger = logging.getLogger(__name__)

_LOCKED_STATUSES = {"submitted", "approved"}


# ---------------------------------------------------------------------------
# Constants & display formatting
# ---------------------------------------------------------------------------


def _localize_diff_timestamps(diff: dict) -> dict:
    """Convert all timestamp values in a NERIS diff to local time for display."""
    result = {}
    for key, val in diff.items():
        if key in ("timestamps", "units"):
            # These have nested local/neris dicts of timestamps
            localized: dict = {k: v for k, v in val.items() if k not in ("local", "neris")}
            for side in ("local", "neris"):
                if side in val:
                    localized[side] = {
                        k: to_local_display(v) if isinstance(v, str) else v
                        for k, v in val[side].items()
                    }
            result[key] = localized
        else:
            result[key] = val
    return result


_DISPATCH_TS_KEYS = frozenset(
    {
        "call_create",
        "call_answered",
        "call_arrival",
        "incident_clear",
        "first_unit_dispatched",
    }
)
_UNIT_TS_KEYS = frozenset(
    {
        "dispatch",
        "enroute_to_scene",
        "staging",
        "on_scene",
        "unit_clear",
        "canceled_enroute",
    }
)


def _localize_creation_payload(payload: dict) -> dict:
    """Convert UTC timestamps in a NERIS creation payload to local time for display."""
    import copy

    p = copy.deepcopy(payload)
    dispatch = p.get("dispatch", {})
    for key in _DISPATCH_TS_KEYS:
        if key in dispatch and isinstance(dispatch[key], str):
            dispatch[key] = to_local_display(dispatch[key])
    for unit_resp in dispatch.get("unit_responses", []):
        for key in _UNIT_TS_KEYS:
            if key in unit_resp and isinstance(unit_resp[key], str):
                unit_resp[key] = to_local_display(unit_resp[key])
    for comment in dispatch.get("comments", []):
        if "timestamp" in comment and isinstance(comment["timestamp"], str):
            comment["timestamp"] = to_local_display(comment["timestamp"])
    return p


# Ephemeral cache: NERIS unit ID → local CAD designation (e.g. FD53055879S001U000 → E31).
# Rebuilt from NERIS entity API on first use; lost on restart (acceptable).
_neris_unit_map: dict[str, str] = {}
# Case-insensitive canonical names: lowercase → uppercase CAD designation.
# Built from the NERIS entity's cad_designation_1 values so that "Ops31"
# and "OPS31" both resolve to the same canonical name without hardcoding.
_cad_canonical: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Unit ID resolution & normalization
# ---------------------------------------------------------------------------


def _load_neris_unit_maps() -> None:
    """Populate unit maps from the NERIS entity API (once per process)."""
    if _neris_unit_map:
        return
    try:
        from sjifire.neris.client import NerisClient

        with NerisClient() as client:
            entity = client.get_entity()
        for station in entity.get("stations", []):
            for unit in station.get("units", []):
                uid = unit.get("neris_id", "")
                cad = unit.get("cad_designation_1", "")
                if uid and cad:
                    _neris_unit_map[uid] = cad.upper()
                    _cad_canonical[cad.lower()] = cad.upper()
        logger.info("Loaded %d NERIS unit mappings", len(_neris_unit_map))
    except Exception:
        logger.debug("Could not load NERIS unit mappings", exc_info=True)


def _resolve_neris_unit_id(neris_unit_id: str) -> str:
    """Map a NERIS unit ID to local CAD designation, fetching entity data if needed.

    Falls back to the raw NERIS ID if the mapping isn't available
    (e.g. credentials not set, API unreachable).
    """
    if not neris_unit_id:
        return neris_unit_id
    _load_neris_unit_maps()
    return _neris_unit_map.get(neris_unit_id, neris_unit_id)


def _normalize_unit_id(unit_id: str) -> str:
    """Normalize a unit ID to canonical uppercase form.

    Uses the NERIS entity's cad_designation_1 values as the source of truth,
    so "Ops31" → "OPS31", "b31" → "B31", etc. Falls back to uppercase
    if the unit isn't in the entity.
    """
    if not unit_id:
        return unit_id
    _load_neris_unit_maps()
    return _cad_canonical.get(unit_id.lower(), unit_id.upper())


# ---------------------------------------------------------------------------
# Timestamp & data parsing
# ---------------------------------------------------------------------------


def _neris_dispatch_to_cad_number(neris_dispatch: dict) -> str:
    """Derive our CAD incident number from a NERIS dispatch section.

    NERIS stores three identifiers in the dispatch section:
    - ``incident_number``: NERIS internal dispatch ID (e.g. ``"1771359925"``)
    - ``determinant_code``: Our CAD number without dash (e.g. ``"26002358"``)
    - ``dispatch_incident_number``: Sometimes populated, sometimes null

    Our canonical format is ``"YY-NNNNNN"`` (e.g. ``"26-002358"``), matching
    iSpyFire's ``long_term_call_id``.  Prefer ``determinant_code`` and
    re-insert the dash; fall back to ``incident_number`` if unavailable.
    """
    det_code = (neris_dispatch.get("determinant_code") or "").strip()
    if det_code and len(det_code) >= 3 and det_code[:2].isdigit():
        # Re-insert dash after the two-digit year prefix: "26002358" → "26-002358"
        return f"{det_code[:2]}-{det_code[2:]}"

    # Fall back to dispatch_incident_number, then incident_number
    return (
        neris_dispatch.get("dispatch_incident_number")
        or neris_dispatch.get("incident_number")
        or ""
    )


def _address_from_neris_location(loc) -> str:
    """Assemble a street address from NERIS granular location fields.

    NERIS locations have separate fields for number, direction prefix,
    street name, and street suffix. This combines them into a single
    address string like "94 Zepher Ln" or "1632 San Juan Rd".

    Accepts either a dict or a ``NerisLocation`` model instance.
    """
    _get = loc.get if isinstance(loc, dict) else lambda k, d=None: getattr(loc, k, d)
    parts: list[str] = []
    number = _get("complete_number") or _get("number") or ""
    if number:
        parts.append(str(number).strip())
    for field in ("street_prefix_direction", "street", "street_postfix"):
        val = _get(field)
        if val:
            parts.append(str(val).strip())
    return " ".join(parts)


def _parse_timestamp(val: str) -> datetime | None:
    """Parse an ISO timestamp string to a timezone-aware datetime.

    Handles both naive (assumed UTC) and aware timestamps so that
    local-time strings (from iSpyFire) and UTC strings (from NERIS)
    can be compared correctly.
    """
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            # Naive timestamps from our dispatch data are stored in the
            # org's local timezone; NERIS sends UTC with 'Z' suffix which
            # fromisoformat already parses as aware.  Assume naive = local.
            dt = dt.replace(tzinfo=get_timezone())
        return dt
    except (ValueError, TypeError):
        return None


def _timestamps_equal(a: str, b: str) -> bool:
    """Compare two ISO timestamp strings as timezone-aware datetimes.

    Returns True when both strings represent the same instant, regardless
    of timezone or trailing-Z formatting differences.
    """
    if a == b:
        return True
    dt_a = _parse_timestamp(a)
    dt_b = _parse_timestamp(b)
    if dt_a is None or dt_b is None:
        return False
    return dt_a == dt_b


def _sanitize_for_neris(text: str) -> str:
    """Prepare text for the NERIS API to avoid HTML entity encoding.

    NERIS HTML-encodes certain characters on storage (e.g. ``'`` → ``&#x27;``).
    Replace them with visually identical Unicode alternatives that pass through
    without encoding.
    """
    # ASCII apostrophe / single quote → Modifier Letter Apostrophe (U+02BC)
    # Looks identical in all fonts, avoids &#x27; encoding.
    return text.replace("'", "\u02bc")


def _getattr_path(obj, path: str):
    """Resolve dotted attribute path, returning None if any segment is None."""
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


# Fire detail: NERIS attribute path → local model key
_FIRE_LOCATION_MAP: dict[str, str] = {
    "damage_type": "fire_bldg_damage",
    "room_of_origin_type": "room_of_origin",
    "floor_of_origin": "floor_of_origin",
    "cause": "fire_cause_in",
    "progression_evident": "fire_progression_evident",
}

_FIRE_TOP_MAP: dict[str, str] = {
    "water_supply": "water_supply",
    "investigation_needed": "fire_investigation",
    "investigation_types": "fire_investigation_types",
    "suppression_appliances": "suppression_appliances",
}

# Alarm sections: NERIS record attr → local alarm_info key
_ALARM_SECTION_MAP: tuple[tuple[str, str], ...] = (
    ("smoke_alarm", "smoke_alarm_presence"),
    ("fire_alarm", "fire_alarm_presence"),
    ("fire_suppression", "sprinkler_presence"),
)

# Tactic timestamp keys (same name in NERIS and local)
_TACTIC_TS_KEYS: tuple[str, ...] = (
    "command_established",
    "completed_sizeup",
    "water_on_fire",
    "fire_under_control",
    "fire_knocked_down",
    "suppression_complete",
    "primary_search_begin",
    "primary_search_complete",
    "extrication_complete",
)


# ---------------------------------------------------------------------------
# Unit & record conversion
# ---------------------------------------------------------------------------


def _build_unit_from_neris(u: NerisUnitResponse) -> UnitAssignment:
    """Convert a NERIS unit response to a local UnitAssignment."""
    raw_id = u.reported_unit_id or _resolve_neris_unit_id(u.unit_neris_id or "")
    unit = UnitAssignment(
        unit_id=raw_id,
        response_mode=u.response_mode or "",
        dispatch=u.dispatch or "",
        enroute=u.enroute_to_scene or "",
        staged=u.staging or "",
        on_scene=u.on_scene or "",
        cleared=u.unit_clear or "",
        canceled=u.canceled_enroute or "",
    )
    if u.staffing is not None:
        unit.comment = f"Staffing: {u.staffing}"
    return unit


def _dedup_units(units: list[UnitAssignment]) -> list[UnitAssignment]:
    """Merge units with the same unit_id, keeping the earliest non-empty timestamp for each field.

    NERIS records sometimes have duplicate entries for the same unit — e.g.,
    one with a ``reported_unit_id`` and another with a ``unit_neris_id`` that
    resolve to the same canonical ID.  Merging avoids duplicate rows.
    """
    seen: dict[str, UnitAssignment] = {}
    ts_fields = ("dispatch", "enroute", "staged", "on_scene", "cleared", "canceled")
    for unit in units:
        key = unit.unit_id.upper()
        if key not in seen:
            seen[key] = unit
            continue
        existing = seen[key]
        for field in ts_fields:
            new_val = getattr(unit, field, "")
            cur_val = getattr(existing, field, "")
            if new_val and (not cur_val or new_val < cur_val):
                setattr(existing, field, new_val)
        if not existing.response_mode and unit.response_mode:
            existing.response_mode = unit.response_mode
        if not existing.comment and unit.comment:
            existing.comment = unit.comment
    return list(seen.values())


# ---------------------------------------------------------------------------
# NERIS record parsing & import prefill
# ---------------------------------------------------------------------------


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
    rec = NerisRecord.model_validate(record)
    prefill: dict = {"neris_incident_id": neris_id}
    extras: dict = {}
    timestamps: dict[str, str] = {}

    # ── Incident type — first primary type ──
    types = rec.incident_types or []
    if types and types[0].type:
        prefill["incident_type"] = types[0].type

    # ── Additional incident types ──
    if len(types) > 1:
        prefill["additional_incident_types"] = [t.type for t in types[1:] if t.type]

    # ── Base section ──
    base = rec.base
    if base:
        if base.outcome_narrative:
            prefill["narrative"] = html.unescape(base.outcome_narrative)
        if base.location_use and base.location_use.use_type:
            prefill["location_use"] = base.location_use.use_type
        if base.people_present is not None:
            prefill["people_present"] = base.people_present
        if base.displacement_count is not None:
            prefill["displaced_count"] = base.displacement_count
        if base.displacement_causes:
            extras["displacement_causes"] = base.displacement_causes
        if base.animals_rescued is not None:
            extras["animals_rescued"] = base.animals_rescued
        if base.impediment_narrative:
            extras["impediment_narrative"] = html.unescape(base.impediment_narrative)

    # ── Location — prefer base.location (corrected), fall back to dispatch ──
    loc = _getattr_path(rec, "base.location")
    if not loc:
        loc = _getattr_path(rec, "dispatch.location")
    if loc:
        addr = _address_from_neris_location(loc)
        if addr:
            prefill["address"] = addr
        if loc.incorporated_municipality:
            prefill["city"] = loc.incorporated_municipality
        if loc.state:
            prefill["state"] = loc.state
        if loc.postal_code:
            prefill["zip_code"] = str(loc.postal_code)
        if loc.county:
            prefill["county"] = loc.county

    # ── Dispatch section ──
    dispatch = rec.dispatch
    if dispatch:
        neris_units = dispatch.unit_responses or []
        if neris_units:
            # Sort units by enroute time (earliest first) for consistent ordering
            sorted_units = sorted(
                neris_units,
                key=lambda u: u.enroute_to_scene or u.dispatch or "\xff",
            )
            prefill["units"] = _dedup_units([_build_unit_from_neris(u) for u in sorted_units])
        if dispatch.automatic_alarm is not None:
            prefill["automatic_alarm"] = dispatch.automatic_alarm

        # Parse NERIS dispatch.comments → dispatch_notes
        neris_comments = (record.get("dispatch") or {}).get("comments") or []
        if neris_comments:
            notes = []
            for c in neris_comments:
                text = html.unescape(c.get("comment", "")).strip()
                ts = c.get("timestamp") or ""
                if text:
                    notes.append(DispatchNote(timestamp=str(ts), text=text))
            if notes:
                prefill["dispatch_notes"] = notes
        # call_arrival = when 911 call arrives at PSAP (earliest)
        # call_create = when the CAD incident is created (later)
        # Use call_arrival as psap_answer; fall back to call_create
        if dispatch.call_arrival:
            timestamps["psap_answer"] = dispatch.call_arrival
        elif dispatch.call_create:
            timestamps["psap_answer"] = dispatch.call_create
        if dispatch.call_create:
            timestamps["alarm_time"] = dispatch.call_create
        if dispatch.incident_clear:
            timestamps["incident_clear"] = dispatch.incident_clear

        # Earliest unit enroute/on_scene across all units
        for field, ts_key in (
            ("enroute_to_scene", "first_unit_enroute"),
            ("on_scene", "first_unit_arrived"),
        ):
            times = [getattr(u, field) for u in neris_units if getattr(u, field, None)]
            if times:
                timestamps[ts_key] = min(times)

    # ── Actions & tactics ──
    action_noaction = _getattr_path(rec, "actions_tactics.action_noaction")
    if action_noaction and action_noaction.type:
        prefill["action_taken"] = action_noaction.type
        if action_noaction.type == "NOACTION" and action_noaction.noaction_type:
            prefill["noaction_reason"] = action_noaction.noaction_type
        elif action_noaction.type == "ACTION" and action_noaction.actions:
            prefill["action_codes"] = [
                a for a in action_noaction.actions if isinstance(a, str) and a
            ]

    # ── Fire detail → typed sub-model ──
    fire_detail_data: dict = {}
    fd = rec.fire_detail
    if fd:
        ld = fd.location_detail
        if ld:
            if ld.arrival_condition:
                prefill["arrival_conditions"] = ld.arrival_condition
            for neris_key, model_key in _FIRE_LOCATION_MAP.items():
                val = getattr(ld, neris_key, None)
                if val is not None:
                    fire_detail_data[model_key] = val
            # Outside fire
            if ld.type == "OUTSIDE":
                if ld.cause:
                    prefill["outside_fire_cause"] = ld.cause
                if ld.acres_burned is not None:
                    prefill["outside_fire_acres"] = ld.acres_burned

        for neris_key, model_key in _FIRE_TOP_MAP.items():
            val = getattr(fd, neris_key, None)
            if val:
                fire_detail_data[model_key] = val

    if fire_detail_data:
        prefill["fire_detail"] = fire_detail_data

    # ── Alarms & suppression → typed sub-model ──
    alarm_info_data: dict = {}
    for alarm_attr, model_key in _ALARM_SECTION_MAP:
        ptype = _getattr_path(rec, f"{alarm_attr}.presence.type")
        if ptype:
            alarm_info_data[model_key] = ptype

    # Smoke alarm details when present
    smoke_presence = _getattr_path(rec, "smoke_alarm.presence")
    if smoke_presence and smoke_presence.type == "PRESENT":
        if smoke_presence.alarm_types:
            alarm_info_data["smoke_alarm_types"] = smoke_presence.alarm_types
        operation = smoke_presence.operation
        if operation:
            alerted = operation.alerted_failed_other or {}
            if alerted.get("type"):
                alarm_info_data["smoke_alarm_operation"] = alerted["type"]
            if alerted.get("occupant_action"):
                alarm_info_data["smoke_alarm_occupant_action"] = alerted["occupant_action"]

    if alarm_info_data:
        prefill["alarm_info"] = alarm_info_data

    # ── Hazards → typed sub-model ──
    hazard_info_data: dict = {}
    if rec.electric_hazards:
        eh_types = [eh.type for eh in rec.electric_hazards if eh.type]
        hazard_info_data["electric_hazards"] = eh_types if eh_types else []

    for pg in rec.powergen_hazards or []:
        pg_type = _getattr_path(pg, "pv_other.type") or ""
        if not pg_type:
            continue
        upper = pg_type.upper()
        if "SOLAR" in upper or "PV" in upper:
            hazard_info_data["solar_present"] = "YES"
        elif "BATTERY" in upper or "ESS" in upper:
            hazard_info_data["battery_ess_present"] = "YES"
        elif "GENERATOR" in upper:
            hazard_info_data["generator_present"] = "YES"
        elif pg_type != "NOT_APPLICABLE":
            hazard_info_data["powergen_type"] = pg_type

    csst = rec.csst_hazard
    if csst:
        if csst.ignition_source is True:
            hazard_info_data["csst_present"] = "YES"
        elif csst.ignition_source is False:
            hazard_info_data["csst_present"] = "NO"
        else:
            hazard_info_data["csst_present"] = "UNKNOWN"
        if csst.lightning_suspected and csst.lightning_suspected != "UNKNOWN":
            hazard_info_data["csst_lightning_suspected"] = csst.lightning_suspected
        if csst.grounded is not None:
            hazard_info_data["csst_grounded"] = csst.grounded

    if hazard_info_data:
        prefill["hazard_info"] = hazard_info_data

    # ── Medical details ──
    medical_details = rec.medical_details or []
    if medical_details:
        extras["patient_count"] = len(medical_details)
        for i, med in enumerate(medical_details):
            prefix = "" if len(medical_details) == 1 else f"patient_{i + 1}_"
            if med.patient_care_evaluation:
                extras[f"{prefix}care_disposition"] = med.patient_care_evaluation
            if med.transport_disposition:
                extras[f"{prefix}transport_disposition"] = med.transport_disposition
            if med.patient_status:
                extras[f"{prefix}patient_status"] = med.patient_status

    # ── Tactic timestamps ──
    tactic_ts = rec.tactic_timestamps
    if tactic_ts:
        for ts_key in _TACTIC_TS_KEYS:
            val = getattr(tactic_ts, ts_key, None)
            if val:
                timestamps[ts_key] = val if isinstance(val, str) else str(val)

    # ── Casualty/rescue data (complex nested — kept as dict traversal) ──
    casualty_rescues = rec.casualty_rescues or []
    if casualty_rescues:
        cr_list = []
        for cr in casualty_rescues:
            cr_entry: dict = {}
            cr_entry["type"] = cr.get("type", "")  # FF or NONFF
            cr_entry["gender"] = cr.get("gender")

            casualty = cr.get("casualty") or {}
            injury = casualty.get("injury_or_noninjury") or {}
            if injury:
                cr_entry["injury_type"] = injury.get("type")
                cr_entry["injury_cause"] = injury.get("cause")

            rescue = cr.get("rescue") or {}
            ff_rescue = rescue.get("ffrescue_or_nonffrescue") or {}
            if ff_rescue:
                cr_entry["rescue_type"] = ff_rescue.get("type")
                cr_entry["rescue_actions"] = ff_rescue.get("actions")
                cr_entry["rescue_impediments"] = ff_rescue.get("impediments")
                removal = ff_rescue.get("removal_or_nonremoval") or {}
                if removal:
                    cr_entry["removal_type"] = removal.get("type")
                    cr_entry["removal_room"] = removal.get("room_type")
                    cr_entry["removal_elevation"] = removal.get("elevation_type")
                    cr_entry["rescue_path"] = removal.get("rescue_path_type")

            presence_known = rescue.get("presence_known") or {}
            if presence_known:
                cr_entry["presence_known"] = presence_known.get("presence_known_type")

            cr_list.append({k: v for k, v in cr_entry.items() if v is not None})
        extras["casualty_rescues"] = cr_list

    # ── Non-FD aids ──
    if rec.nonfd_aids:
        aid_types = [a.type for a in rec.nonfd_aids if a.type]
        if aid_types:
            extras["nonfd_aids"] = aid_types

    if timestamps:
        prefill["timestamps"] = timestamps
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


# ---------------------------------------------------------------------------
# NERIS API wrappers (blocking, run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _get_neris_incident(neris_incident_id: str) -> dict | None:
    """Fetch a single incident from NERIS (blocking, for thread pool)."""
    from sjifire.neris.client import NerisClient

    with NerisClient() as client:
        return client.get_incident(neris_incident_id)


def _list_neris_incidents() -> dict:
    """Fetch incidents from NERIS (blocking, for thread pool)."""
    from sjifire.ops.tasks.neris_sync import fetch_neris_summaries

    summaries = fetch_neris_summaries()
    return {"incidents": summaries, "count": len(summaries)}


def _patch_neris_incident(neris_id: str, properties: dict) -> dict:
    """Patch a NERIS incident record (blocking, for thread pool).

    Raises ``RuntimeError`` when the NERIS API returns an HTTP error.
    The upstream ``neris_api_client`` catches ``HTTPError`` and returns
    the raw ``requests.Response`` object instead of raising, so we
    detect that here and surface the error properly.
    """
    from sjifire.neris.client import NerisClient

    with NerisClient() as client:
        result = client.patch_incident(neris_id, properties)

    # The upstream client returns a requests.Response on HTTP errors
    # instead of raising.  Detect and raise so callers get a clear error.
    if not isinstance(result, dict):
        status = getattr(result, "status_code", "unknown")
        body = ""
        with contextlib.suppress(Exception):
            body = result.text[:500] if hasattr(result, "text") else str(result)
        raise RuntimeError(f"NERIS API error (HTTP {status}): {body}")

    return result


def _submit_to_neris(payload: dict) -> dict:  # pragma: no cover
    """Submit incident payload to NERIS (blocking, for thread pool).

    Returns dict with neris_id on success or error on failure.
    """
    from sjifire.neris.client import NerisClient

    try:
        with NerisClient() as client:
            result = client.api.create_incident(
                neris_id=client.entity_id,
                body=payload,
            )
            # The upstream library returns a raw Response on HTTP errors
            if not isinstance(result, dict):
                status = getattr(result, "status_code", "unknown")
                body = ""
                with contextlib.suppress(Exception):
                    body = result.text[:500] if hasattr(result, "text") else str(result)
                return {"error": f"NERIS API error (HTTP {status}): {body}"}
            neris_id = result.get("neris_id") or result.get("id", "")
            return {"neris_id": neris_id}
    except Exception as e:
        logger.exception("NERIS submission failed")
        return {"error": f"NERIS submission failed: {e}", "details": str(e)}


# ---------------------------------------------------------------------------
# Creation payload construction
# ---------------------------------------------------------------------------


def _resolve_local_to_neris_id(unit_id: str) -> str | None:
    """Map local unit ID (e.g. 'E31') to NERIS unit ID (e.g. 'FD53055879S001U000')."""
    _load_neris_unit_maps()
    uid_upper = unit_id.upper()
    for neris_id, cad in _neris_unit_map.items():
        if cad.upper() == uid_upper:
            return neris_id
    return None


def _build_location(doc: IncidentDocument) -> dict:
    """Build NERIS location from IncidentDocument address fields."""
    loc: dict = {
        "incorporated_municipality": doc.city or None,
        "state": doc.state or None,
        "postal_code": doc.zip_code or None,
        "county": doc.county or None,
    }
    if doc.address:
        parts = doc.address.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            loc["complete_number"] = parts[0]
            loc["street"] = parts[1]
        else:
            loc["street"] = doc.address
    return {k: v for k, v in loc.items() if v is not None}


def _build_unit_response_for_creation(unit: UnitAssignment) -> dict:
    """Build a NERIS unit response entry for incident creation."""
    neris_uid = _resolve_local_to_neris_id(unit.unit_id)
    resp: dict = {"reported_unit_id": unit.unit_id}
    if neris_uid:
        resp["unit_neris_id"] = neris_uid
    if unit.response_mode:
        resp["response_mode"] = unit.response_mode
    for local_field, neris_field in (
        ("dispatch", "dispatch"),
        ("enroute", "enroute_to_scene"),
        ("staged", "staging"),
        ("on_scene", "on_scene"),
        ("cleared", "unit_clear"),
        ("canceled", "canceled_enroute"),
    ):
        val = getattr(unit, local_field, "")
        if val:
            resp[neris_field] = to_utc_iso(val)
    if unit.personnel:
        resp["staffing"] = len(unit.personnel)
    return resp


def _build_dispatch_comments(doc: IncidentDocument) -> list[dict] | None:
    """Build NERIS dispatch comments from dispatch_notes or dispatch_comments."""
    notes = doc.dispatch_notes
    if not notes and doc.dispatch_comments:
        from sjifire.ops.incidents.tools import _parse_cad_comments

        call_ts = doc.timestamps.get("psap_answer", "")
        notes = _parse_cad_comments(doc.dispatch_comments, call_ts=call_ts)
    if not notes:
        return None
    return [
        {
            "comment": _sanitize_for_neris(f"[{n.unit}] {n.text}" if n.unit else n.text),
            **({"timestamp": to_utc_iso(n.timestamp)} if n.timestamp else {}),
        }
        for n in notes
    ]


def _build_neris_creation_payload(doc: IncidentDocument) -> dict:
    """Convert an IncidentDocument into a NERIS creation payload dict."""
    org = get_org_config()
    location = _build_location(doc)

    # ── Incident types (required) ──
    incident_types = []
    if doc.incident_type:
        incident_types.append({"type": doc.incident_type, "primary": True})
    incident_types.extend({"type": t, "primary": False} for t in doc.additional_incident_types)

    # ── Base section ──
    base: dict = {
        "department_neris_id": org.neris_entity_id,
        "incident_number": doc.incident_number,
        "location": location or None,
        "outcome_narrative": _sanitize_for_neris(doc.narrative) if doc.narrative else None,
        "people_present": doc.people_present,
        "displacement_count": doc.displaced_count,
    }
    if doc.location_use:
        base["location_use"] = {"use_type": doc.location_use}
    base = {k: v for k, v in base.items() if v is not None}

    # ── Dispatch section (required) ──
    dispatch: dict = {
        "incident_number": doc.incident_number,
        "determinant_code": doc.incident_number.replace("-", "")[:8] or None,
        "location": location or None,
        "call_create": to_utc_iso(doc.timestamps.get("alarm_time", ""))
        or to_utc_iso(doc.timestamps.get("psap_answer", ""))
        or None,
        "call_answered": to_utc_iso(doc.timestamps.get("psap_answer", "")) or None,
        "call_arrival": to_utc_iso(doc.timestamps.get("psap_answer", "")) or None,
        "incident_clear": to_utc_iso(doc.timestamps.get("incident_clear", "")) or None,
        "automatic_alarm": doc.automatic_alarm,
        "unit_responses": [_build_unit_response_for_creation(u) for u in doc.units],
    }
    comments = _build_dispatch_comments(doc)
    if comments:
        dispatch["comments"] = comments
    dispatch = {k: v for k, v in dispatch.items() if v is not None}

    payload: dict = {
        "base": base,
        "dispatch": dispatch,
        "incident_types": incident_types,
    }

    # ── Actions/Tactics (optional) ──
    if doc.action_taken == "ACTION":
        payload["actions_tactics"] = {
            "action_noaction": {"type": "ACTION", "actions": doc.action_codes or None}
        }
    elif doc.action_taken == "NOACTION" and doc.noaction_reason:
        payload["actions_tactics"] = {
            "action_noaction": {"type": "NOACTION", "noaction_type": doc.noaction_reason}
        }

    # ── Fire detail (optional) ──
    fd = doc.fire_detail
    if fd:
        fire_detail: dict = {}
        location_detail: dict = {}

        if doc.arrival_conditions:
            location_detail["arrival_condition"] = doc.arrival_conditions

        for model_key, neris_key in (
            ("fire_bldg_damage", "damage_type"),
            ("room_of_origin", "room_of_origin_type"),
            ("floor_of_origin", "floor_of_origin"),
            ("fire_cause_in", "cause"),
            ("fire_progression_evident", "progression_evident"),
        ):
            val = getattr(fd, model_key, None)
            if val is not None:
                location_detail[neris_key] = val

        # Outside fire fields
        if doc.outside_fire_cause:
            location_detail["cause"] = doc.outside_fire_cause
            location_detail["type"] = "OUTSIDE"
        if doc.outside_fire_acres is not None:
            location_detail["acres_burned"] = doc.outside_fire_acres
            location_detail["type"] = "OUTSIDE"

        if location_detail and "type" not in location_detail:
            location_detail["type"] = "STRUCTURE"

        if location_detail:
            fire_detail["location_detail"] = location_detail

        for model_key, neris_key in (
            ("water_supply", "water_supply"),
            ("fire_investigation", "investigation_needed"),
            ("fire_investigation_types", "investigation_types"),
            ("suppression_appliances", "suppression_appliances"),
        ):
            val = getattr(fd, model_key, None)
            if val:
                fire_detail[neris_key] = val

        if fire_detail:
            payload["fire_detail"] = fire_detail

    # ── Alarm info (optional) ──
    ai = doc.alarm_info
    if ai:
        for alarm_field, neris_attr in (
            ("smoke_alarm_presence", "smoke_alarm"),
            ("fire_alarm_presence", "fire_alarm"),
            ("sprinkler_presence", "fire_suppression"),
        ):
            val = getattr(ai, alarm_field, None)
            if val:
                presence: dict = {"type": val}
                if neris_attr == "smoke_alarm" and val == "PRESENT":
                    if ai.smoke_alarm_types:
                        presence["alarm_types"] = ai.smoke_alarm_types
                    if ai.smoke_alarm_operation:
                        presence["operation"] = {
                            "alerted_failed_other": {"type": ai.smoke_alarm_operation}
                        }
                        if ai.smoke_alarm_occupant_action:
                            presence["operation"]["alerted_failed_other"]["occupant_action"] = (
                                ai.smoke_alarm_occupant_action
                            )
                payload[neris_attr] = {"presence": presence}

    # ── Hazard info (optional) ──
    hi = doc.hazard_info
    if hi:
        if hi.electric_hazards:
            payload["electric_hazards"] = [{"type": t} for t in hi.electric_hazards]

        powergen = []
        for field_name, pv_type in (
            ("solar_present", "PV_SOLAR"),
            ("battery_ess_present", "BATTERY_ESS"),
            ("generator_present", "GENERATOR"),
        ):
            val = getattr(hi, field_name, None)
            if val and val != "NO":
                powergen.append({"pv_other": {"type": pv_type}})
        if hi.powergen_type:
            powergen.append({"pv_other": {"type": hi.powergen_type}})
        if powergen:
            payload["powergen_hazards"] = powergen

        if hi.csst_present:
            csst: dict = {}
            if hi.csst_present == "YES":
                csst["ignition_source"] = True
            elif hi.csst_present == "NO":
                csst["ignition_source"] = False
            if hi.csst_lightning_suspected and hi.csst_lightning_suspected != "UNKNOWN":
                csst["lightning_suspected"] = hi.csst_lightning_suspected
            if hi.csst_grounded is not None:
                csst["grounded"] = hi.csst_grounded
            if csst:
                payload["csst_hazard"] = csst

    # ── Tactic timestamps (optional) ──
    tactic_ts: dict = {}
    for ts_key in _TACTIC_TS_KEYS:
        val = doc.timestamps.get(ts_key, "")
        if val:
            tactic_ts[ts_key] = to_utc_iso(val)
    if tactic_ts:
        payload["tactic_timestamps"] = tactic_ts

    # ── Medical details from extras (optional) ──
    extras = doc.extras
    if extras.get("patient_count"):
        medical_details = []
        count = extras["patient_count"]
        for i in range(count):
            prefix = "" if count == 1 else f"patient_{i + 1}_"
            med: dict = {}
            if extras.get(f"{prefix}care_disposition"):
                med["patient_care_evaluation"] = extras[f"{prefix}care_disposition"]
            if extras.get(f"{prefix}transport_disposition"):
                med["transport_disposition"] = extras[f"{prefix}transport_disposition"]
            if extras.get(f"{prefix}patient_status"):
                med["patient_status"] = extras[f"{prefix}patient_status"]
            if med:
                medical_details.append(med)
        if medical_details:
            payload["medical_details"] = medical_details

    # ── Casualty/rescues from extras (optional) ──
    if extras.get("casualty_rescues"):
        payload["casualty_rescues"] = extras["casualty_rescues"]

    # ── Non-FD aids from extras (optional) ──
    if extras.get("nonfd_aids"):
        payload["nonfd_aids"] = [{"type": t} for t in extras["nonfd_aids"]]

    return payload


# ---------------------------------------------------------------------------
# Diff & patch construction
# ---------------------------------------------------------------------------


def _build_neris_diff(doc: IncidentDocument, neris_record: dict) -> dict:
    """Compare local incident fields against the NERIS record.

    Returns a dict of field_name → {"local": ..., "neris": ...} for
    fields that differ.
    """
    diff: dict = {}
    base = neris_record.get("base") or {}
    dispatch = neris_record.get("dispatch") or {}

    # Narrative (NERIS HTML-encodes text — decode for comparison;
    # also normalize modifier apostrophe U+02BC back to ASCII for matching)
    neris_narrative = html.unescape(base.get("outcome_narrative") or "").replace("\u02bc", "'")
    if doc.narrative and doc.narrative != neris_narrative:
        diff["narrative"] = {"local": doc.narrative, "neris": neris_narrative}

    # Address
    loc = base.get("location") or {}
    neris_addr = _address_from_neris_location(loc)
    if doc.address and doc.address != neris_addr:
        diff["address"] = {"local": doc.address, "neris": neris_addr}

    # City
    neris_city = loc.get("incorporated_municipality") or ""
    if doc.city and doc.city != neris_city:
        diff["city"] = {"local": doc.city, "neris": neris_city}

    # State
    neris_state = loc.get("state") or ""
    if doc.state and doc.state != neris_state:
        diff["state"] = {"local": doc.state, "neris": neris_state}

    # Zip
    neris_zip = loc.get("postal_code") or ""
    if doc.zip_code and doc.zip_code != neris_zip:
        diff["zip_code"] = {"local": doc.zip_code, "neris": neris_zip}

    # County
    neris_county = loc.get("county") or ""
    if doc.county and doc.county != neris_county:
        diff["county"] = {"local": doc.county, "neris": neris_county}

    # Dispatch incident number (CAD number)
    neris_dispatch_num = dispatch.get("dispatch_incident_number") or ""
    neris_incident_num = dispatch.get("incident_number") or ""
    if doc.incident_number:
        local_normalized = doc.incident_number.replace("-", "")
        neris_disp_normalized = neris_dispatch_num.replace("-", "")
        neris_inc_normalized = neris_incident_num.replace("-", "")
        # Diff if neither NERIS field matches our local dispatch number
        if local_normalized != neris_disp_normalized and local_normalized != neris_inc_normalized:
            diff["dispatch_incident_number"] = {
                "local": doc.incident_number,
                "neris": neris_dispatch_num or neris_incident_num,
            }

    # Dispatch-level timestamps
    # call_arrival = when 911 call arrives at PSAP (earliest timestamp)
    # call_create = when the CAD incident is created (alarm/page time)
    ts_map = {
        "psap_answer": ("call_arrival", dispatch),
        "alarm_time": ("call_create", dispatch),
        "first_unit_dispatched": ("first_unit_dispatched", dispatch),
        "incident_clear": ("incident_clear", dispatch),
    }
    for local_key, (neris_key, section) in ts_map.items():
        local_val = doc.timestamps.get(local_key, "")
        neris_val = section.get(neris_key) or ""
        if local_val and not _timestamps_equal(local_val, neris_val):
            diff.setdefault("timestamps", {"local": {}, "neris": {}})
            diff["timestamps"]["local"][local_key] = local_val
            diff["timestamps"]["neris"][neris_key] = neris_val

    # ── Unit-level fields ──
    # Each entry: (local_field, neris_field, type) where type controls
    # comparison and serialization:
    #   "ts"  — ISO timestamp (compared with _timestamps_equal, serialized via to_utc_iso)
    #   "str" — plain string (compared case-sensitive, passed through as-is)
    unit_field_map: list[tuple[str, str, str]] = [
        ("dispatch", "dispatch", "ts"),
        ("enroute", "enroute_to_scene", "ts"),
        ("staged", "staging", "ts"),
        ("on_scene", "on_scene", "ts"),
        ("cleared", "unit_clear", "ts"),
        ("canceled", "canceled_enroute", "ts"),
        ("response_mode", "response_mode", "str"),
    ]

    neris_units = dispatch.get("unit_responses") or []
    neris_unit_map_local: dict[str, dict] = {}
    for nu in neris_units:
        uid = nu.get("reported_unit_id") or _resolve_neris_unit_id(nu.get("unit_neris_id", ""))
        if uid:
            neris_unit_map_local[uid.upper()] = nu

    for unit in doc.units:
        neris_unit = neris_unit_map_local.get(unit.unit_id.upper(), {})
        neris_uid = neris_unit.get("neris_uid")
        neris_reported_id = neris_unit.get("reported_unit_id", "")

        # Collect changed fields for this unit: list of (local_field, local_val, neris_val)
        changed: list[tuple[str, object, object]] = []

        for local_field, neris_field, ftype in unit_field_map:
            local_val = getattr(unit, local_field, "")
            neris_val = neris_unit.get(neris_field) or ""
            if not local_val:
                continue
            if ftype == "ts" and _timestamps_equal(local_val, neris_val):
                continue
            if ftype == "str" and local_val == neris_val:
                continue
            changed.append((local_field, local_val, neris_val))

        # Staffing: derived from personnel list length, compared to NERIS int
        local_staffing = len(unit.personnel) if unit.personnel else None
        neris_staffing = neris_unit.get("staffing")
        if local_staffing and local_staffing != neris_staffing:
            changed.append(("staffing", local_staffing, neris_staffing))

        # Record all changes for this unit
        for field, local_val, neris_val in changed:
            diff.setdefault(
                "units",
                {"local": {}, "neris": {}, "neris_uids": {}, "reported_unit_ids": {}},
            )
            diff["units"]["local"][f"{unit.unit_id}.{field}"] = local_val
            diff["units"]["neris"][f"{unit.unit_id}.{field}"] = neris_val
            if neris_uid is not None:
                diff["units"]["neris_uids"][unit.unit_id] = neris_uid
            if neris_reported_id:
                diff["units"]["reported_unit_ids"][unit.unit_id] = neris_reported_id

    # Incident type
    types = neris_record.get("incident_types") or []
    neris_type = types[0].get("type", "") if types else ""
    if doc.incident_type and doc.incident_type != neris_type:
        diff["incident_type"] = {"local": doc.incident_type, "neris": neris_type}

    # People present
    neris_people = base.get("people_present")
    if doc.people_present is not None and doc.people_present != neris_people:
        diff["people_present"] = {"local": doc.people_present, "neris": neris_people}

    # Displaced count
    neris_displaced = base.get("displacement_count")
    if doc.displaced_count is not None and doc.displaced_count != neris_displaced:
        diff["displaced_count"] = {"local": doc.displaced_count, "neris": neris_displaced}

    # Automatic alarm
    neris_auto_alarm = dispatch.get("automatic_alarm")
    if doc.automatic_alarm is not None and doc.automatic_alarm != neris_auto_alarm:
        diff["automatic_alarm"] = {"local": doc.automatic_alarm, "neris": neris_auto_alarm}

    # Dispatch comments — find local notes not yet in NERIS.
    # Match by timestamp (not text) because NERIS redacts PII in
    # comment text (phone numbers, names → "************").
    #
    # Re-parse dispatch_comments blob if it produces more granular
    # entries than the stored dispatch_notes (fixes old single-blob imports).
    notes_to_diff = doc.dispatch_notes
    if doc.dispatch_comments:
        from sjifire.ops.incidents.tools import _parse_cad_comments

        call_ts = doc.timestamps.get("psap_answer", "")
        reparsed = _parse_cad_comments(doc.dispatch_comments, call_ts=call_ts)
        if len(reparsed) > len(notes_to_diff):
            notes_to_diff = reparsed

    if notes_to_diff:
        neris_comments = dispatch.get("comments") or []
        neris_comment_timestamps: set[str] = set()
        for c in neris_comments:
            ts = c.get("timestamp") or ""
            if ts:
                neris_comment_timestamps.add(ts)

        new_notes = []
        for note in notes_to_diff:
            if not note.timestamp:
                new_notes.append(note)
                continue
            # Normalize local timestamp to UTC for comparison
            local_utc = to_utc_iso(note.timestamp)
            if local_utc not in neris_comment_timestamps:
                new_notes.append(note)

        if new_notes:
            diff["dispatch_comments"] = {
                "local": new_notes,
                "neris": neris_comments,
            }

    return diff


def _build_neris_patch(diff: dict, neris_record: dict | None = None) -> dict:
    """Convert a diff dict into NERIS patch properties format.

    The NERIS API uses discriminated unions at every nesting level.
    Each section (``base``, ``dispatch``) must be wrapped with
    ``{"action": "patch", "properties": {...}}``, and sub-objects like
    ``base.location`` need the same wrapping.  Each wrapper includes
    the section's ``neris_uid`` so the API can identify which nested
    record to patch.
    """
    neris_record = neris_record or {}
    neris_base = neris_record.get("base") or {}
    neris_dispatch = neris_record.get("dispatch") or {}
    neris_location = neris_base.get("location") or {}

    properties: dict = {}

    # ── base section ──
    base_props: dict = {}
    base_location_props: dict = {}

    if "narrative" in diff:
        base_props["outcome_narrative"] = {
            "action": "set",
            "value": _sanitize_for_neris(diff["narrative"]["local"]),
        }

    if "address" in diff:
        base_location_props["street_address"] = {
            "action": "set",
            "value": diff["address"]["local"],
        }

    if "city" in diff:
        base_location_props["incorporated_municipality"] = {
            "action": "set",
            "value": diff["city"]["local"],
        }

    if "state" in diff:
        base_location_props["state"] = {
            "action": "set",
            "value": diff["state"]["local"],
        }

    if "zip_code" in diff:
        base_location_props["postal_code"] = {
            "action": "set",
            "value": diff["zip_code"]["local"],
        }

    if "county" in diff:
        base_location_props["county"] = {
            "action": "set",
            "value": diff["county"]["local"],
        }

    if "people_present" in diff:
        base_props["people_present"] = {
            "action": "set",
            "value": diff["people_present"]["local"],
        }

    if "displaced_count" in diff:
        base_props["displacement_count"] = {
            "action": "set",
            "value": diff["displaced_count"]["local"],
        }

    if base_location_props:
        loc_action: dict = {"action": "patch", "properties": base_location_props}
        if neris_location.get("neris_uid") is not None:
            loc_action["neris_uid"] = neris_location["neris_uid"]
        base_props["location"] = loc_action

    if base_props:
        base_action: dict = {"action": "patch", "properties": base_props}
        if neris_base.get("neris_uid") is not None:
            base_action["neris_uid"] = neris_base["neris_uid"]
        properties["base"] = base_action

    # ── dispatch section ──
    dispatch_props: dict = {}

    if "timestamps" in diff:
        ts_local = diff["timestamps"]["local"]
        if "psap_answer" in ts_local:
            # psap_answer → call_arrival (when 911 call arrives at PSAP)
            dispatch_props["call_arrival"] = {
                "action": "set",
                "value": to_utc_iso(ts_local["psap_answer"]),
            }
            dispatch_props["call_answered"] = {
                "action": "set",
                "value": to_utc_iso(ts_local["psap_answer"]),
            }
        if "alarm_time" in ts_local:
            # alarm_time → call_create (when CAD incident is created)
            dispatch_props["call_create"] = {
                "action": "set",
                "value": to_utc_iso(ts_local["alarm_time"]),
            }
        # first_unit_dispatched is a read-only computed field in NERIS
        # (derived from earliest unit dispatch time) — not patchable.
        if "incident_clear" in ts_local:
            dispatch_props["incident_clear"] = {
                "action": "set",
                "value": to_utc_iso(ts_local["incident_clear"]),
            }

    if "dispatch_incident_number" in diff:
        # NERIS patch model uses "incident_number" (not "dispatch_incident_number")
        dispatch_props["incident_number"] = {
            "action": "set",
            "value": diff["dispatch_incident_number"]["local"],
        }

    if "automatic_alarm" in diff:
        dispatch_props["automatic_alarm"] = {
            "action": "set",
            "value": diff["automatic_alarm"]["local"],
        }

    if "units" in diff:
        # Unit fields are patched via dispatch.unit_responses as a list
        # of PatchDispatchUnitResponseAction (existing units with neris_uid)
        # or AppendDispatchUnitResponseAction (new units not yet in NERIS).
        neris_uids = diff["units"].get("neris_uids", {})
        reported_ids = diff["units"].get("reported_unit_ids", {})

        # Local field → NERIS field name mapping; fields not listed pass through as-is
        # (e.g. "staffing" and "response_mode" use the same name in NERIS).
        patch_field_map = {
            "dispatch": "dispatch",
            "enroute": "enroute_to_scene",
            "staged": "staging",
            "on_scene": "on_scene",
            "cleared": "unit_clear",
            "canceled": "canceled_enroute",
        }
        # Fields whose values are ISO timestamps and need UTC conversion
        ts_fields = set(patch_field_map.values())

        # Group local diffs by unit_id
        per_unit: dict[str, dict] = {}
        for key, val in diff["units"]["local"].items():
            uid, field = key.rsplit(".", 1)
            neris_field = patch_field_map.get(field, field)
            per_unit.setdefault(uid, {})[neris_field] = val

        unit_actions: list[dict] = []
        for unit_id, fields in per_unit.items():
            neris_uid = neris_uids.get(unit_id)
            if neris_uid is not None:
                # Existing unit — patch changed fields
                unit_props = {}
                for field_name, field_val in fields.items():
                    value = to_utc_iso(field_val) if field_name in ts_fields else field_val
                    unit_props[field_name] = {"action": "set", "value": value}
                unit_actions.append(
                    {
                        "neris_uid": neris_uid,
                        "action": "patch",
                        "properties": unit_props,
                    }
                )
            else:
                # New unit not in NERIS — append with full payload.
                # Use the NERIS reported_unit_id if available (preserves NERIS casing),
                # otherwise fall back to local unit_id.
                append_id = reported_ids.get(unit_id, unit_id)
                payload = {"reported_unit_id": append_id}
                for k, v in fields.items():
                    payload[k] = to_utc_iso(v) if k in ts_fields else v
                unit_actions.append(
                    {
                        "action": "append",
                        "value": payload,
                    }
                )

        if unit_actions:
            dispatch_props["unit_responses"] = unit_actions

    # Dispatch comments — append new notes as individual NERIS comments
    if "dispatch_comments" in diff:
        comment_actions = []
        for note in diff["dispatch_comments"]["local"]:
            # Format: "[UNIT] text" with unit prefix, UTC timestamp
            raw_text = f"[{note.unit}] {note.text}" if note.unit else note.text
            comment_text = _sanitize_for_neris(raw_text)
            comment_payload: dict = {"comment": comment_text}
            if note.timestamp:
                comment_payload["timestamp"] = to_utc_iso(note.timestamp)
            comment_actions.append({"action": "append", "value": comment_payload})
        if comment_actions:
            dispatch_props["comments"] = comment_actions

    if dispatch_props:
        dispatch_action: dict = {"action": "patch", "properties": dispatch_props}
        if neris_dispatch.get("neris_uid") is not None:
            dispatch_action["neris_uid"] = neris_dispatch["neris_uid"]
        properties["dispatch"] = dispatch_action

    if "incident_type" in diff:
        properties["incident_types"] = [
            {
                "action": "append",
                "value": {"type": diff["incident_type"]["local"]},
            },
        ]

    return properties


# ---------------------------------------------------------------------------
# Public MCP tools
# ---------------------------------------------------------------------------


async def submit_to_neris(incident_id: str, *, dry_run: bool = False) -> dict:
    """Push the local incident report to NERIS — creates or updates as needed.

    If the incident already has a ``neris_incident_id``, diffs the local data
    against the NERIS record and patches only fields that differ (same behavior
    as ``update_neris_incident``).  If no NERIS ID exists, builds a creation
    payload and POSTs a new record to NERIS, storing the returned ID.

    Does **not** lock the report — use ``finalize_incident`` to lock.
    Editors only.

    Args:
        incident_id: Local incident document ID (UUID)
        dry_run: If True, return a preview of the payload without submitting

    Returns:
        Result dict with NERIS ID on success, or an error
    """
    user = get_current_user()

    if not await check_is_editor(user.user_id, fallback=user.is_editor, email=user.email):
        group = get_org_config().editor_group_name
        return {
            "error": "You are not authorized to submit to NERIS. "
            f"Ask an administrator to add you to the {group} group in Entra ID."
        }

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return {"error": "Incident not found"}

    # ── Update path: existing NERIS record ──
    if doc.neris_incident_id:
        return await update_neris_incident(incident_id, dry_run=dry_run)

    # ── Create path: validate minimum required fields ──
    missing: list[str] = []
    if not doc.incident_type:
        missing.append("incident_type")
    if not doc.timestamps.get("psap_answer"):
        missing.append("timestamps.psap_answer")
    if not doc.units:
        missing.append("units (at least one unit required)")
    if not doc.address:
        missing.append("address")
    if missing:
        return {
            "error": "Cannot submit to NERIS — required fields are missing.",
            "missing_fields": missing,
            "hint": "Fill in the missing fields and try again.",
        }

    # Build the creation payload
    payload = _build_neris_creation_payload(doc)

    if dry_run:
        return {
            "status": "dry_run",
            "message": "Preview of the NERIS creation payload (not submitted).",
            "payload": _localize_creation_payload(payload),
        }

    # Submit to NERIS
    try:
        result = await asyncio.to_thread(_submit_to_neris, payload)
    except Exception as exc:
        logger.exception("Failed to submit incident %s to NERIS", incident_id)
        return {"error": f"NERIS submission failed: {exc}"}

    if "error" in result:
        return result

    neris_id = result.get("neris_id", "")

    # Store the returned NERIS ID on the document
    doc.neris_incident_id = neris_id
    doc.updated_at = datetime.now(UTC)
    doc.edit_history.append(
        EditEntry(
            editor_email=user.email,
            editor_name=user.name,
            fields_changed=["neris_submit_created"],
        )
    )

    async with IncidentStore() as store:
        await store.update(doc)

    logger.info(
        "User %s created NERIS record %s for incident %s",
        user.email,
        neris_id,
        incident_id,
    )

    return {
        "status": "created",
        "neris_id": neris_id,
        "incident_id": incident_id,
        "message": f"NERIS record created: {neris_id}",
    }


async def list_neris_incidents() -> dict:
    """List incidents from the NERIS federal reporting system.

    Returns incidents submitted to NERIS for this fire department.
    Officers only.

    Returns:
        List of NERIS incident summaries with incident number, date,
        status, and type information
    """
    user = get_current_user()

    if not await check_is_editor(user.user_id, fallback=user.is_editor, email=user.email):
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

    if not await check_is_editor(user.user_id, fallback=user.is_editor, email=user.email):
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


async def import_from_neris(
    neris_id: str,
    *,
    incident_id: str | None = None,
    incident_number: str | None = None,
    station: str = "S31",
    force: bool = False,
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
            (e.g., "FD53055879|26001980|1770500761") or a local dispatch
            number (e.g., "26-002548") which will be searched in NERIS.
        incident_id: Existing incident document ID to import into.
            When omitted a new draft is created from the NERIS data.
        incident_number: Override for dispatch incident number
            (e.g., "26-002358"). When NERIS doesn't store our CAD
            number in determinant_code, use this to link to dispatch.
        station: Station code for new incidents (default "S31",
            ignored when importing into an existing incident)
        force: Bypass the locked-status check, allowing reimport into
            submitted/approved incidents (e.g. after parser fixes).

    Returns:
        The incident document with an ``import_comparison`` key showing
        discrepancies, gaps filled, and crew on duty. Or an error dict.
    """
    from sjifire.ops.incidents.tools import (
        _build_import_comparison,
        _check_edit_access,
        _get_crew_for_incident,
        _prefill_from_dispatch,
    )

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

    # Use the record's actual compound NERIS ID (the lookup may have been
    # a dispatch number like "26-002548" instead of the compound ID).
    actual_neris_id = neris_record.get("neris_id") or neris_id
    neris_prefill = _parse_neris_record(neris_record, actual_neris_id)
    # Stash NERIS status for downstream hints (not persisted in the document)
    neris_status_info = neris_record.get("incident_status") or {}
    neris_prefill["_neris_status"] = neris_status_info.get("status", "")

    # ── 2. Derive dispatch number and date from NERIS record ──
    neris_dispatch = neris_record.get("dispatch") or {}
    neris_incident_number = incident_number or _neris_dispatch_to_cad_number(neris_dispatch)
    neris_call_create = neris_dispatch.get("call_create", "")

    # ── 3. Fetch dispatch data ──
    # Determine incident number: from existing doc, or from NERIS record
    if incident_id:
        async with IncidentStore() as store:
            doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}
        if not await _check_edit_access(doc, user.email, user.is_editor):
            return {"error": "You don't have permission to edit this incident"}
        if doc.status in _LOCKED_STATUSES and not force:
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
            doc, neris_prefill, dispatch_prefill, crew, comparison, user, force=force
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


# ---------------------------------------------------------------------------
# Import & merge helpers
# ---------------------------------------------------------------------------


def _merge_sub_model(doc, field_name: str, model_class, data: dict, *, force: bool = False) -> None:
    """Create or merge into a Pydantic sub-model field."""
    current = getattr(doc, field_name)
    if current is None:
        setattr(doc, field_name, model_class(**data))
    else:
        for k, v in data.items():
            if force or getattr(current, k, None) is None:
                setattr(current, k, v)


# Simple fields set from NERIS if present (fill-if-empty for existing docs)
_NERIS_SIMPLE_FIELDS: tuple[str, ...] = (
    "incident_type",
    "location_use",
    "narrative",
    "action_taken",
    "noaction_reason",
    "action_codes",
    "additional_incident_types",
    "arrival_conditions",
    "outside_fire_cause",
    "people_present",
    "displaced_count",
    "automatic_alarm",
)

# Fields that always overwrite from NERIS (corrected data)
_NERIS_OVERWRITE_FIELDS: tuple[str, ...] = (
    "incident_type",
    "location_use",
    "narrative",
)

# Location fields: NERIS preferred, dispatch fallback
_LOCATION_FIELDS: tuple[str, ...] = ("address", "city", "state", "zip_code", "county")


async def _apply_neris_import_to_existing(
    doc: IncidentDocument,
    neris_prefill: dict,
    dispatch_prefill: dict,
    crew: list[dict],
    comparison: dict,
    user,
    *,
    force: bool = False,
) -> dict:
    """Apply merged NERIS + dispatch + schedule data to an existing incident."""
    from sjifire.ops.incidents.tools import _overlay_crew_from_schedule

    fields_changed: list[str] = []

    # NERIS ID
    doc.neris_incident_id = neris_prefill.get("neris_incident_id", doc.neris_incident_id)
    fields_changed.append("neris_incident_id")

    # Simple NERIS fields: overwrite fields always from NERIS, fill-if-empty for the rest
    for field in _NERIS_SIMPLE_FIELDS:
        if field not in neris_prefill:
            continue
        if field in _NERIS_OVERWRITE_FIELDS or force or not getattr(doc, field, None):
            setattr(doc, field, neris_prefill[field])
            fields_changed.append(field)

    # outside_fire_acres uses None check (0.0 is a valid value)
    if "outside_fire_acres" in neris_prefill and (force or doc.outside_fire_acres is None):
        doc.outside_fire_acres = neris_prefill["outside_fire_acres"]
        fields_changed.append("outside_fire_acres")

    # Location fields: NERIS preferred, dispatch fallback
    for field in _LOCATION_FIELDS:
        if field in neris_prefill:
            setattr(doc, field, neris_prefill[field])
            if field == "address":
                fields_changed.append(field)
        elif field in dispatch_prefill and not getattr(doc, field, None):
            setattr(doc, field, dispatch_prefill[field])
            if field == "address":
                fields_changed.append(field)

    # Coordinates from dispatch (NERIS doesn't provide these)
    for coord in ("latitude", "longitude"):
        if coord in dispatch_prefill and getattr(doc, coord) is None:
            setattr(doc, coord, dispatch_prefill[coord])
            fields_changed.append(coord)

    # Units — prefer dispatch (local unit codes), fall back to NERIS
    units_source = dispatch_prefill.get("units") or neris_prefill.get("units") or None
    if units_source is not None:
        existing_personnel = {u.unit_id: u.personnel for u in doc.units}
        for u in units_source:
            if u.unit_id in existing_personnel:
                u.personnel = existing_personnel[u.unit_id]
        doc.units = units_source
        fields_changed.append("units")

    # Timestamps — dispatch is ground truth, NERIS fills gaps
    merged_ts = {**doc.timestamps}
    neris_ts = neris_prefill.get("timestamps", {})
    dispatch_ts = dispatch_prefill.get("timestamps", {})
    merged_ts.update(dispatch_ts)
    for k, v in neris_ts.items():
        if k not in merged_ts:
            merged_ts[k] = v
    if merged_ts != doc.timestamps:
        doc.timestamps = merged_ts
        fields_changed.append("timestamps")

    # Dispatch comments (joined blob)
    if "dispatch_comments" in dispatch_prefill and not doc.dispatch_comments:
        doc.dispatch_comments = dispatch_prefill["dispatch_comments"]
        fields_changed.append("dispatch_comments")

    # Dispatch notes (individual timestamped entries for NERIS comments)
    notes_source = dispatch_prefill.get("dispatch_notes") or neris_prefill.get("dispatch_notes")
    if notes_source and not doc.dispatch_notes:
        doc.dispatch_notes = notes_source
        fields_changed.append("dispatch_notes")

    # Typed sub-models (fire detail, alarm info, hazard info)
    for field_name, cls in (
        ("fire_detail", FireDetail),
        ("alarm_info", AlarmInfo),
        ("hazard_info", HazardInfo),
    ):
        if field_name in neris_prefill:
            _merge_sub_model(doc, field_name, cls, neris_prefill[field_name], force=force)
            fields_changed.append(field_name)

    # Remaining extras (medical, casualty, displacement, etc.)
    neris_extras = neris_prefill.get("extras", {})
    if neris_extras:
        merged_extras = {**doc.extras}
        for k, v in neris_extras.items():
            if force or k not in merged_extras:
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
    from sjifire.ops.incidents.tools import _overlay_crew_from_schedule

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
        "incident_type",
        "narrative",
        "address",
        "city",
        "state",
        "neris_incident_id",
        "location_use",
        "action_taken",
        "noaction_reason",
        "action_codes",
        "additional_incident_types",
        "arrival_conditions",
        "outside_fire_cause",
        "outside_fire_acres",
        "zip_code",
        "county",
        "people_present",
        "displaced_count",
        "automatic_alarm",
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

    # Dispatch notes: prefer dispatch (individual CAD notes), fall back to NERIS
    if "dispatch_notes" not in merged and "dispatch_notes" in neris_prefill:
        merged["dispatch_notes"] = neris_prefill["dispatch_notes"]

    units = merged.get("units", [])

    # Overlay crew from schedule
    if crew and units:
        _overlay_crew_from_schedule(units, crew)

    # Typed sub-models from NERIS prefill
    fire_detail = neris_prefill.get("fire_detail")
    alarm_info = neris_prefill.get("alarm_info")
    hazard_info = neris_prefill.get("hazard_info")

    # Remaining extras (medical, casualty, displacement, etc.)
    merged_extras = {}
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
        zip_code=merged.get("zip_code", ""),
        county=merged.get("county", ""),
        latitude=merged.get("latitude"),
        longitude=merged.get("longitude"),
        units=units,
        timestamps=merged.get("timestamps", {}),
        narrative=merged.get("narrative", ""),
        dispatch_comments=merged.get("dispatch_comments", ""),
        dispatch_notes=merged.get("dispatch_notes", []),
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
        station=station,
        fire_detail=fire_detail,
        alarm_info=alarm_info,
        hazard_info=hazard_info,
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
            "by calling finalize_incident to mark it as submitted."
        )

    return result


async def update_neris_incident(
    incident_id: str,
    fields: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Push corrections from the local incident report to the NERIS record.

    Compares local data against the current NERIS record, takes a snapshot
    of the NERIS state before any changes (stored for 30 days), then patches
    only the fields that differ. Editors only.

    Args:
        incident_id: Local incident document ID
        fields: Optional list of field names to update (e.g. ["narrative",
            "timestamps"]). If omitted, updates all differing fields.
        dry_run: If True, return the diff without applying changes.

    Returns:
        Summary of what was updated, or an error
    """
    user = get_current_user()

    if not await check_is_editor(user.user_id, fallback=user.is_editor, email=user.email):
        group = get_org_config().editor_group_name
        return {
            "error": "You are not authorized to update NERIS records. "
            f"Ask an administrator to add you to the {group} group in Entra ID."
        }

    # 1. Load local incident
    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return {"error": "Incident not found"}

    if not doc.neris_incident_id:
        return {
            "error": "This incident has no linked NERIS record. "
            "Import from NERIS first using import_from_neris."
        }

    # 2. Fetch current NERIS record
    try:
        neris_record = await asyncio.to_thread(_get_neris_incident, doc.neris_incident_id)
    except Exception:
        logger.warning("Failed to fetch NERIS record %s", doc.neris_incident_id, exc_info=True)
        return {"error": "Failed to fetch NERIS record. Try again later."}

    if not neris_record:
        return {"error": f"NERIS record not found: {doc.neris_incident_id}"}

    # 3. Check NERIS status
    neris_status = (neris_record.get("incident_status") or {}).get("status", "")

    # 4. Build diff between local and NERIS
    diff = _build_neris_diff(doc, neris_record)

    # Filter to requested fields if specified
    if fields:
        diff = {k: v for k, v in diff.items() if k in fields}

    if not diff:
        return {
            "status": "no_changes",
            "message": "Local data matches the NERIS record — nothing to update.",
            "neris_id": doc.neris_incident_id,
        }

    approved_warning = ""
    if neris_status == "APPROVED":
        approved_warning = (
            " WARNING: This NERIS record is APPROVED. Pushing changes will "
            "overwrite the approved record. Confirm with the user before proceeding."
        )

    if dry_run:
        result = {
            "status": "dry_run",
            "neris_id": doc.neris_incident_id,
            "neris_status": neris_status,
            "diff": _localize_diff_timestamps(diff),
            "fields_available": list(diff.keys()),
            "message": f"{len(diff)} field(s) differ between local and NERIS.{approved_warning}",
        }
        if neris_status == "APPROVED":
            result["approved_warning"] = True
        return result

    # 5. Build NERIS patch properties
    properties = _build_neris_patch(diff, neris_record)

    if not properties:
        return {
            "status": "no_changes",
            "message": "No patchable differences found.",
            "neris_id": doc.neris_incident_id,
        }

    # 6. Take snapshot before patching
    from sjifire.ops.neris.models import NerisSnapshotDocument
    from sjifire.ops.neris.store import NerisSnapshotStore

    snapshot_doc = NerisSnapshotDocument(
        year=doc.year,
        neris_id=doc.neris_incident_id,
        incident_id=doc.id,
        incident_number=doc.incident_number,
        snapshot=neris_record,
        patches_applied=properties,
        patched_by=user.email,
    )

    async with NerisSnapshotStore() as snap_store:
        await snap_store.create(snapshot_doc)

    logger.info(
        "Created NERIS snapshot %s before patching %s",
        snapshot_doc.id,
        doc.neris_incident_id,
    )

    # 7. Apply patch to NERIS
    try:
        patch_result = await asyncio.to_thread(
            _patch_neris_incident, doc.neris_incident_id, properties
        )
    except Exception as exc:
        logger.exception("Failed to patch NERIS incident %s", doc.neris_incident_id)
        return {
            "error": f"Failed to update NERIS record: {exc}. "
            "The snapshot was saved — no data was lost.",
            "snapshot_id": snapshot_doc.id,
        }

    logger.info(
        "User %s patched NERIS %s: fields=%s",
        user.email,
        doc.neris_incident_id,
        list(diff.keys()),
    )

    # If dispatch comments were pushed, update local dispatch_notes
    # to the split format so future diffs don't re-push.
    if "dispatch_comments" in diff and doc.dispatch_comments:
        from sjifire.ops.incidents.tools import _parse_cad_comments

        call_ts = doc.timestamps.get("psap_answer", "")
        reparsed = _parse_cad_comments(doc.dispatch_comments, call_ts=call_ts)
        if len(reparsed) > len(doc.dispatch_notes):
            doc.dispatch_notes = reparsed
            doc.updated_at = datetime.now(UTC)
            async with IncidentStore() as store:
                await store.update(doc)
            logger.info("Updated local dispatch_notes to split format for %s", doc.id)

    return {
        "status": "updated",
        "neris_id": doc.neris_incident_id,
        "fields_updated": list(diff.keys()),
        "snapshot_id": snapshot_doc.id,
        "patch_result": patch_result,
    }


async def finalize_incident(incident_id: str, *, skip_neris: bool = False) -> dict:
    """Lock an incident report, optionally pushing to NERIS first.

    When ``skip_neris`` is False (the default), calls ``submit_to_neris``
    before locking — this creates a new NERIS record if none exists, or
    updates the existing one with local corrections.

    When ``skip_neris`` is True, locks the report locally without NERIS
    and records that the export was declined.

    Only editors can finalize incidents.

    Args:
        incident_id: The incident document ID
        skip_neris: If True, close the report without NERIS export.
            Sets ``extras.neris_export_declined = True``.

    Returns:
        The updated incident document, or an error
    """
    user = get_current_user()

    if not await check_is_editor(user.user_id, fallback=user.is_editor, email=user.email):
        group = get_org_config().editor_group_name
        return {
            "error": "You are not authorized to finalize incidents. "
            f"Ask an administrator to add you to the {group} group in Entra ID."
        }

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        return {"error": "Incident not found"}

    if doc.status in _LOCKED_STATUSES:
        return {
            "error": f"Incident is already {doc.status} and locked. "
            "No further changes can be made locally."
        }

    neris_result = None

    if skip_neris:
        finalize_note = "finalized_no_neris"
    else:
        # Push to NERIS first (create or update)
        neris_result = await submit_to_neris(incident_id)
        if isinstance(neris_result, dict) and "error" in neris_result:
            return neris_result  # Don't lock if NERIS push failed
        finalize_note = "finalized"

    # Re-fetch the doc in case submit_to_neris updated it (e.g. set neris_incident_id)
    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)
        if doc is None:
            return {"error": "Incident not found"}

        if skip_neris:
            doc.extras = {**doc.extras, "neris_export_declined": True}

        doc.status = "submitted"
        doc.updated_at = datetime.now(UTC)
        doc.edit_history.append(
            EditEntry(
                editor_email=user.email,
                editor_name=user.name,
                fields_changed=[finalize_note],
            )
        )

        updated = await store.update(doc)

    neris_id = updated.neris_incident_id or "n/a"
    logger.info(
        "User %s finalized incident %s → submitted (neris_id: %s, skip_neris: %s)",
        user.email,
        incident_id,
        neris_id,
        skip_neris,
    )
    result = updated.model_dump(mode="json")
    if neris_result:
        result["neris_result"] = neris_result
    return result
