"""Pydantic models for incident documents stored in Cosmos DB."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from sjifire.core.config import get_org_config
from sjifire.ops.attachments.models import AttachmentMeta

MAX_NARRATIVE_LENGTH = 100_000
MAX_PERSONNEL = 50
MAX_UNITS = 50
MAX_TIMESTAMPS = 30
MAX_EDIT_HISTORY = 200

# Keys belonging to each sub-model — used for lazy migration and routing
FIRE_DETAIL_KEYS = frozenset(
    {
        "fire_cause_in",
        "fire_bldg_damage",
        "room_of_origin",
        "floor_of_origin",
        "fire_progression_evident",
        "water_supply",
        "fire_investigation",
        "fire_investigation_types",
        "suppression_appliances",
    }
)

ALARM_INFO_KEYS = frozenset(
    {
        "smoke_alarm_presence",
        "smoke_alarm_types",
        "smoke_alarm_operation",
        "smoke_alarm_occupant_action",
        "fire_alarm_presence",
        "sprinkler_presence",
    }
)

HAZARD_INFO_KEYS = frozenset(
    {
        "electric_hazards",
        "csst_present",
        "csst_lightning_suspected",
        "csst_grounded",
        "solar_present",
        "battery_ess_present",
        "generator_present",
        "powergen_type",
    }
)


class FireDetail(BaseModel):
    """Fire detail sub-model — replaces flat extras keys for fire data."""

    fire_cause_in: str | None = None
    fire_bldg_damage: str | None = None
    room_of_origin: str | None = None
    floor_of_origin: int | None = None
    fire_progression_evident: bool | None = None
    water_supply: str | None = None
    fire_investigation: str | None = None
    fire_investigation_types: list[str] = Field(default_factory=list)
    suppression_appliances: list[str] = Field(default_factory=list)


class AlarmInfo(BaseModel):
    """Alarm info sub-model — replaces flat extras keys for alarm data."""

    smoke_alarm_presence: str | None = None
    smoke_alarm_types: list[str] = Field(default_factory=list)
    smoke_alarm_operation: str | None = None
    smoke_alarm_occupant_action: str | None = None
    fire_alarm_presence: str | None = None
    sprinkler_presence: str | None = None


class HazardInfo(BaseModel):
    """Hazard info sub-model — replaces flat extras keys for hazard data."""

    electric_hazards: list[str] = Field(default_factory=list)
    csst_present: str | None = None
    csst_lightning_suspected: str | None = None
    csst_grounded: bool | None = None
    solar_present: str | None = None
    battery_ess_present: str | None = None
    generator_present: str | None = None
    powergen_type: str | None = None


class PersonnelAssignment(BaseModel):
    """A person assigned to a unit on an incident.

    Rank is snapshotted at incident time -- a Lt today may be a Captain
    next year, so we capture what they were when the call happened.
    Email is the stable UID for the person across systems.
    """

    name: str = Field(max_length=200)
    email: str | None = Field(default=None, max_length=254)
    rank: str = Field(default="", max_length=100)
    position: str = Field(default="", max_length=100)
    role: str = Field(default="", max_length=40)  # officer, driver, or both

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str | None) -> str | None:
        return v.lower() if v else v


class UnitAssignment(BaseModel):
    """A responding unit with its times and personnel.

    Combines what was previously separate ``unit_responses`` and ``crew``
    lists into a single structure. Each unit has its own timestamps and
    a nested personnel list.
    """

    unit_id: str = Field(max_length=40)  # E31, BN31, M31, POV, etc.
    response_mode: str = Field(default="", max_length=20)  # EMERGENT or NON_EMERGENT
    personnel: list[PersonnelAssignment] = Field(default_factory=list, max_length=MAX_PERSONNEL)

    # Per-unit timestamps (ISO 8601 strings)
    dispatch: str = ""
    enroute: str = ""
    staged: str = ""  # Approximate time unit staged (from CAD comments)
    on_scene: str = ""
    cleared: str = ""
    canceled: str = ""
    in_quarters: str = ""

    # Free-text note for this unit (staging location, IC role, etc.)
    comment: str = ""


class DispatchNote(BaseModel):
    """Individual dispatch radio log note (NOTE status from CAD).

    Each note corresponds to a single line from the dispatch radio log,
    with a timestamp and the unit that entered it.  Used to populate
    NERIS ``dispatch.comments`` line-by-line.
    """

    timestamp: str = ""  # ISO 8601, local timezone (from iSpyFire)
    unit: str = ""
    text: str = ""


class EditEntry(BaseModel):
    """A single edit to the incident report for audit tracking."""

    editor_email: str
    editor_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fields_changed: list[str] = Field(default_factory=list)


class IncidentDocument(BaseModel):
    """Full incident document stored in Cosmos DB.

    Superset of NERIS fields -- includes personnel, internal notes, extras,
    and status tracking. The partition key is ``year`` (derived from incident_datetime).

    Architecture: strict core fields for every-call data, plus a flexible
    ``extras`` dict for conditional NERIS sections and edge cases.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    year: str = ""  # Partition key — set by validator from incident_datetime
    status: Literal["draft", "in_progress", "ready_review", "submitted", "approved"] = "draft"

    # Core incident info
    incident_number: str = Field(max_length=40)  # e.g., "26-000944"
    incident_datetime: datetime
    incident_type: str | None = Field(default=None, max_length=200)
    additional_incident_types: list[str] = Field(default_factory=list)
    automatic_alarm: bool | None = None

    # Location
    address: str | None = Field(default=None, max_length=500)
    apt_suite: str | None = Field(default=None, max_length=100)
    city: str = Field(default="", max_length=100)
    state: str = Field(default="", max_length=2)
    zip_code: str = Field(default="", max_length=20)
    county: str = Field(default="", max_length=100)
    latitude: float | None = None
    longitude: float | None = None
    location_use: str | None = Field(default=None, max_length=200)

    # Fire-specific first-class fields
    arrival_conditions: str | None = Field(default=None, max_length=100)
    outside_fire_cause: str | None = Field(default=None, max_length=200)
    outside_fire_acres: float | None = None

    # Response — merged units with nested personnel
    units: list[UnitAssignment] = Field(default_factory=list, max_length=MAX_UNITS)
    timestamps: dict[str, str] = Field(default_factory=dict)

    # Actions & Tactics (NERIS discriminated union: ACTION or NOACTION)
    action_taken: Literal["ACTION", "NOACTION"] | None = None
    noaction_reason: str | None = Field(default=None, max_length=100)
    action_codes: list[str] = Field(default_factory=list)

    # Single combined narrative
    narrative: str = Field(default="", max_length=MAX_NARRATIVE_LENGTH)

    # People
    people_present: bool | None = None
    displaced_count: int | None = None

    # Dispatch
    dispatch_comments: str = Field(default="", max_length=MAX_NARRATIVE_LENGTH)
    dispatch_notes: list[DispatchNote] = Field(default_factory=list)

    # Tracking
    contributed_by: list[str] = Field(default_factory=list)
    created_by: str  # Entra ID user email
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    neris_incident_id: str | None = None  # Set after NERIS submission

    # Internal only — never sent to NERIS
    internal_notes: str | None = Field(default="", max_length=MAX_NARRATIVE_LENGTH)
    edit_history: list[EditEntry] = Field(default_factory=list, max_length=MAX_EDIT_HISTORY)

    # Attachments — metadata only; blobs live in Azure Blob Storage
    attachments: list[AttachmentMeta] = Field(default_factory=list)

    # Station code (e.g., "S31"). Core field for filtering and display.
    station: str = Field(default="", max_length=10)

    # Typed sub-models for NERIS fire/alarm/hazard sections
    fire_detail: FireDetail | None = None
    alarm_info: AlarmInfo | None = None
    hazard_info: HazardInfo | None = None

    # Flexible extras for remaining conditional NERIS sections (medical,
    # casualties, etc.). Claude saves edge-case data with descriptive
    # snake_case keys.
    extras: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _set_defaults(self) -> IncidentDocument:
        """Derive year partition key and apply org defaults."""
        self.year = str(self.incident_datetime.year)
        cfg = get_org_config()
        if not self.city:
            self.city = cfg.default_city
        if not self.state:
            self.state = cfg.default_state
        return self

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB storage."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> IncidentDocument:
        """Deserialize from Cosmos DB document."""
        # Strip None values from timestamps — the LLM may have stored nulls
        if "timestamps" in data and isinstance(data["timestamps"], dict):
            data["timestamps"] = {k: v for k, v in data["timestamps"].items() if v is not None}
        # Coerce null strings to empty — Cosmos may store None for str fields
        for key in ("city", "state", "zip_code", "county"):
            if key in data and data[key] is None:
                data[key] = ""
            elif key in data and not isinstance(data[key], str):
                data[key] = str(data[key])
        # Migrate station from extras to top-level field (Phase 1)
        if "station" not in data or not data.get("station"):
            extras = data.get("extras") or {}
            if "station" in extras:
                data["station"] = extras.pop("station")

        # Migrate fire/alarm/hazard keys from extras to typed sub-models (Phase 2)
        extras = data.get("extras") or {}
        for field_name, key_set in (
            ("fire_detail", FIRE_DETAIL_KEYS),
            ("alarm_info", ALARM_INFO_KEYS),
            ("hazard_info", HAZARD_INFO_KEYS),
        ):
            if not data.get(field_name):
                migrated = {k: extras.pop(k) for k in list(extras) if k in key_set}
                if migrated:
                    data[field_name] = migrated

        return cls.model_validate(data)

    def all_personnel(self) -> list[PersonnelAssignment]:
        """Flatten personnel from all units."""
        return [p for u in self.units for p in u.personnel]

    def personnel_emails(self) -> set[str]:
        """Get set of personnel emails (lowered) for access checks."""
        return {p.email.lower() for p in self.all_personnel() if p.email}

    def personnel_count(self) -> int:
        """Total personnel across all units."""
        return sum(len(u.personnel) for u in self.units)

    def completeness(self) -> dict:
        """Report completeness across key sections.

        Returns a dict with ``filled`` / ``total`` counts and a per-section
        breakdown so callers can show "3/5 complete" style progress.
        """
        sections = {
            "incident_type": bool(self.incident_type),
            "station": bool(self.station),
            "units": len(self.units) > 0,
            "personnel": self.personnel_count() > 0,
            "timestamps": len(self.timestamps) > 0,
            "narrative": bool(self.narrative),
            "actions_taken": (
                self.action_taken == "NOACTION"
                or (self.action_taken == "ACTION" and len(self.action_codes) > 0)
            ),
            "address": bool(self.address),
        }
        filled = sum(sections.values())
        return {
            "filled": filled,
            "total": len(sections),
            "sections": sections,
        }
