"""NERIS-specific incident functions.

Handles parsing, diffing, patching, and import/export of NERIS incident records.
These functions are re-exported from ``tools.py`` for backward compatibility so
that callers using ``incident_tools.import_from_neris`` etc. continue to work.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sjifire.core.config import get_org_config, get_timezone
from sjifire.ops.auth import check_is_editor, get_current_user
from sjifire.ops.incidents.models import (
    EditEntry,
    IncidentDocument,
    UnitAssignment,
)
from sjifire.ops.incidents.store import IncidentStore

logger = logging.getLogger(__name__)

_LOCKED_STATUSES = {"submitted", "approved"}

# Ephemeral cache: NERIS unit ID → local CAD designation (e.g. FD53055879S001U000 → E31).
# Rebuilt from NERIS entity API on first use; lost on restart (acceptable).
_neris_unit_map: dict[str, str] = {}


def _resolve_neris_unit_id(neris_unit_id: str) -> str:
    """Map a NERIS unit ID to local CAD designation, fetching entity data if needed.

    Falls back to the raw NERIS ID if the mapping isn't available
    (e.g. credentials not set, API unreachable).
    """
    if not neris_unit_id:
        return neris_unit_id
    if _neris_unit_map:
        return _neris_unit_map.get(neris_unit_id, neris_unit_id)

    # Try to populate the map from the NERIS entity API
    try:
        from sjifire.neris.client import NerisClient

        with NerisClient() as client:
            entity = client.get_entity()
        for station in entity.get("stations", []):
            for unit in station.get("units", []):
                uid = unit.get("neris_id", "")
                cad = unit.get("cad_designation_1", "")
                if uid and cad:
                    _neris_unit_map[uid] = cad
        logger.info("Loaded %d NERIS unit mappings", len(_neris_unit_map))
    except Exception:
        logger.debug("Could not load NERIS unit mappings", exc_info=True)
        return neris_unit_id

    return _neris_unit_map.get(neris_unit_id, neris_unit_id)


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
        zip_code = loc.get("postal_code") or ""
        if zip_code:
            prefill["zip_code"] = zip_code
        county = loc.get("county") or ""
        if county:
            prefill["county"] = county

    # Unit responses from dispatch
    dispatch = record.get("dispatch") or {}
    neris_units = dispatch.get("unit_responses") or []
    if neris_units:
        units = []
        for u in neris_units:
            raw_id = u.get("reported_unit_id") or _resolve_neris_unit_id(u.get("unit_neris_id", ""))
            unit = UnitAssignment(
                unit_id=raw_id,
                response_mode=u.get("response_mode") or "",
                dispatch=u.get("dispatch") or "",
                enroute=u.get("enroute_to_scene") or "",
                staged=u.get("staging") or "",
                on_scene=u.get("on_scene") or "",
                cleared=u.get("unit_clear") or "",
                canceled=u.get("canceled_enroute") or "",
            )
            # Staffing count into unit comment if available
            staffing = u.get("staffing")
            if staffing is not None:
                unit.comment = f"Staffing: {staffing}"
            units.append(unit)
        prefill["units"] = units

    # Automatic alarm from dispatch
    auto_alarm = dispatch.get("automatic_alarm")
    if auto_alarm is not None:
        prefill["automatic_alarm"] = auto_alarm

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

        progression = location_detail.get("progression_evident")
        if progression is not None:
            extras["fire_progression_evident"] = progression

        water_supply = fire_detail.get("water_supply")
        if water_supply:
            extras["water_supply"] = water_supply
        investigation = fire_detail.get("investigation_needed")
        if investigation:
            extras["fire_investigation"] = investigation
        inv_types = fire_detail.get("investigation_types")
        if inv_types:
            extras["fire_investigation_types"] = inv_types
        supp_appliances = fire_detail.get("suppression_appliances")
        if supp_appliances:
            extras["suppression_appliances"] = supp_appliances

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
            extras[extras_key] = ptype

    # Smoke alarm details when present
    smoke_alarm = record.get("smoke_alarm") or {}
    smoke_presence = smoke_alarm.get("presence") or {}
    if smoke_presence.get("type") == "PRESENT":
        alarm_types = smoke_presence.get("alarm_types")
        if alarm_types:
            extras["smoke_alarm_types"] = alarm_types
        operation = smoke_presence.get("operation") or {}
        alerted = operation.get("alerted_failed_other") or {}
        op_type = alerted.get("type")
        if op_type:
            extras["smoke_alarm_operation"] = op_type
        occ_action = alerted.get("occupant_action")
        if occ_action:
            extras["smoke_alarm_occupant_action"] = occ_action

    # --- Hazards (top-level in NERIS response) ---
    electric_hazards = record.get("electric_hazards") or []
    if electric_hazards:
        eh_types = [eh.get("type") for eh in electric_hazards if eh.get("type")]
        extras["electric_hazards"] = eh_types if eh_types else True
    powergen = record.get("powergen_hazards") or []
    for pg in powergen:
        # Real structure: pg.pv_other.type (not pg.type)
        pv_other = pg.get("pv_other") or {} if isinstance(pg, dict) else {}
        pg_type = pv_other.get("type") or ""
        if not pg_type:
            continue
        if "SOLAR" in pg_type.upper() or "PV" in pg_type.upper():
            extras["solar_present"] = "YES"
        elif "BATTERY" in pg_type.upper() or "ESS" in pg_type.upper():
            extras["battery_ess_present"] = "YES"
        elif "GENERATOR" in pg_type.upper():
            extras["generator_present"] = "YES"
        elif pg_type != "NOT_APPLICABLE":
            extras["powergen_type"] = pg_type
    csst = record.get("csst_hazard") or {}
    if csst:
        # CSST is an ignition source concern — ignition_source is the key field.
        # lightning_suspected is a sub-detail. A truthy string like "UNKNOWN"
        # does NOT mean CSST was present.
        ignition = csst.get("ignition_source")
        if ignition is True:
            extras["csst_present"] = "YES"
        elif ignition is False:
            extras["csst_present"] = "NO"
        else:
            extras["csst_present"] = "UNKNOWN"
        lightning = csst.get("lightning_suspected")
        if lightning and lightning != "UNKNOWN":
            extras["csst_lightning_suspected"] = lightning
        grounded = csst.get("grounded")
        if grounded is not None:
            extras["csst_grounded"] = grounded

    # --- People & occupancy (in base) ---
    people_present = base.get("people_present")
    if people_present is not None:
        prefill["people_present"] = people_present
    displaced = base.get("displacement_count")
    if displaced is not None:
        prefill["displaced_count"] = displaced
    displacement_causes = base.get("displacement_causes")
    if displacement_causes:
        extras["displacement_causes"] = displacement_causes
    animals_rescued = base.get("animals_rescued")
    if animals_rescued is not None:
        extras["animals_rescued"] = animals_rescued

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
        "completed_sizeup",
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
    if casualty_rescues:
        cr_list = []
        for cr in casualty_rescues:
            cr_entry: dict = {}
            cr_entry["type"] = cr.get("type", "")  # FF or NONFF
            cr_entry["gender"] = cr.get("gender")

            # Casualty info
            casualty = cr.get("casualty") or {}
            injury = casualty.get("injury_or_noninjury") or {}
            if injury:
                cr_entry["injury_type"] = injury.get("type")  # INJURED_NONFATAL, etc.
                cr_entry["injury_cause"] = injury.get("cause")  # EXPOSURE, etc.

            # Rescue info — nested under ffrescue_or_nonffrescue
            rescue = cr.get("rescue") or {}
            ff_rescue = rescue.get("ffrescue_or_nonffrescue") or {}
            if ff_rescue:
                cr_entry["rescue_type"] = ff_rescue.get("type")  # RESCUED_BY_FIREFIGHTER, etc.
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

            # Strip None values
            cr_list.append({k: v for k, v in cr_entry.items() if v is not None})
        extras["casualty_rescues"] = cr_list

    # --- Non-FD aids (top-level in NERIS response) ---
    nonfd_aids = record.get("nonfd_aids") or []
    if nonfd_aids:
        extras["nonfd_aids"] = [a.get("type") for a in nonfd_aids if a.get("type")]

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
                neris_id_entity=client.entity_id,
                body=payload,
            )
            neris_id = result.get("neris_id") or result.get("id", "")
            return {"neris_id": neris_id}
    except Exception as e:
        logger.exception("NERIS submission failed")
        return {"error": f"NERIS submission failed: {e}", "details": str(e)}


def _build_neris_diff(doc: IncidentDocument, neris_record: dict) -> dict:
    """Compare local incident fields against the NERIS record.

    Returns a dict of field_name → {"local": ..., "neris": ...} for
    fields that differ.
    """
    diff: dict = {}
    base = neris_record.get("base") or {}
    dispatch = neris_record.get("dispatch") or {}

    # Narrative
    neris_narrative = base.get("outcome_narrative") or ""
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

    # Dispatch-level timestamps
    ts_map = {
        "psap_answer": ("call_create", dispatch),
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

    # Unit-level timestamps
    neris_units = dispatch.get("unit_responses") or []
    neris_unit_map_local: dict[str, dict] = {}
    for nu in neris_units:
        uid = nu.get("reported_unit_id") or _resolve_neris_unit_id(nu.get("unit_neris_id", ""))
        if uid:
            neris_unit_map_local[uid] = nu

    field_map = {
        "dispatch": "dispatch",
        "enroute": "enroute_to_scene",
        "staged": "staging",
        "on_scene": "on_scene",
        "cleared": "unit_clear",
        "canceled": "canceled_enroute",
    }
    for unit in doc.units:
        neris_unit = neris_unit_map_local.get(unit.unit_id, {})
        neris_uid = neris_unit.get("neris_uid")  # NERIS internal ID for patching
        for local_field, neris_field in field_map.items():
            local_val = getattr(unit, local_field, "")
            neris_val = neris_unit.get(neris_field) or ""
            if local_val and not _timestamps_equal(local_val, neris_val):
                diff.setdefault("units", {"local": {}, "neris": {}, "neris_uids": {}})
                key = f"{unit.unit_id}.{local_field}"
                diff["units"]["local"][key] = local_val
                diff["units"]["neris"][key] = neris_val
                if neris_uid is not None:
                    diff["units"]["neris_uids"][unit.unit_id] = neris_uid

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

    return diff


def _build_neris_patch(diff: dict) -> dict:
    """Convert a diff dict into NERIS patch properties format.

    Each field uses ``{"action": "set", "value": ...}`` format.
    """
    properties: dict = {}

    if "narrative" in diff:
        properties.setdefault("base", {})
        properties["base"]["outcome_narrative"] = {
            "action": "set",
            "value": diff["narrative"]["local"],
        }

    if "address" in diff:
        properties.setdefault("base", {}).setdefault("location", {})
        properties["base"]["location"]["street_address"] = {
            "action": "set",
            "value": diff["address"]["local"],
        }

    if "city" in diff:
        properties.setdefault("base", {}).setdefault("location", {})
        properties["base"]["location"]["incorporated_municipality"] = {
            "action": "set",
            "value": diff["city"]["local"],
        }

    if "state" in diff:
        properties.setdefault("base", {}).setdefault("location", {})
        properties["base"]["location"]["state"] = {
            "action": "set",
            "value": diff["state"]["local"],
        }

    if "zip_code" in diff:
        properties.setdefault("base", {}).setdefault("location", {})
        properties["base"]["location"]["postal_code"] = {
            "action": "set",
            "value": diff["zip_code"]["local"],
        }

    if "people_present" in diff:
        properties.setdefault("base", {})
        properties["base"]["people_present"] = {
            "action": "set",
            "value": diff["people_present"]["local"],
        }

    if "displaced_count" in diff:
        properties.setdefault("base", {})
        properties["base"]["displacement_count"] = {
            "action": "set",
            "value": diff["displaced_count"]["local"],
        }

    if "timestamps" in diff:
        ts_local = diff["timestamps"]["local"]
        if "psap_answer" in ts_local:
            properties.setdefault("dispatch", {})
            properties["dispatch"]["call_create"] = {
                "action": "set",
                "value": ts_local["psap_answer"],
            }
        if "first_unit_dispatched" in ts_local:
            properties.setdefault("dispatch", {})
            properties["dispatch"]["first_unit_dispatched"] = {
                "action": "set",
                "value": ts_local["first_unit_dispatched"],
            }
        if "incident_clear" in ts_local:
            properties.setdefault("dispatch", {})
            properties["dispatch"]["incident_clear"] = {
                "action": "set",
                "value": ts_local["incident_clear"],
            }

    if "automatic_alarm" in diff:
        properties.setdefault("dispatch", {})
        properties["dispatch"]["automatic_alarm"] = {
            "action": "set",
            "value": diff["automatic_alarm"]["local"],
        }

    if "units" in diff:
        # Unit timestamps are patched via dispatch.unit_responses as a list
        # of PatchDispatchUnitResponseAction (existing units with neris_uid)
        # or AppendDispatchUnitResponseAction (new units not yet in NERIS).
        neris_uids = diff["units"].get("neris_uids", {})

        # Group local diffs by unit_id
        per_unit: dict[str, dict] = {}
        for key, val in diff["units"]["local"].items():
            uid, field = key.rsplit(".", 1)
            neris_field_map = {
                "dispatch": "dispatch",
                "enroute": "enroute_to_scene",
                "staged": "staging",
                "on_scene": "on_scene",
                "cleared": "unit_clear",
                "canceled": "canceled_enroute",
            }
            neris_field = neris_field_map.get(field, field)
            per_unit.setdefault(uid, {})[neris_field] = val

        unit_actions: list[dict] = []
        for unit_id, fields in per_unit.items():
            neris_uid = neris_uids.get(unit_id)
            if neris_uid is not None:
                # Existing unit — patch its timestamp fields
                unit_props = {}
                for field_name, field_val in fields.items():
                    unit_props[field_name] = {"action": "set", "value": field_val}
                unit_actions.append({
                    "neris_uid": neris_uid,
                    "action": "patch",
                    "properties": unit_props,
                })
            else:
                # New unit not in NERIS — append with full payload
                payload = {"reported_unit_id": unit_id, **fields}
                unit_actions.append({
                    "action": "append",
                    "value": payload,
                })

        if unit_actions:
            properties.setdefault("dispatch", {})
            properties["dispatch"]["unit_responses"] = unit_actions

    if "incident_type" in diff:
        properties["incident_types"] = {
            "action": "set",
            "value": [{"type": diff["incident_type"]["local"]}],
        }

    return properties


# ---------------------------------------------------------------------------
# Public MCP tools
# ---------------------------------------------------------------------------


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
            (e.g., "FD53055879|26001980|1770500761")
        incident_id: Existing incident document ID to import into.
            When omitted a new draft is created from the NERIS data.
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

    neris_prefill = _parse_neris_record(neris_record, neris_id)
    # Stash NERIS status for downstream hints (not persisted in the document)
    neris_status_info = neris_record.get("incident_status") or {}
    neris_prefill["_neris_status"] = neris_status_info.get("status", "")

    # ── 2. Derive dispatch number and date from NERIS record ──
    neris_dispatch = neris_record.get("dispatch") or {}
    neris_incident_number = _neris_dispatch_to_cad_number(neris_dispatch)
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

    # 3. Check NERIS status — reject if APPROVED (locked)
    neris_status = (neris_record.get("incident_status") or {}).get("status", "")
    if neris_status == "APPROVED":
        return {
            "error": "NERIS record is APPROVED and locked. "
            "It cannot be modified. Contact NERIS support to reopen it."
        }

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

    if dry_run:
        return {
            "status": "dry_run",
            "neris_id": doc.neris_incident_id,
            "neris_status": neris_status,
            "diff": diff,
            "fields_available": list(diff.keys()),
            "message": f"{len(diff)} field(s) differ between local and NERIS.",
        }

    # 5. Build NERIS patch properties
    properties = _build_neris_patch(diff)

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
    except Exception:
        logger.exception("Failed to patch NERIS incident %s", doc.neris_incident_id)
        return {
            "error": "Failed to update NERIS record. The snapshot was saved — "
            "no data was lost. Try again later.",
            "snapshot_id": snapshot_doc.id,
        }

    logger.info(
        "User %s patched NERIS %s: fields=%s",
        user.email,
        doc.neris_incident_id,
        list(diff.keys()),
    )

    return {
        "status": "updated",
        "neris_id": doc.neris_incident_id,
        "fields_updated": list(diff.keys()),
        "snapshot_id": snapshot_doc.id,
        "patch_result": patch_result,
    }


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
