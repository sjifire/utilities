"""Data models for iSpyFire integration."""

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
