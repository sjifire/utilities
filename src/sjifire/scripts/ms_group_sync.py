"""Unified CLI script to sync groups from Aladtec data.

Supports both M365 groups (via Graph API) and mail-enabled security groups
(via Exchange Online PowerShell). Automatically detects existing group type
and syncs accordingly. New groups default to Exchange (no SharePoint sprawl).

Prerequisites for Exchange groups:
1. PowerShell 7+ with ExchangeOnlineManagement module
2. Azure AD App with Exchange.ManageAsApp permission
3. Certificate-based authentication configured
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from enum import Enum

from sjifire.aladtec.models import Member
from sjifire.aladtec.scraper import AladtecScraper
from sjifire.core.backup import backup_entra_groups, backup_mail_groups
from sjifire.core.group_strategies import (
    STRATEGY_NAMES,
    GroupStrategy,
    get_strategy,
)
from sjifire.entra.groups import EntraGroupManager
from sjifire.entra.users import EntraUserManager
from sjifire.exchange.client import ExchangeOnlineClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Silence verbose HTTP request logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)


class GroupType(Enum):
    """Type of group backend."""

    M365 = "m365"
    EXCHANGE = "exchange"
    BOTH = "both"  # Exists in both systems - conflict
    NONE = "none"  # Doesn't exist


@dataclass
class GroupSyncResult:
    """Result of syncing a single group."""

    group_name: str
    group_email: str
    group_type: GroupType
    created: bool = False
    members_added: list[str] = field(default_factory=list)
    members_removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None

    @property
    def has_changes(self) -> bool:
        """Check if any changes were made."""
        return self.created or bool(self.members_added) or bool(self.members_removed)


@dataclass
class FullSyncResult:
    """Result of a full sync operation."""

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

    @property
    def total_skipped(self) -> int:
        """Count of skipped groups."""
        return sum(1 for g in self.groups if g.skipped)


# All available strategy names (from core module)
STRATEGIES = STRATEGY_NAMES


class UnifiedGroupSyncManager:
    """Unified manager for syncing groups via M365 or Exchange."""

    def __init__(self, domain: str = "sjifire.org") -> None:
        """Initialize the sync manager."""
        self.domain = domain
        self._entra_groups: EntraGroupManager | None = None
        self._entra_users: EntraUserManager | None = None
        self._exchange_client: ExchangeOnlineClient | None = None
        self._entra_users_cache: list | None = None

    @property
    def entra_groups(self) -> EntraGroupManager:
        """Lazy-load Entra group manager."""
        if self._entra_groups is None:
            self._entra_groups = EntraGroupManager()
        return self._entra_groups

    @property
    def entra_users(self) -> EntraUserManager:
        """Lazy-load Entra user manager."""
        if self._entra_users is None:
            self._entra_users = EntraUserManager()
        return self._entra_users

    @property
    def exchange_client(self) -> ExchangeOnlineClient:
        """Lazy-load Exchange client."""
        if self._exchange_client is None:
            self._exchange_client = ExchangeOnlineClient()
        return self._exchange_client

    async def _load_entra_users(self) -> None:
        """Load Entra users for member matching."""
        if self._entra_users_cache is None:
            self._entra_users_cache = await self.entra_users.get_users(include_disabled=False)
            logger.info(f"Loaded {len(self._entra_users_cache)} Entra users")

    def _find_entra_user(self, member: Member) -> tuple | None:
        """Find an Entra user matching an Aladtec member.

        Returns:
            Tuple of (EntraUser, user_id) if found, None otherwise
        """
        if not self._entra_users_cache or not member.email:
            return None

        email_lower = member.email.lower()
        for user in self._entra_users_cache:
            # Match by email
            if user.email and user.email.lower() == email_lower:
                return (user, user.id)
            # Match by UPN
            if user.upn and user.upn.lower() == email_lower:
                return (user, user.id)
        return None

    async def detect_group_type(self, email: str, mail_nickname: str) -> GroupType:
        """Detect if a group exists and what type it is.

        Checks Entra ID first (faster via Graph API), then determines if it's
        an M365 group or Exchange mail-enabled security group.

        Note: Exchange mail-enabled security groups appear in both Entra ID
        (via Graph API) and Exchange. We distinguish them by checking if the
        group has 'Unified' in group_types (M365) vs mail+security enabled (Exchange).

        Args:
            email: Full email address (e.g., "station31@sjifire.org")
            mail_nickname: Mail nickname (e.g., "station31")

        Returns:
            GroupType indicating where the group exists
        """
        # Check Entra ID first (faster via Graph API)
        entra_group = None
        try:
            entra_group = await self.entra_groups.get_group_by_mail_nickname(mail_nickname)
        except Exception as e:
            logger.debug(f"Error checking Entra for {email}: {e}")

        if entra_group:
            # Check if it's an M365 (Unified) group or Exchange mail-enabled security group
            is_m365 = entra_group.group_types and "Unified" in entra_group.group_types
            is_mail_security = (
                entra_group.mail_enabled and entra_group.security_enabled and not is_m365
            )

            if is_m365:
                return GroupType.M365
            elif is_mail_security:
                # This is an Exchange mail-enabled security group
                # (it shows in Entra but is managed via Exchange)
                return GroupType.EXCHANGE

            # Fallback: if mail-enabled but not security, treat as M365
            if entra_group.mail_enabled:
                return GroupType.M365

        # If not found in Entra, check Exchange directly
        # (shouldn't normally happen, but handles edge cases)
        try:
            exchange_group = await self.exchange_client.get_distribution_group(email)
            if exchange_group:
                return GroupType.EXCHANGE
        except Exception as e:
            logger.debug(f"Error checking Exchange for {email}: {e}")

        return GroupType.NONE

    async def sync_group(
        self,
        strategy: GroupStrategy,
        group_key: str,
        group_members: list[Member],
        new_group_type: GroupType,
        dry_run: bool = False,
    ) -> GroupSyncResult:
        """Sync a single group, detecting type automatically.

        Args:
            strategy: The group strategy instance
            group_key: Key identifying the group within the strategy
            group_members: Members who should be in this group
            new_group_type: Type to create if group doesn't exist
            dry_run: If True, don't make changes

        Returns:
            GroupSyncResult with sync details
        """
        # Get group config from strategy
        config = strategy.get_config(group_key)
        display_name = config.display_name
        mail_nickname = config.mail_nickname
        email = f"{mail_nickname}@{self.domain}"

        # Detect existing group type
        detected_type = await self.detect_group_type(email, mail_nickname)

        # Handle conflict - exists in both systems
        if detected_type == GroupType.BOTH:
            logger.warning(f"⚠️  {display_name}: Exists in both M365 and Exchange - SKIPPING")
            return GroupSyncResult(
                group_name=display_name,
                group_email=email,
                group_type=detected_type,
                skipped=True,
                skip_reason="Exists in both M365 and Exchange",
            )

        # Determine which type to use
        if detected_type == GroupType.NONE:
            use_type = new_group_type
            creating = True
        else:
            use_type = detected_type
            creating = False

        # Sync using appropriate backend
        if use_type == GroupType.M365:
            return await self._sync_m365_group(
                strategy=strategy,
                group_key=group_key,
                group_members=group_members,
                dry_run=dry_run,
                creating=creating,
            )
        else:  # EXCHANGE
            return await self._sync_exchange_group(
                strategy=strategy,
                group_key=group_key,
                group_members=group_members,
                dry_run=dry_run,
                creating=creating,
            )

    async def _sync_m365_group(
        self,
        strategy: GroupStrategy,
        group_key: str,
        group_members: list[Member],
        dry_run: bool,
        creating: bool,
    ) -> GroupSyncResult:
        """Sync a group via M365 (Graph API)."""
        config = strategy.get_config(group_key)
        display_name = config.display_name
        mail_nickname = config.mail_nickname
        full_description = (
            f"{config.description}\n\n{strategy.automation_notice}"
            if config.description
            else strategy.automation_notice
        )
        email = f"{mail_nickname}@{self.domain}"

        # Ensure Entra users are loaded
        await self._load_entra_users()

        added: list[str] = []
        removed: list[str] = []
        errors: list[str] = []

        # Get or create the group
        group = None
        if creating:
            if dry_run:
                logger.info(f"Would create M365 group: {display_name}")
            else:
                group = await self.entra_groups.create_m365_group(
                    display_name=display_name,
                    mail_nickname=mail_nickname,
                    description=full_description,
                )
                if group:
                    logger.info(f"Created M365 group: {display_name}")
                else:
                    return GroupSyncResult(
                        group_name=display_name,
                        group_email=email,
                        group_type=GroupType.M365,
                        errors=[f"Failed to create group: {display_name}"],
                    )
        else:
            group = await self.entra_groups.get_group_by_mail_nickname(mail_nickname)

        # Sync membership
        if group or dry_run:
            # Get current members
            if group:
                current_member_ids = set(await self.entra_groups.get_group_members(group.id))
            else:
                current_member_ids = set()

            # Build map of who should be members (user_id -> (EntraUser, Member))
            should_be: dict[str, tuple] = {}
            for member in group_members:
                match = self._find_entra_user(member)
                if match:
                    entra_user, user_id = match
                    should_be[user_id] = (entra_user, member)
                else:
                    errors.append(
                        f"Could not find Entra user for: {member.display_name} "
                        f"(email: {member.email})"
                    )

            should_be_ids = set(should_be.keys())

            # Add missing members
            for user_id in should_be_ids - current_member_ids:
                entra_user, _ = should_be[user_id]
                name = entra_user.display_name or user_id

                if dry_run:
                    logger.info(f"Would add {name} to {display_name}")
                    added.append(name)
                else:
                    if await self.entra_groups.add_user_to_group(group.id, user_id):
                        added.append(name)
                    else:
                        errors.append(f"Failed to add {name}")

            # Remove extra members
            for user_id in current_member_ids - should_be_ids:
                # Find user name for logging
                name = user_id
                if self._entra_users_cache:
                    for user in self._entra_users_cache:
                        if user.id == user_id:
                            name = user.display_name or user_id
                            break

                if dry_run:
                    logger.info(f"Would remove {name} from {display_name}")
                    removed.append(name)
                else:
                    if await self.entra_groups.remove_user_from_group(group.id, user_id):
                        removed.append(name)
                    else:
                        errors.append(f"Failed to remove {name}")

        return GroupSyncResult(
            group_name=display_name,
            group_email=email,
            group_type=GroupType.M365,
            created=creating,  # True if group was/would be created
            members_added=added,
            members_removed=removed,
            errors=errors,
        )

    async def _sync_exchange_group(
        self,
        strategy: GroupStrategy,
        group_key: str,
        group_members: list[Member],
        dry_run: bool,
        creating: bool,
    ) -> GroupSyncResult:
        """Sync a group via Exchange (PowerShell)."""
        config = strategy.get_config(group_key)
        display_name = config.display_name
        alias = config.mail_nickname
        email = f"{alias}@{self.domain}"

        # Get or create the group
        if creating:
            if dry_run:
                logger.info(f"Would create Exchange group: {display_name} ({email})")
                group = None
            else:
                group = await self.exchange_client.create_mail_enabled_security_group(
                    name=display_name,
                    display_name=display_name,
                    alias=alias,
                    primary_smtp_address=email,
                    managed_by="svc-automations@sjifire.org",
                )
                if group:
                    logger.info(f"Created Exchange group: {display_name} ({email})")
                    # Set the group description/notes
                    await self.exchange_client.update_distribution_group_description(
                        email, strategy.automation_notice
                    )
        else:
            group = await self.exchange_client.get_distribution_group(email)
            # Update description and owner on existing groups
            if group and not dry_run:
                await self.exchange_client.update_distribution_group_description(
                    email, strategy.automation_notice
                )
                await self.exchange_client.update_distribution_group_managed_by(
                    email, "svc-automations@sjifire.org"
                )

        # Sync membership
        added: list[str] = []
        removed: list[str] = []
        errors: list[str] = []

        if group or dry_run:
            # Get current members (even in dry-run to show accurate diff)
            current_members = set(await self.exchange_client.get_distribution_group_members(email))

            # Determine who should be in the group
            should_be_emails: dict[str, Member] = {}
            for member in group_members:
                if member.email:
                    should_be_emails[member.email.lower()] = member

            should_be_set = set(should_be_emails.keys())

            # Add missing members
            for member_email in should_be_set - current_members:
                member = should_be_emails[member_email]
                if dry_run:
                    logger.info(f"Would add {member.display_name} to {email}")
                    added.append(member.display_name)
                else:
                    if await self.exchange_client.add_distribution_group_member(
                        email, member_email
                    ):
                        added.append(member.display_name)
                    else:
                        errors.append(f"Failed to add {member.display_name}")

            # Remove extra members
            for member_email in current_members - should_be_set:
                if dry_run:
                    logger.info(f"Would remove {member_email} from {email}")
                    removed.append(member_email)
                else:
                    if await self.exchange_client.remove_distribution_group_member(
                        email, member_email
                    ):
                        removed.append(member_email)
                    else:
                        errors.append(f"Failed to remove {member_email}")

        return GroupSyncResult(
            group_name=display_name,
            group_email=email,
            group_type=GroupType.EXCHANGE,
            created=creating,  # True if group was/would be created
            members_added=added,
            members_removed=removed,
            errors=errors,
        )

    async def sync(
        self,
        strategy_name: str,
        members: list[Member],
        new_group_type: GroupType = GroupType.EXCHANGE,
        dry_run: bool = False,
    ) -> FullSyncResult:
        """Sync all groups for a strategy.

        Args:
            strategy_name: Name of the strategy to run
            members: List of Aladtec members
            new_group_type: Type to use for new groups (default: Exchange)
            dry_run: If True, don't make changes

        Returns:
            FullSyncResult with all group results
        """
        # Get groups to sync from strategy
        strategy = get_strategy(strategy_name)
        groups_to_sync = strategy.get_members(members)

        if not groups_to_sync:
            logger.warning(f"No groups to sync for strategy: {strategy_name}")
            return FullSyncResult()

        logger.info(
            f"Syncing {len(groups_to_sync)} groups for {strategy_name}: "
            f"{', '.join(sorted(groups_to_sync.keys()))}"
        )

        results: list[GroupSyncResult] = []

        for group_key in sorted(groups_to_sync.keys()):
            group_members = groups_to_sync[group_key]
            logger.info(f"Processing {group_key} ({len(group_members)} members)")

            result = await self.sync_group(
                strategy=strategy,
                group_key=group_key,
                group_members=group_members,
                new_group_type=new_group_type,
                dry_run=dry_run,
            )
            results.append(result)

        return FullSyncResult(groups=results)

    async def close(self) -> None:
        """Close all clients."""
        if self._exchange_client:
            await self._exchange_client.close()


def print_result(result: FullSyncResult, dry_run: bool = False) -> None:
    """Print sync results in a readable format."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("Sync Results")
    logger.info("=" * 60)

    # Human-readable type names
    type_display = {
        GroupType.M365: "M365: Unified Group",
        GroupType.EXCHANGE: "Exchange: Mail-enabled Security Group",
        GroupType.BOTH: "CONFLICT: Exists in both M365 and Exchange",
        GroupType.NONE: "None",
    }

    for group_result in result.groups:
        type_str = type_display.get(group_result.group_type, group_result.group_type.value)

        if group_result.skipped:
            logger.warning(f"\n⚠️  {group_result.group_name} ({group_result.group_email}):")
            logger.warning(f"  SKIPPED: {group_result.skip_reason}")
            continue

        logger.info(f"\n{group_result.group_name} ({group_result.group_email}):")
        logger.info(f"  Type: {type_str}")

        if group_result.created:
            action = "Would create" if dry_run else "Created"
            logger.info(f"  Group: {action}")
        else:
            logger.info("  Group: Exists")

        if group_result.members_added:
            action = "Would add" if dry_run else "Added"
            logger.info(f"  {action}: {', '.join(group_result.members_added)}")

        if group_result.members_removed:
            action = "Would remove" if dry_run else "Removed"
            logger.info(f"  {action}: {', '.join(group_result.members_removed)}")

        if group_result.errors:
            for error in group_result.errors:
                logger.error(f"  Error: {error}")

        if not group_result.has_changes and not group_result.errors:
            logger.info("  No changes needed")

    logger.info("")
    logger.info("-" * 60)
    logger.info("Summary:")
    logger.info(f"  Groups processed: {len(result.groups)}")
    logger.info(f"  Groups created: {result.total_created}")
    logger.info(f"  Members added: {result.total_added}")
    logger.info(f"  Members removed: {result.total_removed}")
    if result.total_skipped:
        logger.warning(f"  Groups skipped (conflicts): {result.total_skipped}")
    if result.total_errors:
        logger.error(f"  Errors: {result.total_errors}")


async def backup_groups(
    strategies: list[str],
    entra_groups: EntraGroupManager,
    exchange_client: ExchangeOnlineClient,
    domain: str = "sjifire.org",
) -> None:
    """Backup all groups that will be synced.

    Args:
        strategies: List of strategy names being synced
        entra_groups: EntraGroupManager instance
        exchange_client: ExchangeOnlineClient instance
        domain: Domain for email addresses
    """
    logger.info("")
    logger.info("Creating backup of existing groups...")

    m365_groups = []
    m365_memberships: dict[str, list[str]] = {}
    exchange_groups_data = []

    # Get all mail nicknames we might sync
    mail_nicknames = set()
    for strategy_name in strategies:
        strategy = get_strategy(strategy_name)
        # Use empty member list to get all possible group configs
        # For single-group strategies, we just need any key
        sample_keys = {
            "stations": ["31", "32", "33", "34", "35", "36"],
            "support": ["Support"],
            "ff": ["FF"],
            "wff": ["WFF"],
            "ao": ["Apparatus Operator"],
            "marine": ["Marine"],
            "volunteers": ["Volunteers"],
            "mobe": ["mobe"],
        }
        for key in sample_keys.get(strategy_name, [strategy_name]):
            try:
                config = strategy.get_config(key)
                mail_nicknames.add(config.mail_nickname)
            except Exception:
                pass

    # Fetch existing groups
    for nickname in mail_nicknames:
        email = f"{nickname}@{domain}"

        # Check M365
        try:
            group = await entra_groups.get_group_by_mail_nickname(nickname)
            if group:
                is_m365 = group.group_types and "Unified" in group.group_types
                if is_m365:
                    m365_groups.append(group)
                    # Get members
                    try:
                        member_ids = await entra_groups.get_group_members(group.id)
                        m365_memberships[group.id] = member_ids
                    except Exception:
                        pass
        except Exception:
            pass

        # Check Exchange
        try:
            exch_group = await exchange_client.get_distribution_group(email)
            if exch_group:
                members = await exchange_client.get_distribution_group_members(email)
                exchange_groups_data.append({
                    "identity": exch_group.identity,
                    "display_name": exch_group.display_name,
                    "email": exch_group.primary_smtp_address,
                    "group_type": exch_group.group_type,
                    "members": members,
                })
        except Exception:
            pass

    # Save backups
    if m365_groups:
        try:
            backup_path = backup_entra_groups(m365_groups, m365_memberships)
            logger.info(f"M365 groups backup: {backup_path}")
        except Exception as e:
            logger.error(f"Failed to backup M365 groups: {e}")

    if exchange_groups_data:
        try:
            backup_path = backup_mail_groups(exchange_groups_data)
            logger.info(f"Exchange groups backup: {backup_path}")
        except Exception as e:
            logger.error(f"Failed to backup Exchange groups: {e}")

    if not m365_groups and not exchange_groups_data:
        logger.info("No existing groups to backup")


async def run_sync(
    strategies: list[str],
    new_group_type: GroupType = GroupType.EXCHANGE,
    dry_run: bool = False,
) -> int:
    """Run group sync for specified strategies.

    Args:
        strategies: List of strategy names to run
        new_group_type: Type to use for new groups
        dry_run: If True, don't make changes

    Returns:
        Exit code
    """
    logger.info("=" * 60)
    logger.info("Microsoft Group Sync")
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

    logger.info(f"Strategies: {', '.join(strategies)}")
    logger.info(f"New group type: {new_group_type.value}")

    # Fetch members from Aladtec
    logger.info("")
    logger.info("Fetching members from Aladtec...")

    try:
        with AladtecScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to log in to Aladtec")
                return 1

            members = scraper.get_members()

        if not members:
            logger.error("No members found in Aladtec")
            return 1

        logger.info(f"Found {len(members)} members")

    except Exception as e:
        logger.error(f"Failed to fetch Aladtec members: {e}")
        return 1

    # Run sync for each strategy
    manager = UnifiedGroupSyncManager()

    # Backup existing groups before making changes (skip for dry run)
    if not dry_run:
        try:
            await backup_groups(
                strategies=strategies,
                entra_groups=manager.entra_groups,
                exchange_client=manager.exchange_client,
            )
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            # Continue with sync even if backup fails
    total_errors = 0
    total_skipped = 0

    try:
        for strategy_name in strategies:
            logger.info("")
            logger.info(f"Running {strategy_name} sync...")

            try:
                result = await manager.sync(
                    strategy_name=strategy_name,
                    members=members,
                    new_group_type=new_group_type,
                    dry_run=dry_run,
                )
                print_result(result, dry_run=dry_run)
                total_errors += result.total_errors
                total_skipped += result.total_skipped

            except Exception as e:
                logger.error(f"Failed to sync {strategy_name}: {e}")
                total_errors += 1

    finally:
        await manager.close()

    if total_skipped:
        logger.warning(
            f"\n⚠️  {total_skipped} group(s) skipped due to conflicts. "
            "These groups exist in both M365 and Exchange and must be resolved manually."
        )

    return 0 if total_errors == 0 else 1


async def delete_group(email: str, dry_run: bool = False) -> int:
    """Delete an Exchange distribution group.

    Args:
        email: Email address of the group to delete
        dry_run: If True, just check if group exists

    Returns:
        Exit code
    """
    logger.info("=" * 60)
    logger.info("Delete Distribution Group")
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

    logger.info(f"Target: {email}")

    client = ExchangeOnlineClient()

    try:
        # Check if group exists
        group = await client.get_distribution_group(email)

        if not group:
            logger.warning(f"Group not found: {email}")
            return 1

        logger.info(f"Found group: {group.display_name} ({group.primary_smtp_address})")

        if dry_run:
            logger.info(f"Would delete: {email}")
            return 0

        # Delete the group
        if await client.delete_distribution_group(email):
            logger.info(f"Successfully deleted: {email}")
            return 0
        else:
            logger.error(f"Failed to delete: {email}")
            return 1

    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    finally:
        await client.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sync Microsoft groups (M365 or Exchange) from Aladtec data. "
        "Automatically detects existing group type and syncs accordingly. "
        "New groups are created as Exchange mail-enabled security groups by default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--strategy",
        choices=STRATEGIES,
        action="append",
        dest="strategies",
        help="Sync strategy to run (can be specified multiple times). "
        f"Available: {', '.join(STRATEGIES)}",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all available sync strategies",
    )
    parser.add_argument(
        "--new-type",
        choices=["exchange", "m365"],
        default="exchange",
        help="Type for new groups (default: exchange). "
        "Exchange groups don't create SharePoint sites.",
    )
    parser.add_argument(
        "--delete",
        metavar="EMAIL",
        help="Delete an Exchange distribution group by email address",
    )

    args = parser.parse_args()

    # Handle delete command
    if args.delete:
        exit_code = asyncio.run(delete_group(args.delete, dry_run=args.dry_run))
        sys.exit(exit_code)

    # Determine which strategies to run
    strategies: list[str] = []
    if args.all:
        strategies = STRATEGIES
    elif args.strategies:
        strategies = args.strategies

    if not strategies:
        parser.error("Specify --all or at least one --strategy")

    # Map new-type to GroupType
    new_group_type = GroupType.M365 if args.new_type == "m365" else GroupType.EXCHANGE

    exit_code = asyncio.run(
        run_sync(
            strategies=strategies,
            new_group_type=new_group_type,
            dry_run=args.dry_run,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
