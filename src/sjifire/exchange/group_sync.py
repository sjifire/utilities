"""Mail-enabled security group sync from Aladtec data.

This module provides strategies for syncing mail-enabled security groups
using the Exchange Online REST API. Unlike M365 groups, mail-enabled
security groups don't create SharePoint sites or other resources.

The strategies here mirror those in entra.group_sync but target mail-enabled
security groups instead of M365 groups.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sjifire.aladtec.models import (
    MARINE_POSITIONS,
    OPERATIONAL_POSITIONS,
    Member,
)
from sjifire.core.config import get_exchange_credentials
from sjifire.exchange.client import ExchangeGroup, ExchangeOnlineClient

logger = logging.getLogger(__name__)


class MailGroupSyncStrategy(ABC):
    """Base class for mail-enabled security group sync strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the strategy name for logging and CLI."""
        ...

    @property
    def automation_notice(self) -> str:
        """Return the automation notice to add to group description."""
        return (
            "Membership is automatically managed based on Aladtec data. "
            "Manual changes will be overwritten."
        )

    @abstractmethod
    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Determine which groups to sync and their members.

        Args:
            members: List of Aladtec members

        Returns:
            Dict mapping group_key to list of members who should be in that group
        """
        ...

    @abstractmethod
    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get configuration for a specific group.

        Args:
            group_key: The key returned by get_groups_to_sync

        Returns:
            Tuple of (display_name, email_alias, description)
        """
        ...


class MailStationGroupStrategy(MailGroupSyncStrategy):
    """Sync strategy for station-based mail-enabled security groups."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mail-stations"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for station groups."""
        return (
            "Membership is automatically managed based on Aladtec station assignments. "
            "Manual changes will be overwritten."
        )

    def _parse_station(self, station: str | None) -> str | None:
        """Parse station assignment to extract station number."""
        if not station:
            return None

        station = station.strip()
        if station.lower().startswith("station "):
            station = station[8:].strip()

        if station.isdigit():
            return station

        return None

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Group members by station assignment."""
        stations: dict[str, list[Member]] = {}

        for member in members:
            station = self._parse_station(member.station_assignment)
            if station:
                if station not in stations:
                    stations[station] = []
                stations[station].append(member)

        return stations

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get station group configuration."""
        return (
            f"Station {group_key}",
            f"station{group_key}",
            f"Members assigned to Station {group_key}",
        )


class MailSupportGroupStrategy(MailGroupSyncStrategy):
    """Sync strategy for Support mail-enabled security group."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mail-support"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for support group."""
        return (
            "Membership is automatically managed based on Support position in Aladtec. "
            "Manual changes will be overwritten."
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


class MailFirefighterGroupStrategy(MailGroupSyncStrategy):
    """Sync strategy for Firefighter mail-enabled security group."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mail-ff"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for firefighter group."""
        return (
            "Membership is automatically managed based on Firefighter position in Aladtec. "
            "Manual changes will be overwritten."
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


class MailWildlandFirefighterGroupStrategy(MailGroupSyncStrategy):
    """Sync strategy for Wildland Firefighter mail-enabled security group."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mail-wff"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for wildland firefighter group."""
        return (
            "Membership is automatically managed based on Wildland Firefighter "
            "position in Aladtec. Manual changes will be overwritten."
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


class MailApparatusOperatorGroupStrategy(MailGroupSyncStrategy):
    """Sync strategy for Apparatus Operator mail-enabled security group."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mail-ao"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for apparatus operator group."""
        return (
            "Membership is automatically managed based on EVIP certification in Aladtec. "
            "Manual changes will be overwritten."
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


class MailMarineGroupStrategy(MailGroupSyncStrategy):
    """Sync strategy for Marine mail-enabled security group."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mail-marine"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for marine group."""
        return (
            "Membership is automatically managed based on Mate/Pilot positions in Aladtec. "
            "Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get members with marine positions (Mate or Pilot)."""
        marine_members = [
            m for m in members if any(pos in MARINE_POSITIONS for pos in (m.positions or []))
        ]
        if marine_members:
            return {"Marine": marine_members}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get marine group configuration."""
        return (
            "Marine",
            "marine",
            "Members with Mate or Pilot positions",
        )


class MailVolunteerGroupStrategy(MailGroupSyncStrategy):
    """Sync strategy for Volunteer mail-enabled security group."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mail-volunteers"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for volunteer group."""
        return (
            "Membership is automatically managed based on Work Group = Volunteer "
            "and operational positions in Aladtec. Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get volunteer members with operational positions."""
        volunteer_members = [
            m
            for m in members
            if m.work_group == "Volunteer"
            and any(pos in OPERATIONAL_POSITIONS for pos in (m.positions or []))
        ]
        if volunteer_members:
            return {"Volunteers": volunteer_members}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get volunteer group configuration."""
        return (
            "Volunteers",
            "volunteers",
            "Volunteer members with operational positions",
        )


class MailAllPersonnelGroupStrategy(MailGroupSyncStrategy):
    """Sync strategy for All Personnel mail-enabled security group."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "mail-allpersonnel"

    @property
    def automation_notice(self) -> str:
        """Return automation notice for all personnel group."""
        return (
            "Membership is automatically managed based on all active Aladtec members. "
            "Manual changes will be overwritten."
        )

    def get_groups_to_sync(self, members: list[Member]) -> dict[str, list[Member]]:
        """Get all members with email addresses."""
        # Filter to only members with sjifire.org emails
        personnel = [
            m
            for m in members
            if m.email
            and "@sjifire.org" in m.email.lower()
            and "test" not in m.display_name.lower()
            and "admin" not in m.display_name.lower()
        ]
        if personnel:
            return {"All Personnel": personnel}
        return {}

    def get_group_config(self, group_key: str) -> tuple[str, str, str | None]:
        """Get all personnel group configuration."""
        return (
            "All Personnel",
            "allpersonnel",
            "All active personnel",
        )


@dataclass
class MailGroupSyncResult:
    """Result of syncing a single mail-enabled security group."""

    group_name: str
    group_email: str | None
    created: bool = False
    members_added: list[str] = field(default_factory=list)
    members_removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Check if any changes were made."""
        return self.created or bool(self.members_added) or bool(self.members_removed)


@dataclass
class MailFullSyncResult:
    """Result of a full mail group sync operation."""

    group_type: str
    groups: list[MailGroupSyncResult] = field(default_factory=list)

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


class MailGroupSyncManager:
    """Manager for syncing mail-enabled security groups via Exchange Online API."""

    def __init__(self, domain: str = "sjifire.org") -> None:
        """Initialize the sync manager.

        Args:
            domain: Email domain for the organization

        Raises:
            ValueError: If Exchange credentials are not configured
        """
        self.domain = domain
        creds = get_exchange_credentials()
        self.exchange_client = ExchangeOnlineClient(
            certificate_thumbprint=creds.certificate_thumbprint,
            certificate_path=creds.certificate_path,
            certificate_password=creds.certificate_password,
            organization=creds.organization,
        )

    async def _get_or_create_group(
        self,
        display_name: str,
        alias: str,
        description: str | None,
        dry_run: bool = False,
    ) -> tuple[ExchangeGroup | None, bool]:
        """Get existing group or create new mail-enabled security group.

        Returns:
            Tuple of (group, was_created)
        """
        email = f"{alias}@{self.domain}"

        # Check if group exists
        existing = await self.exchange_client.get_distribution_group(email)
        if existing:
            return existing, False

        if dry_run:
            logger.info(f"Would create mail-enabled security group: {display_name} ({email})")
            return None, True

        # Create the group
        created = await self.exchange_client.create_mail_enabled_security_group(
            name=display_name,
            display_name=display_name,
            alias=alias,
            primary_smtp_address=email,
        )

        if created:
            logger.info(f"Created mail-enabled security group: {display_name} ({email})")
            return created, True

        return None, False

    async def _sync_group_membership(
        self,
        group_email: str,
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
        current_members = set(
            await self.exchange_client.get_distribution_group_members(group_email)
        )

        # Determine who should be in the group (by email)
        should_be_emails: dict[str, Member] = {}
        for member in should_be_members:
            if member.email:
                should_be_emails[member.email.lower()] = member

        should_be_set = set(should_be_emails.keys())

        # Add missing members
        for email in should_be_set - current_members:
            member = should_be_emails[email]
            if dry_run:
                logger.info(f"Would add {member.display_name} to {group_email}")
                added.append(member.display_name)
            else:
                if await self.exchange_client.add_distribution_group_member(group_email, email):
                    added.append(member.display_name)
                else:
                    errors.append(f"Failed to add {member.display_name}")

        # Remove extra members
        for email in current_members - should_be_set:
            if dry_run:
                logger.info(f"Would remove {email} from {group_email}")
                removed.append(email)
            else:
                if await self.exchange_client.remove_distribution_group_member(group_email, email):
                    removed.append(email)
                else:
                    errors.append(f"Failed to remove {email}")

        return added, removed, errors

    async def sync(
        self,
        strategy: MailGroupSyncStrategy,
        members: list[Member],
        dry_run: bool = False,
    ) -> MailFullSyncResult:
        """Sync mail-enabled security groups using the given strategy.

        Args:
            strategy: The sync strategy to use
            members: List of Aladtec members
            dry_run: If True, don't make changes

        Returns:
            MailFullSyncResult with details of all changes
        """
        groups_to_sync = strategy.get_groups_to_sync(members)

        if not groups_to_sync:
            logger.warning(f"No groups to sync for strategy: {strategy.name}")
            return MailFullSyncResult(group_type=strategy.name)

        logger.info(
            f"Syncing {len(groups_to_sync)} {strategy.name} groups: "
            f"{', '.join(sorted(groups_to_sync.keys()))}"
        )

        results: list[MailGroupSyncResult] = []

        for group_key in sorted(groups_to_sync.keys()):
            group_members = groups_to_sync[group_key]
            display_name, alias, description = strategy.get_group_config(group_key)
            email = f"{alias}@{self.domain}"

            logger.info(f"Processing {display_name} ({len(group_members)} members)")

            # Get or create group
            group, created = await self._get_or_create_group(
                display_name=display_name,
                alias=alias,
                description=description,
                dry_run=dry_run,
            )

            if group is None and not dry_run:
                results.append(
                    MailGroupSyncResult(
                        group_name=display_name,
                        group_email=email,
                        created=False,
                        errors=[f"Failed to get or create group: {display_name}"],
                    )
                )
                continue

            # Sync membership
            if group or dry_run:
                added, removed, sync_errors = await self._sync_group_membership(
                    group_email=email,
                    should_be_members=group_members,
                    dry_run=dry_run,
                )
            else:
                added, removed, sync_errors = [], [], []

            results.append(
                MailGroupSyncResult(
                    group_name=display_name,
                    group_email=email,
                    created=created,
                    members_added=added,
                    members_removed=removed,
                    errors=sync_errors,
                )
            )

        return MailFullSyncResult(
            group_type=strategy.name,
            groups=results,
        )

    async def close(self) -> None:
        """Close the Exchange client."""
        await self.exchange_client.close()
