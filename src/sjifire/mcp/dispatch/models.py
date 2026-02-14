"""Pydantic models for dispatch call documents stored in Cosmos DB."""

from dataclasses import asdict
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from sjifire.ispyfire.models import DispatchCall


class CrewOnDuty(BaseModel):
    """A person on duty at the time of a dispatch call."""

    name: str
    """Person's name."""

    position: str
    """Schedule position, e.g. "Captain", "Firefighter"."""

    section: str
    """Schedule section, e.g. "S31", "Chief Officer"."""


class UnitTiming(BaseModel):
    """Timing breakdown for a single responding unit (SJF only)."""

    unit: str
    """Unit code, e.g. "E31", "BN31"."""

    paged: str = ""
    """ISO timestamp when this unit was paged, or empty if not paged."""

    enroute: str = ""
    """ISO timestamp when this unit went enroute."""

    arrived: str = ""
    """ISO timestamp when this unit arrived on scene."""

    completed: str = ""
    """ISO timestamp when this unit cleared/completed the call."""


class DispatchAnalysis(BaseModel):
    """AI-extracted structured analysis of a dispatch call.

    Populated by ``analyze_dispatch()`` at ingestion time. All fields
    default to empty/zero so existing Cosmos documents deserialize
    without migration.
    """

    incident_commander: str = ""
    """Unit code with command, e.g. "BN31" or "E31 → BN31" for transfers."""

    incident_commander_name: str = ""
    """Resolved person name from on-duty schedule, e.g. "Kyle Dodd"."""

    alarm_time: str = ""
    """ISO timestamp when SJF3 was first paged. Empty if no page."""

    first_enroute: str = ""
    """ISO timestamp when first SJF3 unit went enroute."""

    unit_times: list[UnitTiming] = []
    """Per-unit timing for SJF3 units (paged/enroute/arrived)."""

    on_duty_crew: list[CrewOnDuty] = []
    """Everyone on duty at the time of the call (from schedule)."""

    summary: str = ""
    """1-2 sentence factual narrative of the incident."""

    actions_taken: list[str] = []
    """Key actions in chronological order."""

    patient_count: int = 0
    """Number of patients (0 for non-medical calls)."""

    escalated: bool = False
    """True if mutual aid, additional alarms, or significant escalation."""

    outcome: str = ""
    """Brief outcome: "transported", "fire controlled", "false alarm", etc."""


class DispatchCallDocument(BaseModel):
    """Dispatch call stored in Cosmos DB.

    Mirrors the iSpyFire ``DispatchCall`` dataclass with additional
    fields for Cosmos DB storage: ``year`` (partition key) and ``stored_at``.

    Once a call is completed (``is_completed=True``), it's immutable in
    iSpyFire and safe to cache permanently.
    """

    id: str  # iSpyFire UUID (_id)
    year: str  # Partition key, e.g. "2026"
    long_term_call_id: str  # Dispatch ID, e.g. "26-001678"
    nature: str
    address: str
    agency_code: str
    type: str = ""
    zone_code: str = ""
    time_reported: datetime | None = None
    is_completed: bool = False
    cad_comments: str = ""
    responding_units: str = ""
    responder_details: list[dict] = []
    city: str = ""
    state: str = ""
    zip_code: str = ""
    geo_location: str = ""
    analysis: DispatchAnalysis = Field(default_factory=DispatchAnalysis)
    created_timestamp: int | None = None
    stored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_dispatch_call(cls, call: DispatchCall) -> DispatchCallDocument:
        """Convert from an iSpyFire DispatchCall dataclass.

        Extracts the year from ``time_reported``. Falls back to deriving
        from the dispatch ID prefix (e.g., "26" → "2026") if time_reported
        is None.

        Args:
            call: DispatchCall dataclass from iSpyFire
        """
        d = asdict(call)
        year = _extract_year(call.time_reported, call.long_term_call_id)
        # Serialize datetime values in nested responder_details dicts
        details = []
        for rd in d["responder_details"]:
            converted = dict(rd)
            if isinstance(converted.get("time_of_status_change"), datetime):
                converted["time_of_status_change"] = converted["time_of_status_change"].isoformat()
            details.append(converted)
        return cls(
            id=call.id,
            year=year,
            long_term_call_id=call.long_term_call_id,
            nature=call.nature,
            address=call.address,
            agency_code=call.agency_code,
            type=call.type,
            zone_code=call.zone_code,
            time_reported=call.time_reported,
            is_completed=call.is_completed,
            cad_comments=call.cad_comments,
            responding_units=call.responding_units,
            responder_details=details,
            city=call.city,
            state=call.state,
            zip_code=call.zip_code,
            geo_location=call.geo_location,
            created_timestamp=call.created_timestamp,
        )

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB storage."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> DispatchCallDocument:
        """Deserialize from Cosmos DB document."""
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        """Convert to tool-output dict, stripping Cosmos-only fields.

        Strips Cosmos-only fields (``year``, ``stored_at``) so the
        output matches the shape tool consumers expect.
        """
        d = self.model_dump(mode="json")
        # Remove Cosmos storage fields not in the original DispatchCall
        for key in ("year", "stored_at"):
            d.pop(key, None)
        return d


def _extract_year(time_reported: datetime | None, dispatch_id: str) -> str:
    """Extract four-digit year from time_reported or dispatch ID prefix.

    Args:
        time_reported: Parsed datetime from iSpyFire
        dispatch_id: Dispatch ID like "26-001678"

    Returns:
        Four-digit year string, e.g. "2026"
    """
    if time_reported is not None:
        return str(time_reported.year)

    # Fall back to dispatch ID prefix: "26" → "2026"
    if dispatch_id and "-" in dispatch_id:
        prefix = dispatch_id.split("-")[0]
        if len(prefix) == 2 and prefix.isdigit():
            return f"20{prefix}"

    return str(datetime.now(UTC).year)


def year_from_dispatch_id(dispatch_id: str) -> str | None:
    """Derive the partition key year from a dispatch ID prefix.

    Args:
        dispatch_id: e.g. "26-001678"

    Returns:
        Four-digit year like "2026", or None if not derivable.
    """
    if dispatch_id and "-" in dispatch_id:
        prefix = dispatch_id.split("-")[0]
        if len(prefix) == 2 and prefix.isdigit():
            return f"20{prefix}"
    return None
