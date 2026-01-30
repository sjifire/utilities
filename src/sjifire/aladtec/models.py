"""Data models for Aladtec members."""

from dataclasses import dataclass, field

# Rank hierarchy for determining employee type (highest first)
RANK_HIERARCHY = ["Chief", "Division Chief", "Battalion Chief", "Captain", "Lieutenant"]


@dataclass
class Member:
    """Represents an Aladtec member."""

    id: str
    first_name: str
    last_name: str
    email: str | None = None
    personal_email: str | None = None
    phone: str | None = None
    home_phone: str | None = None
    employee_type: str | None = None  # "Employee Type" field from Aladtec CSV
    positions: list[str] = field(default_factory=list)
    title: str | None = None
    status: str | None = None
    work_group: str | None = None
    pay_profile: str | None = None
    employee_id: str | None = None
    station_assignment: str | None = None
    evip: str | None = None
    date_hired: str | None = None

    @property
    def display_name(self) -> str:
        """Full display name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_active(self) -> bool:
        """Check if member is active.

        Based on the 'Member Status' field from Aladtec.
        Note: The Aladtec CSV export currently only includes active members
        by default. Inactive members may not appear in exports.
        """
        return self.status is None or self.status.lower() == "active"

    @property
    def user_principal_name(self) -> str | None:
        """Generate UPN for Entra ID (email-based)."""
        return self.email

    @property
    def rank(self) -> str | None:
        """Extract full rank from Title or Employee Type field.

        Checks for: Chief, Division Chief, Battalion Chief, Captain, Lieutenant.
        Prioritizes Title field (more specific) over Employee Type.
        Returns the full rank (e.g., "Battalion Chief") for extensionAttribute1.
        """
        # Check Title first (more specific), then Employee Type
        for field_value in (self.title, self.employee_type):
            if not field_value:
                continue
            for rank in RANK_HIERARCHY:
                if rank.lower() == field_value.lower():
                    return rank
        return None

    @property
    def display_rank(self) -> str | None:
        """Get shortened rank for display name prefix.

        Returns a shortened version of the rank for display names:
        - "Battalion Chief" → "Chief"
        - "Division Chief" → "Chief"
        - "Chief" → "Chief"
        - "Captain" → "Captain"
        - "Lieutenant" → "Lieutenant"
        """
        full_rank = self.rank
        if not full_rank:
            return None

        # Shorten *Chief ranks to just "Chief" for display
        if full_rank.endswith("Chief"):
            return "Chief"

        return full_rank

    @property
    def job_title(self) -> str | None:
        """Get job title, excluding rank titles.

        If the title field contains a rank (Chief, Captain, etc.),
        returns None since that's used as employeeType instead.
        """
        if not self.title:
            return None

        # Check if title is a rank
        for rank in RANK_HIERARCHY:
            if rank.lower() == self.title.lower():
                return None

        return self.title

    @property
    def office_location(self) -> str | None:
        """Get office location formatted for Entra ID.

        Adds 'Station ' prefix to station assignment numbers.
        """
        if not self.station_assignment:
            return None

        station = self.station_assignment.strip()
        # If it's just a number, add "Station " prefix
        if station.isdigit():
            return f"Station {station}"
        # If it already has "Station" in it, return as-is
        if station.lower().startswith("station"):
            return station
        # Otherwise return as-is
        return station
