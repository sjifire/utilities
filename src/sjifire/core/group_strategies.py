"""Backend-agnostic group membership strategies.

Defines strategies for determining group membership based on Aladtec member data.
These strategies are used by both M365 (Graph API) and Exchange (PowerShell) backends.
The strategy has no knowledge of which backend will be used - it only defines
WHO should be in each group.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from sjifire.aladtec.models import Member
from sjifire.core.constants import MARINE_POSITIONS, OPERATIONAL_POSITIONS


@dataclass
class GroupConfig:
    """Configuration for a single group."""

    display_name: str
    mail_nickname: str  # e.g., "station31", "ff"
    description: str | None = None


class GroupStrategy(ABC):
    """Base class for group membership strategies.

    Each strategy defines:
    - How to identify which groups should exist
    - Which members belong in each group
    - Configuration for each group (name, email alias, description)

    Strategies are backend-agnostic - they know nothing about M365 vs Exchange.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy identifier used in CLI (e.g., 'stations', 'ff')."""

    @property
    @abstractmethod
    def membership_criteria(self) -> str:
        """Human-readable description of how membership is determined.

        Examples:
            - "Station Assignment field in Aladtec"
            - "Firefighter position in Aladtec"
            - "Work Group = Volunteer AND has operational position in Aladtec"
        """

    @property
    def automation_notice(self) -> str:
        """Full automation notice combining standard warning + criteria."""
        return (
            "Membership is automatically managed. "
            "Manual changes will be overwritten.\n\n"
            f"Membership criteria: {self.membership_criteria}"
        )

    @abstractmethod
    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Determine which members belong in which groups.

        Args:
            members: List of Aladtec members

        Returns:
            Dict mapping group_key to list of members for that group.
            Empty dict if no members qualify.
        """

    @abstractmethod
    def get_config(self, group_key: str) -> GroupConfig:
        """Get configuration for a specific group.

        Args:
            group_key: The key from get_members() (e.g., station number, "FF")

        Returns:
            GroupConfig with display name, mail nickname, and description
        """


class StationStrategy(GroupStrategy):
    """Members grouped by station assignment.

    Creates groups like "Station 31" based on the Station Assignment field.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "stations"

    @property
    def membership_criteria(self) -> str:
        """Return membership criteria description."""
        return "Station Assignment field in Aladtec"

    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Group members by station assignment."""
        by_station: dict[str, list[Member]] = {}

        for member in members:
            station = self._parse_station(member.station_assignment)
            if station:
                if station not in by_station:
                    by_station[station] = []
                by_station[station].append(member)

        return by_station

    def get_config(self, group_key: str) -> GroupConfig:
        """Get station group configuration."""
        return GroupConfig(
            display_name=f"Station {group_key}",
            mail_nickname=f"station{group_key}",
            description=f"Members assigned to Station {group_key}",
        )

    def _parse_station(self, station_assignment: str | None) -> str | None:
        """Extract station number from assignment field."""
        if not station_assignment:
            return None

        station = station_assignment.strip()

        # Handle plain number
        if station.isdigit():
            return station

        # Handle "Station 31" format
        if station.lower().startswith("station "):
            num = station[8:].strip()
            if num.isdigit():
                return num

        return None


class SupportStrategy(GroupStrategy):
    """Members with the Support position."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "support"

    @property
    def membership_criteria(self) -> str:
        """Return membership criteria description."""
        return "Support position in Aladtec"

    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with Support position."""
        support_members = [m for m in members if "Support" in (m.positions or [])]
        return {"Support": support_members} if support_members else {}

    def get_config(self, group_key: str) -> GroupConfig:
        """Return group configuration."""
        return GroupConfig(
            display_name="Support",
            mail_nickname="support",
            description="Members with Support position",
        )


class FirefighterStrategy(GroupStrategy):
    """Members with the Firefighter position."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "ff"

    @property
    def membership_criteria(self) -> str:
        """Return membership criteria description."""
        return "Firefighter position in Aladtec"

    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with Firefighter position."""
        ff_members = [m for m in members if "Firefighter" in (m.positions or [])]
        return {"FF": ff_members} if ff_members else {}

    def get_config(self, group_key: str) -> GroupConfig:
        """Return group configuration."""
        return GroupConfig(
            display_name="Firefighters",
            mail_nickname="firefighters",
            description="Members with Firefighter position",
        )


class WildlandFirefighterStrategy(GroupStrategy):
    """Members with the Wildland Firefighter position."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "wff"

    @property
    def membership_criteria(self) -> str:
        """Return membership criteria description."""
        return "Wildland Firefighter position in Aladtec"

    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with Wildland Firefighter position."""
        wff_members = [m for m in members if "Wildland Firefighter" in (m.positions or [])]
        return {"WFF": wff_members} if wff_members else {}

    def get_config(self, group_key: str) -> GroupConfig:
        """Return group configuration."""
        return GroupConfig(
            display_name="Wildland Firefighters",
            mail_nickname="wildlandff",
            description="Members with Wildland Firefighter position",
        )


class ApparatusOperatorStrategy(GroupStrategy):
    """Members with EVIP certification (Apparatus Operators)."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "ao"

    @property
    def membership_criteria(self) -> str:
        """Return membership criteria description."""
        return "EVIP certification in Aladtec"

    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with EVIP certification."""
        ao_members = [m for m in members if m.evip]
        return {"Apparatus Operator": ao_members} if ao_members else {}

    def get_config(self, group_key: str) -> GroupConfig:
        """Return group configuration."""
        return GroupConfig(
            display_name="Apparatus Operator",
            mail_nickname="apparatus-operator",
            description="Members with EVIP certification (Apparatus Operators)",
        )


class MarineStrategy(GroupStrategy):
    """Members with marine positions (Mate, Pilot, Deckhand)."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "marine"

    @property
    def membership_criteria(self) -> str:
        """Return membership criteria description."""
        return "Marine positions (Mate, Pilot, Deckhand) in Aladtec"

    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with marine positions."""
        marine_members = [
            m for m in members if set(m.positions or []) & MARINE_POSITIONS
        ]
        return {"Marine": marine_members} if marine_members else {}

    def get_config(self, group_key: str) -> GroupConfig:
        """Return group configuration."""
        return GroupConfig(
            display_name="Marine",
            mail_nickname="marine",
            description="Members with Marine positions",
        )


class VolunteerStrategy(GroupStrategy):
    """Volunteer members with operational positions.

    Members must have Work Group = "Volunteer" AND at least one operational
    position (Firefighter, Apparatus Operator, Support, Wildland Firefighter,
    or Marine positions).
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "volunteers"

    @property
    def membership_criteria(self) -> str:
        """Return membership criteria description."""
        return "Work Group = 'Volunteer' AND has operational position in Aladtec"

    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get volunteers with operational positions."""
        volunteers: list[Member] = []

        for member in members:
            # Must be in Volunteer work group
            if member.work_group != "Volunteer":
                continue

            # Must have at least one operational position
            member_positions = set(member.positions or [])
            if member_positions & OPERATIONAL_POSITIONS:
                volunteers.append(member)

        return {"Volunteers": volunteers} if volunteers else {}

    def get_config(self, group_key: str) -> GroupConfig:
        """Return group configuration."""
        return GroupConfig(
            display_name="Volunteers",
            mail_nickname="volunteers",
            description="Volunteer members with operational positions",
        )


class MobeScheduleStrategy(GroupStrategy):
    """Members with State Mobe schedule access.

    Members who have access to the "State Mobe" schedule in Aladtec
    are available for state-wide wildland fire mobilization deployments.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mobe"

    @property
    def membership_criteria(self) -> str:
        """Return membership criteria description."""
        return "Members with 'State Mobe' schedule access in Aladtec"

    def get_members(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with State Mobe schedule access."""
        mobe_members = [
            m for m in members if any("mobe" in s.lower() for s in (m.schedules or []))
        ]
        return {"mobe": mobe_members} if mobe_members else {}

    def get_config(self, group_key: str) -> GroupConfig:
        """Return group configuration."""
        return GroupConfig(
            display_name="State Mobilization",
            mail_nickname="statemobe",
            description="Members available for state-wide wildland fire mobilization",
        )


# Registry of all available strategies
STRATEGY_CLASSES: dict[str, type[GroupStrategy]] = {
    "stations": StationStrategy,
    "support": SupportStrategy,
    "ff": FirefighterStrategy,
    "wff": WildlandFirefighterStrategy,
    "ao": ApparatusOperatorStrategy,
    "marine": MarineStrategy,
    "volunteers": VolunteerStrategy,
    "mobe": MobeScheduleStrategy,
}

# List of strategy names for CLI
STRATEGY_NAMES: list[str] = list(STRATEGY_CLASSES.keys())


def get_strategy(name: str) -> GroupStrategy:
    """Get a strategy instance by name.

    Args:
        name: Strategy name (e.g., 'ff', 'stations')

    Returns:
        Instantiated strategy

    Raises:
        KeyError: If strategy name is not found
    """
    if name not in STRATEGY_CLASSES:
        raise KeyError(f"Unknown strategy: {name}. Available: {STRATEGY_NAMES}")
    return STRATEGY_CLASSES[name]()
