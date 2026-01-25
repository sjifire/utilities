"""Data models for Aladtec members."""

from dataclasses import dataclass, field


@dataclass
class Member:
    """Represents an Aladtec member."""

    id: str
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    home_phone: str | None = None
    position: str | None = None
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
