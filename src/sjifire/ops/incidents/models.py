"""Pydantic models for incident documents stored in Cosmos DB."""

import uuid
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from sjifire.core.config import get_org_config

MAX_NARRATIVE_LENGTH = 10_000
MAX_CREW_SIZE = 50
MAX_UNIT_RESPONSES = 50
MAX_TIMESTAMPS = 30
MAX_EDIT_HISTORY = 200


class CrewAssignment(BaseModel):
    """A person assigned to an incident with their role and unit.

    Rank is snapshotted at incident time -- a Lt today may be a Captain
    next year, so we capture what they were when the call happened.
    Email is the stable UID for the person across systems.
    """

    name: str = Field(max_length=200)
    email: str | None = Field(default=None, max_length=254)
    rank: str = Field(default="", max_length=100)
    position: str = Field(default="", max_length=100)
    unit: str = Field(default="", max_length=20)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str | None) -> str | None:
        return v.lower() if v else v


class EditEntry(BaseModel):
    """A single edit to the incident report for audit tracking."""

    editor_email: str
    editor_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fields_changed: list[str] = Field(default_factory=list)


class Narratives(BaseModel):
    """Incident narrative fields."""

    outcome: str = Field(default="", max_length=MAX_NARRATIVE_LENGTH)
    actions_taken: str = Field(default="", max_length=MAX_NARRATIVE_LENGTH)


class IncidentDocument(BaseModel):
    """Full incident document stored in Cosmos DB.

    Superset of NERIS fields -- includes crew, internal notes, and status tracking.
    The partition key is ``year`` (four-digit string derived from incident_date).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    year: str = ""  # Partition key — set by validator from incident_date
    station: str  # Station code (e.g., "S31")
    status: Literal["draft", "in_progress", "ready_review", "submitted"] = "draft"

    # Core incident info
    incident_number: str = Field(max_length=40)  # e.g., "26-000944"
    incident_date: date
    incident_type: str | None = Field(default=None, max_length=200)  # NERIS type code
    location_use: str | None = Field(default=None, max_length=200)  # NERIS location use code
    address: str | None = Field(default=None, max_length=500)
    city: str = Field(default="", max_length=100)
    state: str = Field(default="", max_length=2)
    latitude: float | None = None
    longitude: float | None = None

    # Crew and response
    crew: list[CrewAssignment] = Field(default_factory=list, max_length=MAX_CREW_SIZE)
    unit_responses: list[dict] = Field(default_factory=list, max_length=MAX_UNIT_RESPONSES)
    timestamps: dict[str, str] = Field(default_factory=dict, max_length=MAX_TIMESTAMPS)

    # Narratives
    narratives: Narratives = Field(default_factory=Narratives)

    # Internal tracking
    created_by: str  # Entra ID user email
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    neris_incident_id: str | None = None  # Set after NERIS submission
    # Internal only — never sent to NERIS
    internal_notes: str | None = Field(default="", max_length=MAX_NARRATIVE_LENGTH)
    edit_history: list[EditEntry] = Field(default_factory=list, max_length=MAX_EDIT_HISTORY)

    @model_validator(mode="after")
    def _set_defaults(self) -> IncidentDocument:
        """Derive year partition key and apply org defaults."""
        self.year = str(self.incident_date.year)
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
        return cls.model_validate(data)

    def to_neris_payload(self) -> dict:
        """Map to NERIS incident creation/update payload shape.

        Returns a dict matching the NERIS API incident format.
        Fields that are None or empty are omitted.
        """
        payload: dict = {
            "incident_number": self.incident_number,
            "incident_date": self.incident_date.isoformat(),
        }

        if self.incident_type:
            payload["type"] = {"code": self.incident_type}

        if self.location_use:
            payload["location_use"] = {"code": self.location_use}

        if self.address:
            payload["address"] = {
                "address_line1": self.address,
                "city": self.city,
                "state": self.state,
            }

        if self.latitude is not None and self.longitude is not None:
            payload["location"] = {
                "latitude": self.latitude,
                "longitude": self.longitude,
            }

        if self.unit_responses:
            payload["apparatus"] = self.unit_responses

        if self.narratives.outcome or self.narratives.actions_taken:
            payload["narrative"] = {}
            if self.narratives.outcome:
                payload["narrative"]["outcome"] = self.narratives.outcome
            if self.narratives.actions_taken:
                payload["narrative"]["actions_taken"] = self.narratives.actions_taken

        if self.timestamps:
            payload["timestamps"] = self.timestamps

        return payload

    def completeness(self) -> dict:
        """Report completeness across key sections.

        Returns a dict with ``filled`` / ``total`` counts and a per-section
        breakdown so callers can show "3/5 complete" style progress.
        """
        sections = {
            "incident_type": bool(self.incident_type),
            "unit_responses": len(self.unit_responses) > 0,
            "crew": len(self.crew) > 0,
            "timestamps": len(self.timestamps) > 0,
            "narrative": bool(self.narratives.outcome),
            "actions_taken": bool(self.narratives.actions_taken),
            "address": bool(self.address),
        }
        filled = sum(sections.values())
        return {
            "filled": filled,
            "total": len(sections),
            "sections": sections,
        }

    def crew_emails(self) -> set[str]:
        """Get set of crew member emails (lowered) for access checks."""
        return {c.email.lower() for c in self.crew if c.email}
