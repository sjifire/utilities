"""Data models for ESO entities."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Personnel(BaseModel):
    """A person in the ESO system."""

    eso_id: str = Field(description="ESO personnel ID")
    first_name: str = Field(description="First name")
    last_name: str = Field(description="Last name")
    full_name: str = Field(description="Full display name")

    @classmethod
    def from_parsed(cls, last_name: str, first_name: str, eso_id: str) -> "Personnel":
        """Create from parsed name components."""
        # Capitalize properly
        last_name = last_name.strip().title()
        first_name = first_name.strip().title()
        return cls(
            eso_id=eso_id.strip(),
            first_name=first_name,
            last_name=last_name,
            full_name=f"{first_name} {last_name}",
        )

    def to_choice(self) -> str:
        """Format for dropdown choice."""
        return f"{self.full_name} - {self.eso_id}"


class Apparatus(BaseModel):
    """A vehicle/apparatus in the system."""

    code: str = Field(description="Apparatus code (e.g., E31)")
    name: str = Field(description="Full name (e.g., Engine 31)")
    type: str = Field(description="Type: SUPPRESSION, EMS, OTHER")
    station: Optional[str] = Field(default=None, description="Station number")
    active: bool = Field(default=True, description="Whether apparatus is active")

    def to_choice(self) -> str:
        """Format for dropdown choice."""
        return f"{self.code} - {self.name}"


class UnitReport(BaseModel):
    """A unit report from an incident."""

    unit_code: str = Field(description="Unit/apparatus code")
    unit_type: str = Field(description="SUPPRESSION, EMS, OTHER")
    response_priority: str = Field(description="EMERGENT, NON-EMERGENT")
    dispatch_time: Optional[str] = Field(default=None)
    enroute_time: Optional[str] = Field(default=None)
    arrival_time: Optional[str] = Field(default=None)
    at_patient_time: Optional[str] = Field(default=None)
    clear_time: Optional[str] = Field(default=None)
    in_district_time: Optional[str] = Field(default=None)
    personnel: list[Personnel] = Field(default_factory=list)


class IncidentBasic(BaseModel):
    """Basic incident information."""

    incident_number: str
    incident_date: Optional[str] = None
    incident_type: Optional[str] = None
    station: Optional[str] = None
    address: Optional[str] = None


class PersonnelList(BaseModel):
    """Container for personnel list with metadata."""

    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = "ESO Suite"
    count: int = 0
    personnel: list[Personnel] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        """Update count after initialization."""
        self.count = len(self.personnel)
