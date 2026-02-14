"""MCP tools for incident management.

Provides CRUD operations with role-based access control:
- Any authenticated user can create incidents
- Creator and crew members can view their incidents
- Officers (Entra group) can view all incidents and submit to NERIS
- Only creator and officers can edit incidents

NERIS interaction is only through this module (no separate NERIS tools).
"""

import asyncio
import logging
from datetime import UTC, datetime

from cachetools import TTLCache

from sjifire.core.config import get_org_config
from sjifire.mcp.auth import get_current_user
from sjifire.mcp.incidents.models import CrewAssignment, IncidentDocument, Narratives
from sjifire.mcp.incidents.store import IncidentStore

logger = logging.getLogger(__name__)

_EDITABLE_STATUSES = {"draft", "in_progress", "ready_review"}
_RESETTABLE_STATUSES = {"draft", "in_progress"}

# One reset per user per 24 hours (server-side enforced).
# Resets on server restart — acceptable for accidental-destruction prevention.
_reset_cooldowns: TTLCache[str, str] = TTLCache(maxsize=1024, ttl=86400)


def _extract_timestamps(responder_details: list[dict]) -> dict[str, str]:
    """Extract NERIS event timestamps from dispatch responder details.

    Maps iSpyFire responder status changes to NERIS timestamp fields:
    - time_reported → psap_answer (from call creation)
    - First "Dispatch" → first_unit_dispatched
    - First "Enroute" → first_unit_enroute
    - First "On Scene" → first_unit_arrived

    Args:
        responder_details: List of responder status dicts from dispatch

    Returns:
        Dict of NERIS timestamp field → ISO datetime string
    """
    timestamps: dict[str, str] = {}
    status_map = {
        "Dispatch": "first_unit_dispatched",
        "Dispatched": "first_unit_dispatched",
        "Enroute": "first_unit_enroute",
        "On Scene": "first_unit_arrived",
    }

    for detail in responder_details:
        status = detail.get("status", "")
        time_str = detail.get("time_of_status_change", "")
        if not status or not time_str:
            continue

        neris_field = status_map.get(status)
        if neris_field and neris_field not in timestamps:
            timestamps[neris_field] = str(time_str)

    return timestamps


async def _prefill_from_dispatch(incident_number: str) -> dict:
    """Look up dispatch data and return pre-fill fields for an incident.

    Both ``create_incident`` and ``reset_incident`` call this to populate
    address, coordinates, and timestamps from dispatch records.

    Args:
        incident_number: Dispatch ID (e.g. "26-000944")

    Returns:
        Dict of pre-fill field values, or empty dict if dispatch not found
    """
    from sjifire.mcp.dispatch.store import DispatchStore

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

    # Extract timestamps from responder details
    ts = _extract_timestamps(dispatch.responder_details)
    if dispatch.time_reported:
        ts["psap_answer"] = dispatch.time_reported.isoformat()
    if ts:
        prefill["timestamps"] = ts

    return prefill


def _check_view_access(doc: IncidentDocument, user_email: str, is_officer: bool) -> bool:
    """Check if user can view this incident."""
    return is_officer or doc.created_by == user_email or user_email in doc.crew_emails()


def _check_edit_access(doc: IncidentDocument, user_email: str, is_officer: bool) -> bool:
    """Check if user can edit this incident."""
    return is_officer or doc.created_by == user_email


async def create_incident(
    incident_number: str,
    incident_date: str,
    station: str,
    *,
    incident_type: str | None = None,
    address: str | None = None,
    crew: list[dict] | None = None,
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
              "position" (optional), "unit" (optional)

    Returns:
        The created incident document with its ID
    """
    user = get_current_user()

    # Pre-fill from dispatch data (address, coordinates, timestamps)
    prefill = await _prefill_from_dispatch(incident_number)

    crew_assignments = [
        CrewAssignment(
            name=c["name"],
            email=c.get("email"),
            rank=c.get("rank", ""),
            position=c.get("position", ""),
            unit=c.get("unit", ""),
        )
        for c in (crew or [])
    ]

    doc = IncidentDocument(
        station=station,
        incident_number=incident_number,
        incident_date=datetime.strptime(incident_date, "%Y-%m-%d").date(),
        incident_type=incident_type,
        address=address if address is not None else prefill.get("address"),
        city=prefill.get("city", ""),
        state=prefill.get("state", ""),
        latitude=prefill.get("latitude"),
        longitude=prefill.get("longitude"),
        crew=crew_assignments,
        timestamps=prefill.get("timestamps", {}),
        created_by=user.email,
    )

    async with IncidentStore() as store:
        created = await store.create(doc)

    logger.info("User %s created incident %s", user.email, created.id)
    return created.model_dump(mode="json")


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

    if not _check_view_access(doc, user.email, user.is_officer):
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
        if user.is_officer:
            incidents = await store.list_by_status(
                status, station=station, exclude_status=exclude_status
            )
        else:
            incidents = await store.list_for_user(
                user.email, status=status, exclude_status=exclude_status
            )

    summaries = [
        {
            "id": doc.id,
            "incident_number": doc.incident_number,
            "incident_date": doc.incident_date.isoformat(),
            "station": doc.station,
            "status": doc.status,
            "incident_type": doc.incident_type,
            "created_by": doc.created_by,
            "crew_count": len(doc.crew),
            "neris_incident_id": doc.neris_incident_id,
        }
        for doc in incidents
    ]

    return {"incidents": summaries, "count": len(summaries)}


async def update_incident(
    incident_id: str,
    *,
    station: str | None = None,
    status: str | None = None,
    incident_type: str | None = None,
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
) -> dict:
    """Update fields on an existing incident.

    Only the incident creator and officers can edit. Submitted incidents
    cannot be modified.

    Args:
        incident_id: The incident document ID
        station: Update station code (e.g., "S31")
        status: New status (draft, in_progress, ready_review)
        incident_type: NERIS incident type code
        address: Incident address
        city: City (defaults to Friday Harbor)
        latitude: GPS latitude
        longitude: GPS longitude
        crew: Replace crew list (each entry: name, email, rank, position, unit)
        outcome_narrative: What happened
        actions_taken_narrative: What actions were taken
        unit_responses: NERIS apparatus/unit response data
        timestamps: Event timestamps (dispatch, on_scene, etc.)
        internal_notes: Internal notes (not sent to NERIS)

    Returns:
        The updated incident document, or an error
    """
    user = get_current_user()

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}

        if not _check_edit_access(doc, user.email, user.is_officer):
            return {"error": "You don't have permission to edit this incident"}

        if doc.status == "submitted":
            return {"error": "Cannot modify a submitted incident"}

        # Apply updates (only non-None values)
        if status is not None:
            if status == "submitted":
                return {"error": "Use submit_incident to submit"}
            if status not in _EDITABLE_STATUSES:
                valid = ", ".join(sorted(_EDITABLE_STATUSES))
                return {"error": f"Invalid status '{status}'. Must be one of: {valid}"}
            doc.status = status

        if station is not None:
            doc.station = station
        if incident_type is not None:
            doc.incident_type = incident_type
        if address is not None:
            doc.address = address
        if city is not None:
            doc.city = city
        if latitude is not None:
            doc.latitude = latitude
        if longitude is not None:
            doc.longitude = longitude

        if crew is not None:
            doc.crew = [
                CrewAssignment(
                    name=c["name"],
                    email=c.get("email"),
                    rank=c.get("rank", ""),
                    position=c.get("position", ""),
                    unit=c.get("unit", ""),
                )
                for c in crew
            ]

        if outcome_narrative is not None or actions_taken_narrative is not None:
            doc.narratives = Narratives(
                outcome=(
                    outcome_narrative if outcome_narrative is not None else doc.narratives.outcome
                ),
                actions_taken=(
                    actions_taken_narrative
                    if actions_taken_narrative is not None
                    else doc.narratives.actions_taken
                ),
            )

        if unit_responses is not None:
            doc.unit_responses = unit_responses
        if timestamps is not None:
            doc.timestamps = {**doc.timestamps, **timestamps}
        if internal_notes is not None:
            doc.internal_notes = internal_notes

        doc.updated_at = datetime.now(UTC)
        updated = await store.update(doc)

    logger.info("User %s updated incident %s", user.email, incident_id)
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

    if not user.is_officer:
        group = get_org_config().officer_group_name
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

    # --- NERIS submission (disabled until credentials are configured) ---

    async with IncidentStore() as store:  # pragma: no cover
        doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}

        if doc.status != "ready_review":
            return {
                "error": f"Incident must be in 'ready_review' status to submit "
                f"(current: {doc.status})"
            }

        # Build the NERIS payload
        payload = doc.to_neris_payload()

        # Submit to NERIS (synchronous client, run in thread pool)
        result = await asyncio.to_thread(_submit_to_neris, payload)

        if result.get("error"):
            return {"error": result["error"], "details": result.get("details")}

        # Update local record with NERIS ID and status
        doc.status = "submitted"
        doc.neris_incident_id = result.get("neris_id")
        doc.updated_at = datetime.now(UTC)
        await store.update(doc)

    logger.info(
        "User %s submitted incident %s to NERIS (neris_id=%s)",
        user.email,
        incident_id,
        doc.neris_incident_id,
    )

    return {
        "status": "submitted",
        "incident_id": incident_id,
        "neris_incident_id": doc.neris_incident_id,
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

    # Check 24hr cooldown BEFORE loading the incident
    if user.email in _reset_cooldowns:
        return {
            "error": "You already reset an incident in the last 24 hours. "
            "Please wait before resetting another."
        }

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

        if doc is None:
            return {"error": "Incident not found"}

        if not _check_edit_access(doc, user.email, user.is_officer):
            return {"error": "You don't have permission to reset this incident"}

        if doc.status not in _RESETTABLE_STATUSES:
            return {
                "error": f"Cannot reset an incident in '{doc.status}' status. "
                f"Only draft or in_progress incidents can be reset."
            }

        # Pre-fill from dispatch (same as creation)
        prefill = await _prefill_from_dispatch(doc.incident_number)

        # Clear content fields
        doc.incident_type = None
        doc.crew = []
        doc.unit_responses = []
        doc.narratives = Narratives()
        doc.internal_notes = ""

        # Apply dispatch pre-fill
        doc.address = prefill.get("address")
        doc.city = prefill.get("city", "")
        doc.state = prefill.get("state", "")
        doc.latitude = prefill.get("latitude")
        doc.longitude = prefill.get("longitude")
        doc.timestamps = prefill.get("timestamps", {})

        # Reset status to draft
        doc.status = "draft"
        doc.updated_at = datetime.now(UTC)

        updated = await store.update(doc)

    # Record cooldown AFTER successful update
    _reset_cooldowns[user.email] = incident_id
    logger.info("User %s reset incident %s", user.email, incident_id)
    return updated.model_dump(mode="json")


async def list_neris_incidents() -> dict:
    """List incidents from the NERIS federal reporting system.

    Returns incidents submitted to NERIS for this fire department.
    Officers only.

    Returns:
        List of NERIS incident summaries with incident number, date,
        status, and type information
    """
    user = get_current_user()

    if not user.is_officer:
        group = get_org_config().officer_group_name
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
    from sjifire.neris.client import NerisClient

    with NerisClient() as client:
        incidents = client.get_all_incidents()

    summaries = []
    for inc in incidents:
        dispatch = inc.get("dispatch", {})
        types = inc.get("incident_types", [])
        status_info = inc.get("incident_status", {})
        summaries.append(
            {
                "neris_id": inc.get("neris_id", ""),
                "incident_number": dispatch.get("incident_number", ""),
                "call_create": dispatch.get("call_create", ""),
                "status": status_info.get("status", ""),
                "incident_type": types[0].get("type", "") if types else "",
            }
        )

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

    if not user.is_officer:
        group = get_org_config().officer_group_name
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
