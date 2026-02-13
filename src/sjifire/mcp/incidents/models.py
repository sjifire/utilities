"""Pydantic models for incident documents stored in Cosmos DB."""

import uuid
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class CrewAssignment(BaseModel):
    """A person assigned to an incident with their role and unit.

    Rank is snapshotted at incident time -- a Lt today may be a Captain
    next year, so we capture what they were when the call happened.
    Email is the stable UID for the person across systems.
    """

    name: str
    email: str | None = None
    rank: str = ""  # Snapshotted at incident time (e.g., "Lieutenant", "Captain")
    position: str = ""  # Role on this incident (e.g., "Engine Boss", "Firefighter")
    unit: str = ""  # e.g., "E31", "M31"


class Narratives(BaseModel):
    """Incident narrative fields."""

    outcome: str = ""
    actions_taken: str = ""


class IncidentDocument(BaseModel):
    """Full incident document stored in Cosmos DB.

    Superset of NERIS fields -- includes crew, internal notes, and status tracking.
    The partition key is ``station`` (e.g., "S31").
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    station: str  # Partition key
    status: Literal["draft", "in_progress", "ready_review", "submitted"] = "draft"

    # Core incident info
    incident_number: str  # e.g., "26-000944"
    incident_date: date
    incident_type: str | None = None  # NERIS type code
    address: str | None = None
    city: str = "Friday Harbor"
    state: str = "WA"
    latitude: float | None = None
    longitude: float | None = None

    # Crew and response
    crew: list[CrewAssignment] = []
    unit_responses: list[dict] = []  # NERIS apparatus/unit format
    timestamps: dict[str, str] = {}  # e.g., {"dispatch": "...", "on_scene": "..."}

    # Narratives
    narratives: Narratives = Field(default_factory=Narratives)

    # Internal tracking
    created_by: str  # Entra ID user email
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    neris_incident_id: str | None = None  # Set after NERIS submission
    internal_notes: str = ""  # Never sent to NERIS

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB storage."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> IncidentDocument:
        """Deserialize from Cosmos DB document."""
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

    def crew_emails(self) -> set[str]:
        """Get set of crew member emails (lowered) for access checks."""
        return {c.email.lower() for c in self.crew if c.email}
