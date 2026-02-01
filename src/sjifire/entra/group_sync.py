"""Group synchronization from Aladtec data.

Manages M365 groups based on Aladtec member attributes like
station assignments, positions, work groups, etc.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sjifire.aladtec.models import Member
from sjifire.core.backup import backup_entra_groups
from sjifire.core.constants import MARINE_POSITIONS, OPERATIONAL_POSITIONS
from sjifire.entra.groups import EntraGroup, EntraGroupManager, GroupType
from sjifire.entra.users import EntraUser, EntraUserManager

logger = logging.getLogger(__name__)


@dataclass
class GroupSyncResult:
    """Result of syncing a single group."""

    group_name: str
    group_id: str | None
    created: bool
    members_added: list[str] = field(default_factory=list)
    members_removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Check if any changes were made."""
        return self.created or bool(self.members_added) or bool(self.members_removed)


@dataclass
class FullSyncResult:
    """Result of a full group sync operation."""

    group_type: str
    groups: list[GroupSyncResult] = field(default_factory=list)

    @property
    def total_created(self) -> int:
        """Count of groups created."""
        return sum(1 for g in self.groups if g.created)

    @property
    def total_added(self) -> int:
        """Count of members added across all groups."""
        return sum(len(g.members_added) for g in self.groups)

    @property
    def total_removed(self) -> int:
        """Count of members removed across all groups."""
        return sum(len(g.members_removed) for g in self.groups)

    @property
    def total_errors(self) -> int:
        """Count of errors across all groups."""
        return sum(len(g.errors) for g in self.groups)


class GroupSyncStrategy(ABC):
    """Base class for group sync strategies.

    Each strategy defines how to:
    - Identify which groups should exist
    - Determine which members belong in each group
    - Configure the group properties (name, mail, etc.)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this sync strategy."""

    @property
    @abstractmethod
    def automation_notice(self) -> str:
        """Notice to append to group descriptions indicating automated management."""

    @abstractmethod
    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Determine which groups should exist and their members.

        Args:
            members: List of Aladtec members

        Returns:
            Dict mapping group key to list of members that should be in that group
        """

    @abstractmethod
    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get configuration for a group.

        Args:
            group_key: The key identifying this group (e.g., station number)

        Returns:
            Tuple of (display_name, mail_nickname, description)
        """

    def get_full_description(self, group_key: str) -> str:
        """Get full description including automation notice.

        Args:
            group_key: The key identifying this group

        Returns:
            Full description with automation notice appended
        """
        _, _, base_description = self.get_group_config(group_key)
        parts = []
        if base_description:
            parts.append(base_description)
        parts.append(self.automation_notice)
        return "\n\n".join(parts)


class StationGroupStrategy(GroupSyncStrategy):
    """Sync strategy for station-based groups.

    Creates M365 groups like "Station 31" (station31@domain) based on
    member station assignments from Aladtec.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "station"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for station groups."""
        return (
            "⚠️ Membership is automatically managed based on Station Assignment "
            "in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Group members by station assignment."""
        by_station: dict[str, list[Member]] = {}
        for member in members:
            station = self._parse_station(member.station_assignment)
            if station:
                if station not in by_station:
                    by_station[station] = []
                by_station[station].append(member)
        return by_station

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get station group configuration."""
        return (
            f"Station {group_key}",
            f"station{group_key}",
            f"Members assigned to Station {group_key}",
        )

    def _parse_station(self, station_assignment: str | None) -> str | None:
        """Extract station number from assignment field."""
        if not station_assignment:
            return None

        station = station_assignment.strip()

        if station.isdigit():
            return station

        if station.lower().startswith("station "):
            num = station[8:].strip()
            if num.isdigit():
                return num

        return None


class SupportGroupStrategy(GroupSyncStrategy):
    """Sync strategy for Support group.

    Creates a Support M365 group containing members with the Support position.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "support"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for support group."""
        return (
            "⚠️ Membership is automatically managed based on Support position "
            "in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with Support position."""
        support_members = [m for m in members if "Support" in (m.positions or [])]
        if support_members:
            return {"Support": support_members}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get support group configuration."""
        return (
            "Support",
            "support",
            "Members with Support position",
        )


class FirefighterGroupStrategy(GroupSyncStrategy):
    """Sync strategy for Firefighter group.

    Creates an FF M365 group containing members with the Firefighter position.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "ff"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for firefighter group."""
        return (
            "⚠️ Membership is automatically managed based on Firefighter position "
            "in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with Firefighter position."""
        ff_members = [m for m in members if "Firefighter" in (m.positions or [])]
        if ff_members:
            return {"FF": ff_members}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get firefighter group configuration."""
        return (
            "FF",
            "ff",
            "Members with Firefighter position",
        )


class ApparatusOperatorGroupStrategy(GroupSyncStrategy):
    """Sync strategy for Apparatus Operator group.

    Creates an Apparatus Operator M365 group containing members with EVIP certification.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "ao"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for apparatus operator group."""
        return (
            "⚠️ Membership is automatically managed based on EVIP certification "
            "in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with EVIP certification."""
        ao_members = [m for m in members if m.evip]
        if ao_members:
            return {"Apparatus Operator": ao_members}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get apparatus operator group configuration."""
        return (
            "Apparatus Operator",
            "apparatus-operator",
            "Members with EVIP certification (Apparatus Operators)",
        )


class WildlandFirefighterGroupStrategy(GroupSyncStrategy):
    """Sync strategy for Wildland Firefighter group.

    Creates a WFF M365 group containing members with the Wildland Firefighter position.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "wff"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for wildland firefighter group."""
        return (
            "⚠️ Membership is automatically managed based on Wildland Firefighter position "
            "in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with Wildland Firefighter position."""
        wff_members = [m for m in members if "Wildland Firefighter" in (m.positions or [])]
        if wff_members:
            return {"WFF": wff_members}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get wildland firefighter group configuration."""
        return (
            "WFF",
            "wff",
            "Members with Wildland Firefighter position",
        )


class MarineGroupStrategy(GroupSyncStrategy):
    """Sync strategy for Marine group.

    Creates a Marine M365 group containing members with marine positions.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "marine"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for marine group."""
        return (
            "⚠️ Membership is automatically managed based on Marine positions "
            "in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with marine positions."""
        marine_members = [m for m in members if set(m.positions or []) & MARINE_POSITIONS]
        if marine_members:
            return {"Marine": marine_members}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get marine group configuration."""
        return (
            "Marine",
            "marine",
            "Members with Marine positions",
        )


class VolunteerGroupStrategy(GroupSyncStrategy):
    """Sync strategy for volunteer group.

    Creates a Volunteers M365 group containing members who:
    - Have Work Group = "Volunteer" in Aladtec
    - AND have at least one operational position (defined in OPERATIONAL_POSITIONS)
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "volunteers"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for volunteer group."""
        return (
            "⚠️ Membership is automatically managed based on Work Group and "
            "Positions in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
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

        if volunteers:
            return {"Volunteers": volunteers}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get volunteer group configuration."""
        return (
            "Volunteers",
            "volunteers",
            "Volunteer members with operational positions",
        )


class MobeGroupStrategy(GroupSyncStrategy):
    """Sync strategy for state mobilization group.

    Creates a State Mobilization M365 group containing members who
    have access to the "State Mobe" schedule in Aladtec.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mobe"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for mobe group."""
        return (
            "⚠️ Membership is automatically managed based on State Mobe "
            "schedule access in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with State Mobe schedule access."""
        mobe_members = [
            m for m in members if any("mobe" in s.lower() for s in (m.schedules or []))
        ]
        if mobe_members:
            return {"mobe": mobe_members}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get state mobilization group configuration."""
        return (
            "State Mobilization",
            "statemobe",
            "Members available for state-wide wildland fire mobilization",
        )


class GroupSyncManager:
    """Manages group synchronization across different strategies."""

    def __init__(self, domain: str = "sjifire.org") -> None:
        """Initialize the sync manager.

        Args:
            domain: Email domain for user lookups
        """
        self.domain = domain
        self.group_manager = EntraGroupManager()
        self.user_manager = EntraUserManager(domain=domain)
        self._entra_users: list[EntraUser] | None = None
        self._user_by_email: dict[str, EntraUser] = {}
        self._user_by_upn: dict[str, EntraUser] = {}

    async def _load_entra_users(self) -> None:
        """Load and cache Entra users."""
        if self._entra_users is None:
            self._entra_users = await self.user_manager.get_users()
            logger.info(f"Loaded {len(self._entra_users)} Entra ID users")

            for user in self._entra_users:
                if user.email:
                    self._user_by_email[user.email.lower()] = user
                if user.upn:
                    self._user_by_upn[user.upn.lower()] = user

    def _find_entra_user(self, member: Member) -> EntraUser | None:
        """Find Entra user matching an Aladtec member."""
        if member.email:
            user = self._user_by_email.get(member.email.lower())
            if user:
                return user
            user = self._user_by_upn.get(member.email.lower())
            if user:
                return user

        # Try generated UPN
        upn = self.user_manager.generate_upn(member.first_name, member.last_name)
        return self._user_by_upn.get(upn.lower())

    async def _get_or_create_group(
        self,
        display_name: str,
        mail_nickname: str,
        description: str | None,
        dry_run: bool = False,
    ) -> tuple[EntraGroup | None, bool, bool]:
        """Get existing group or create new M365 group.

        Also updates description if it differs from expected.

        Returns:
            Tuple of (group, was_created, description_updated)
        """
        # Check by mail nickname first
        existing = await self.group_manager.get_group_by_mail_nickname(mail_nickname)
        if not existing:
            # Check by display name as fallback
            existing = await self.group_manager.get_group_by_name(display_name)

        if existing:
            # Check if description needs updating
            description_updated = False
            if description and existing.description != description:
                if dry_run:
                    logger.info(f"Would update description for {display_name}")
                else:
                    if await self.group_manager.update_group_description(existing.id, description):
                        description_updated = True
            return existing, False, description_updated

        if dry_run:
            logger.info(f"Would create M365 group: {display_name}")
            return None, True, False

        created = await self.group_manager.create_m365_group(
            display_name=display_name,
            mail_nickname=mail_nickname,
            description=description,
        )
        return created, created is not None, False

    async def _apply_group_visibility(
        self,
        group: EntraGroup,
        dry_run: bool = False,
    ) -> bool:
        """Ensure group visibility is set to Public.

        Args:
            group: The group to configure
            dry_run: If True, don't make changes

        Returns:
            True if visibility was updated

        Note:
            Other settings like allowExternalSenders require Exchange Online
            PowerShell - they cannot be set via Graph API.
        """
        if dry_run:
            logger.info(f"Would set visibility to Public for {group.display_name}")
            return True

        return await self.group_manager.update_group_visibility(
            group_id=group.id,
            visibility="Public",
        )

    async def _sync_group_membership(
        self,
        group: EntraGroup,
        should_be_members: list[Member],
        dry_run: bool = False,
    ) -> tuple[list[str], list[str], list[str]]:
        """Sync group membership.

        Returns:
            Tuple of (added_names, removed_names, errors)
        """
        added: list[str] = []
        removed: list[str] = []
        errors: list[str] = []

        # Get current members
        current_member_ids = set(await self.group_manager.get_group_members(group.id))

        # Determine who should be in the group
        should_be: dict[str, tuple[EntraUser, Member]] = {}
        for member in should_be_members:
            entra_user = self._find_entra_user(member)
            if entra_user:
                should_be[entra_user.id] = (entra_user, member)
            else:
                logger.warning(
                    f"Could not find Entra user for: {member.display_name} (email: {member.email})"
                )

        should_be_ids = set(should_be.keys())

        # Add missing members
        for user_id in should_be_ids - current_member_ids:
            entra_user, _ = should_be[user_id]
            name = entra_user.display_name or user_id

            if dry_run:
                logger.info(f"Would add {name} to {group.display_name}")
                added.append(name)
            else:
                if await self.group_manager.add_user_to_group(group.id, user_id):
                    added.append(name)
                else:
                    errors.append(f"Failed to add {name}")

        # Remove extra members
        for user_id in current_member_ids - should_be_ids:
            # Find user name for logging
            name = user_id
            if self._entra_users:
                for user in self._entra_users:
                    if user.id == user_id:
                        name = user.display_name or user_id
                        break

            if dry_run:
                logger.info(f"Would remove {name} from {group.display_name}")
                removed.append(name)
            else:
                if await self.group_manager.remove_user_from_group(group.id, user_id):
                    removed.append(name)
                else:
                    errors.append(f"Failed to remove {name}")

        return added, removed, errors

    async def backup_all_groups(self) -> str | None:
        """Backup current state of all M365 groups.

        Returns:
            Path to backup file, or None if backup failed
        """
        try:
            # Get all M365 groups
            all_groups = await self.group_manager.get_groups(
                include_types=[GroupType.MICROSOFT_365]
            )

            # Get memberships for each group
            memberships: dict[str, list[str]] = {}
            for group in all_groups:
                members = await self.group_manager.get_group_members(group.id)
                memberships[group.id] = members

            # Create backup
            backup_path = backup_entra_groups(
                groups=all_groups,
                memberships=memberships,
                prefix="entra",
            )
            return str(backup_path)

        except Exception as e:
            logger.error(f"Failed to backup groups: {e}")
            return None

    async def sync(
        self,
        strategy: GroupSyncStrategy,
        members: list[Member],
        dry_run: bool = False,
    ) -> FullSyncResult:
        """Sync groups using the given strategy.

        Args:
            strategy: The sync strategy to use
            members: List of Aladtec members
            dry_run: If True, don't make changes

        Returns:
            FullSyncResult with details of all changes
        """
        # Load Entra users
        await self._load_entra_users()

        # Get groups to sync from strategy
        groups_to_sync = strategy.get_groups_to_sync(members)

        if not groups_to_sync:
            logger.warning(f"No groups to sync for strategy: {strategy.name}")
            return FullSyncResult(group_type=strategy.name)

        logger.info(
            f"Syncing {len(groups_to_sync)} {strategy.name} groups: "
            f"{', '.join(sorted(groups_to_sync.keys()))}"
        )

        results: list[GroupSyncResult] = []

        for group_key in sorted(groups_to_sync.keys()):
            group_members = groups_to_sync[group_key]
            display_name, mail_nickname, _ = strategy.get_group_config(group_key)
            full_description = strategy.get_full_description(group_key)

            logger.info(f"Processing {display_name} ({len(group_members)} members)")

            # Get or create group (also updates description if needed)
            group, created, description_updated = await self._get_or_create_group(
                display_name=display_name,
                mail_nickname=mail_nickname,
                description=full_description,
                dry_run=dry_run,
            )
            if description_updated:
                logger.info(f"Updated description for {display_name}")

            # Ensure group visibility is Public
            if group:
                await self._apply_group_visibility(group, dry_run=dry_run)

            if group is None and not dry_run:
                results.append(
                    GroupSyncResult(
                        group_name=display_name,
                        group_id=None,
                        created=False,
                        errors=[f"Failed to get or create group: {display_name}"],
                    )
                )
                continue

            # Sync membership
            if group:
                added, removed, errors = await self._sync_group_membership(
                    group=group,
                    should_be_members=group_members,
                    dry_run=dry_run,
                )
            else:
                added, removed, errors = [], [], []

            results.append(
                GroupSyncResult(
                    group_name=display_name,
                    group_id=group.id if group else None,
                    created=created,
                    members_added=added,
                    members_removed=removed,
                    errors=errors,
                )
            )

        return FullSyncResult(
            group_type=strategy.name,
            groups=results,
        )
