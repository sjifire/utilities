"""Data models for iSpyFire integration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ISpyFirePerson:
    """Represents a person in iSpyFire."""

    id: str  # _id from API
    first_name: str
    last_name: str
    email: str | None = None
    cell_phone: str | None = None
    title: str | None = None
    is_active: bool = True
    is_login_active: bool = True  # Always match is_active
    is_utility: bool = False
    group_set_acls: list[str] = field(default_factory=list)
    responder_types: list[str] = field(default_factory=list)
    message_email: bool = False
    message_cell: bool = False

    @property
    def display_name(self) -> str:
        """Full display name."""
        return f"{self.first_name} {self.last_name}"

    def set_active(self, active: bool) -> None:
        """Set active status for both is_active and is_login_active.

        These should always be synchronized in iSpyFire.
        """
        self.is_active = active
        self.is_login_active = active

    @classmethod
    def from_api(cls, data: dict) -> ISpyFirePerson:
        """Create from API response data."""
        return cls(
            id=data.get("_id", ""),
            first_name=data.get("firstName", ""),
            last_name=data.get("lastName", ""),
            email=data.get("email"),
            cell_phone=data.get("cellPhone"),
            title=data.get("title"),
            is_active=data.get("isActive", True),
            is_login_active=data.get("isLoginActive", False),
            is_utility=data.get("isUtility", False),
            group_set_acls=data.get("groupSetACLs", []),
            responder_types=data.get("responderTypes", []),
            message_email=data.get("messageEmail", False),
            message_cell=data.get("messageCell", False),
        )

    def to_api(self) -> dict:
        """Convert to API request format."""
        return {
            "firstName": self.first_name,
            "lastName": self.last_name,
            "email": self.email,
            "cellPhone": self.cell_phone,
            "title": self.title,
            "isActive": self.is_active,
            "isLoginActive": self.is_login_active,
            "responderTypes": self.responder_types,
            "messageEmail": self.message_email,
            "messageCell": self.message_cell,
        }


@dataclass
class UnitResponse:
    """A unit's dispatch response detail from CAD."""

    unit_number: str
    agency_code: str
    status: str
    time_of_status_change: str
    radio_log: str = ""

    @classmethod
    def from_api(cls, data: dict) -> UnitResponse:
        """Create from API response data."""
        return cls(
            unit_number=data.get("UnitNumber", ""),
            agency_code=data.get("AgencyCode", ""),
            status=data.get("StatusDisplayCode", ""),
            time_of_status_change=data.get("TimeOfStatusChange", ""),
            radio_log=data.get("RadioLog", ""),
        )


@dataclass
class CallSummary:
    """Minimal call reference from the list endpoint."""

    id: str
    ispy_timestamp: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> CallSummary:
        """Create from API response data."""
        return cls(
            id=data.get("_id", ""),
            ispy_timestamp=data.get("iSpyTimestamp"),
        )


@dataclass
class DispatchCall:
    """Full dispatch call details from the central API."""

    id: str
    long_term_call_id: str
    nature: str
    address: str
    agency_code: str
    type: str = ""
    zone_code: str = ""
    time_reported: str = ""
    is_completed: bool = False
    comments: str = ""
    joined_responders: str = ""
    responder_details: list[UnitResponse] = field(default_factory=list)
    ispy_responders: dict = field(default_factory=dict)
    city: str = ""
    state: str = ""
    zip_code: str = ""
    geo_location: str = ""
    created_timestamp: int | None = None

    @classmethod
    def from_api(cls, data: dict) -> DispatchCall:
        """Create from API response data."""
        city_info = data.get("CityInfo", {}) or {}
        details = [UnitResponse.from_api(d) for d in data.get("JoinedRespondersDetail", []) or []]
        return cls(
            id=data.get("_id", ""),
            long_term_call_id=data.get("LongTermCallID", ""),
            nature=data.get("Nature", ""),
            address=data.get("RespondToAddress", ""),
            agency_code=data.get("AgencyCode", ""),
            type=data.get("Type", ""),
            zone_code=data.get("ZoneCode", ""),
            time_reported=data.get("TimeDateReported", ""),
            is_completed=data.get("IsCompleted", False),
            comments=data.get("JoinedComments", ""),
            joined_responders=data.get("JoinedResponders", ""),
            responder_details=details,
            ispy_responders=data.get("iSpyResponders", {}),
            city=city_info.get("City", ""),
            state=city_info.get("StateAbbreviation", ""),
            zip_code=city_info.get("ZIPCode", ""),
            geo_location=data.get("iSpyGeoLocation", ""),
            created_timestamp=data.get("iSpyCreatedTimestamp"),
        )
