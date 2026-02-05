"""Unified CLI script to sync groups from Entra ID user data.

Supports both M365 groups (via Graph API) and mail-enabled security groups
(via Exchange Online PowerShell). Automatically detects existing group type
and syncs accordingly. New groups default to Exchange (no SharePoint sprawl).

Uses Entra ID as the source of truth for membership data (positions, schedules,
station assignment, etc.), which is synced from Aladtec via the entra-user-sync
process.

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

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from sjifire.core.backup import backup_entra_groups, backup_mail_groups
from sjifire.core.group_strategies import (
    STRATEGY_NAMES,
    GroupMember,
    GroupStrategy,
    get_strategy,
)
from sjifire.entra.groups import EntraGroupManager
from sjifire.entra.users import EntraUser, EntraUserManager
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
    """Unified manager for syncing groups via M365 or Exchange.

    Uses Entra ID users as the source of truth for membership data.
    """

    def __init__(self, domain: str = "sjifire.org") -> None:
        """Initialize the sync manager."""
        self.domain = domain
        self._entra_groups: EntraGroupManager | None = None
        self._entra_users: EntraUserManager | None = None
        self._exchange_client: ExchangeOnlineClient | None = None
        self._entra_users_cache: list[EntraUser] | None = None

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

    async def get_entra_users(self) -> list[EntraUser]:
        """Get Entra users (cached).

        Returns active users with @sjifire.org email addresses. This filters
        out guest accounts and non-human accounts while including members
        who may not have employee IDs set.
        """
        if self._entra_users_cache is None:
            all_users = await self.entra_users.get_users(include_disabled=False)
            # Filter to only sjifire.org domain (excludes guests, external accounts)
            self._entra_users_cache = [
                u for u in all_users if u.email and u.email.lower().endswith(f"@{self.domain}")
            ]
            logger.info(f"Loaded {len(self._entra_users_cache)} Entra users for group sync")
        return self._entra_users_cache

    async def _add_svc_automations_to_group(self, group_id: str) -> bool:
        """Add svc-automations to an M365 group for delegated calendar auth.

        The svc-automations service account needs to be a member of any M365 group
        where we want to write calendar events, because application permissions
        don't support group calendar writes.

        Uses retry logic to handle M365 group provisioning delays (group may not
        be immediately available after creation).

        Args:
            group_id: The M365 group ID

        Returns:
            True if added successfully or already a member
        """
        svc_email = "svc-automations@sjifire.org"

        # Find svc-automations user (do this once, outside retry)
        all_users = await self.entra_users.get_users(include_disabled=True)
        svc_user = next(
            (u for u in all_users if u.email and u.email.lower() == svc_email),
            None,
        )

        if not svc_user:
            logger.warning(f"Could not find {svc_email} to add to group")
            return False

        # Retry logic for adding user (handles provisioning delay)
        @retry(
            retry=retry_if_exception_type(Exception),
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            reraise=True,
        )
        async def _add_with_retry() -> bool:
            result = await self.entra_groups.add_user_to_group(group_id, svc_user.id)
            if not result:
                # add_user_to_group returns False on failure, raise to trigger retry
                raise RuntimeError(f"Failed to add {svc_email} to group {group_id}")
            return result

        try:
            await _add_with_retry()
            logger.info(f"Added {svc_email} to group for delegated calendar auth")
            return True
        except Exception as e:
            logger.warning(f"Failed to add {svc_email} to group after retries: {e}")
            return False

    async def _get_group_members_with_retry(
        self, group_id: str, newly_created: bool = False
    ) -> set[str]:
        """Get group members with retry logic for newly created groups.

        M365 groups may not be immediately queryable after creation due to
        provisioning delays. This method retries on 404 errors.

        Args:
            group_id: The M365 group ID
            newly_created: If True, use more aggressive retry for provisioning

        Returns:
            Set of member user IDs
        """
        if not newly_created:
            # For existing groups, just get members directly
            return set(await self.entra_groups.get_group_members(group_id))

        # For newly created groups, retry on failure
        @retry(
            retry=retry_if_exception_type(Exception),
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            reraise=True,
        )
        async def _get_with_retry() -> list[str]:
            return await self.entra_groups.get_group_members(group_id)

        try:
            members = await _get_with_retry()
            return set(members)
        except Exception as e:
            logger.warning(f"Failed to get group members after retries: {e}")
            return set()

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
        group_members: list[GroupMember],
        new_group_type: GroupType,
        dry_run: bool = False,
        partial_sync: bool = False,
        source_emails: set[str] | None = None,
    ) -> GroupSyncResult:
        """Sync a single group, detecting type automatically.

        Args:
            strategy: The group strategy instance
            group_key: Key identifying the group within the strategy
            group_members: Members who should be in this group (EntraUser or Member)
            new_group_type: Type to create if group doesn't exist
            dry_run: If True, don't make changes
            partial_sync: If True, preserve members not in source_emails
            source_emails: Set of all source member emails (for partial sync)

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
                partial_sync=partial_sync,
                source_emails=source_emails,
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
        group_members: list[GroupMember],
        dry_run: bool,
        creating: bool,
        partial_sync: bool = False,
        source_emails: set[str] | None = None,
    ) -> GroupSyncResult:
        """Sync a group via M365 (Graph API).

        When group_members are EntraUser objects, we already have their IDs.

        Args:
            strategy: The group strategy instance
            group_key: Key identifying the group within the strategy
            group_members: Members who should be in this group
            dry_run: If True, don't make changes
            creating: If True, the group needs to be created
            partial_sync: If True, only remove members who are in source_emails
                         (preserves manually-added members not in the source data)
            source_emails: Set of all source member emails (lowercase)
        """
        config = strategy.get_config(group_key)
        display_name = config.display_name
        mail_nickname = config.mail_nickname
        full_description = (
            f"{config.description}\n\n{strategy.automation_notice}"
            if config.description
            else strategy.automation_notice
        )
        email = f"{mail_nickname}@{self.domain}"

        added: list[str] = []
        removed: list[str] = []
        errors: list[str] = []

        # Get or create the group
        group = None
        newly_created = False
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
                    newly_created = True
                    # Add svc-automations as member (required for delegated calendar auth)
                    # Uses retry logic to handle provisioning delay
                    await self._add_svc_automations_to_group(group.id)
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
            # Get current members (with retry for newly created groups)
            if group:
                current_member_ids = await self._get_group_members_with_retry(
                    group.id, newly_created
                )
            else:
                current_member_ids = set()

            # Build map of who should be members (user_id -> EntraUser)
            # EntraUser objects already have .id, no matching needed
            should_be: dict[str, GroupMember] = {}
            for member in group_members:
                # EntraUser has .id attribute directly
                if isinstance(member, EntraUser):
                    if member.id:
                        should_be[member.id] = member
                    else:
                        errors.append(
                            f"EntraUser missing ID: {member.display_name} (email: {member.email})"
                        )
                else:
                    # Legacy support for Member objects - should not happen
                    # in new flow but keeps backward compatibility
                    errors.append(
                        f"Unexpected member type: {type(member).__name__} for {member.display_name}"
                    )

            should_be_ids = set(should_be.keys())

            # Add missing members
            for user_id in should_be_ids - current_member_ids:
                user = should_be[user_id]
                name = user.display_name or user_id

                if dry_run:
                    logger.info(f"Would add {name} to {display_name}")
                    added.append(name)
                else:
                    if await self.entra_groups.add_user_to_group(group.id, user_id):
                        added.append(name)
                    else:
                        errors.append(f"Failed to add {name}")

            # Remove extra members
            # Build a lookup of user_id -> EntraUser for the removal logic
            users = await self.get_entra_users()
            users_by_id = {u.id: u for u in users}

            for user_id in current_member_ids - should_be_ids:
                user = users_by_id.get(user_id)
                name = user.display_name if user else user_id

                # Partial sync: only remove if user is in source data
                # (preserves manually-added members who aren't in Aladtec/source)
                if partial_sync and source_emails:
                    user_email = user.email.lower() if user and user.email else None
                    if user_email and user_email not in source_emails:
                        logger.debug(f"Preserving non-source member: {name}")
                        continue  # Skip removal - not in source data

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
        group_members: list[GroupMember],
        dry_run: bool,
        creating: bool,
    ) -> GroupSyncResult:
        """Sync a group via Exchange (PowerShell).

        Uses a SINGLE PowerShell connection per group that does everything:
        - Gets group info and current members
        - Updates description and managed_by
        - Adds/removes members to match target
        """
        config = strategy.get_config(group_key)
        display_name = config.display_name
        alias = config.mail_nickname
        email = f"{alias}@{self.domain}"

        # Build target member list (works with both EntraUser and Member)
        target_emails = [m.email.lower() for m in group_members if m.email]
        email_to_member = {m.email.lower(): m for m in group_members if m.email}

        # Handle group creation separately (can't batch with sync)
        if creating:
            if dry_run:
                logger.info(f"Would create Exchange group: {display_name} ({email})")
                # For dry-run of new group, all members would be added
                added = [email_to_member[e].display_name for e in target_emails]
                for name in added:
                    logger.info(f"Would add {name} to {email}")
                return GroupSyncResult(
                    group_name=display_name,
                    group_email=email,
                    group_type=GroupType.EXCHANGE,
                    created=True,
                    members_added=added,
                    members_removed=[],
                    errors=[],
                )
            else:
                group = await self.exchange_client.create_mail_enabled_security_group(
                    name=display_name,
                    display_name=display_name,
                    alias=alias,
                    primary_smtp_address=email,
                    managed_by="svc-automations@sjifire.org",
                )
                if not group:
                    return GroupSyncResult(
                        group_name=display_name,
                        group_email=email,
                        group_type=GroupType.EXCHANGE,
                        errors=[f"Failed to create group: {display_name}"],
                    )
                logger.info(f"Created Exchange group: {display_name} ({email})")

                # Set aliases if configured
                if config.aliases:
                    await self.exchange_client.set_distribution_group_aliases(
                        identity=email,
                        aliases=config.aliases,
                        domain=self.domain,
                    )

        # For dry-run on existing groups, we need to fetch current state first
        if dry_run:
            group, current_members = await self.exchange_client.get_group_with_members(email)
            if not group:
                return GroupSyncResult(
                    group_name=display_name,
                    group_email=email,
                    group_type=GroupType.EXCHANGE,
                    errors=[f"Group not found: {email}"],
                )

            current_set = set(current_members)
            target_set = set(target_emails)
            to_add = target_set - current_set
            to_remove = current_set - target_set

            added = []
            for member_email in to_add:
                member = email_to_member.get(member_email)
                name = member.display_name if member else member_email
                logger.info(f"Would add {name} to {email}")
                added.append(name)

            removed = []
            for member_email in to_remove:
                logger.info(f"Would remove {member_email} from {email}")
                removed.append(member_email)

            return GroupSyncResult(
                group_name=display_name,
                group_email=email,
                group_type=GroupType.EXCHANGE,
                created=False,
                members_added=added,
                members_removed=removed,
                errors=[],
            )

        # SINGLE CONNECTION: Do everything in one PowerShell call
        result = await self.exchange_client.sync_group(
            identity=email,
            description=strategy.automation_notice,
            managed_by="svc-automations@sjifire.org",
            target_members=target_emails,
        )

        if not result.get("group"):
            return GroupSyncResult(
                group_name=display_name,
                group_email=email,
                group_type=GroupType.EXCHANGE,
                errors=result.get("errors", [f"Group not found: {email}"]),
            )

        # Convert added emails to display names
        added = []
        for member_email in result.get("added", []):
            member = email_to_member.get(member_email.lower())
            added.append(member.display_name if member else member_email)

        # Log changes
        if result.get("added"):
            logger.info(f"Updated description for {email}")
            logger.info(f"Updated ManagedBy for {email}")
        for name in added:
            logger.info(f"Added {name} to {email}")
        for member_email in result.get("removed", []):
            logger.info(f"Removed {member_email} from {email}")
        for error in result.get("errors", []):
            logger.error(f"Error syncing {email}: {error}")

        # Set aliases if configured (idempotent - won't duplicate existing aliases)
        if config.aliases:
            await self.exchange_client.set_distribution_group_aliases(
                identity=email,
                aliases=config.aliases,
                domain=self.domain,
            )

        return GroupSyncResult(
            group_name=display_name,
            group_email=email,
            group_type=GroupType.EXCHANGE,
            created=creating,
            members_added=added,
            members_removed=result.get("removed", []),
            errors=result.get("errors", []),
        )

    async def sync(
        self,
        strategy_name: str,
        members: list[GroupMember],
        new_group_type: GroupType = GroupType.EXCHANGE,
        dry_run: bool = False,
        partial_sync: bool = False,
    ) -> FullSyncResult:
        """Sync all groups for a strategy.

        Args:
            strategy_name: Name of the strategy to run
            members: List of members (EntraUser or Aladtec Member)
            new_group_type: Type to use for new groups (default: Exchange)
            dry_run: If True, don't make changes
            partial_sync: If True, preserve members not in source data

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

        # For partial sync, collect all source member emails
        # This is used to determine which current members to preserve
        source_emails: set[str] | None = None
        if partial_sync:
            source_emails = {m.email.lower() for m in members if m.email}

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
                partial_sync=partial_sync,
                source_emails=source_emails,
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
            "all-personnel": ["all-personnel"],
        }
        for key in sample_keys.get(strategy_name, [strategy_name]):
            try:
                config = strategy.get_config(key)
                mail_nicknames.add(config.mail_nickname)
            except Exception as e:
                logger.debug(f"Error getting config for {key}: {e}")

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
                    except Exception as e:
                        logger.debug(f"Error getting members for {group.id}: {e}")
        except Exception as e:
            logger.debug(f"Error checking M365 group {nickname}: {e}")

        # Check Exchange (batched: get group + members in one call)
        try:
            exch_group, members = await exchange_client.get_group_with_members(email)
            if exch_group:
                exchange_groups_data.append(
                    {
                        "identity": exch_group.identity,
                        "display_name": exch_group.display_name,
                        "email": exch_group.primary_smtp_address,
                        "group_type": exch_group.group_type,
                        "members": members,
                    }
                )
        except Exception as e:
            logger.debug(f"Error checking Exchange group {email}: {e}")

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
    partial_sync: bool = False,
) -> int:
    """Run group sync for specified strategies.

    Uses Entra ID as the source of truth for membership data.

    Args:
        strategies: List of strategy names to run
        new_group_type: Type to use for new groups
        dry_run: If True, don't make changes
        partial_sync: If True, preserve members not in source data (for mixed groups)

    Returns:
        Exit code
    """
    logger.info("=" * 60)
    logger.info("Microsoft Group Sync")
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

    if partial_sync:
        logger.info("PARTIAL SYNC - preserving non-Aladtec members")

    logger.info(f"Strategies: {', '.join(strategies)}")
    logger.info(f"New group type: {new_group_type.value}")

    # Initialize manager
    manager = UnifiedGroupSyncManager()

    # Fetch users from Entra ID
    logger.info("")
    logger.info("Fetching users from Entra ID...")

    try:
        members = await manager.get_entra_users()

        if not members:
            logger.error("No users found in Entra ID")
            return 1

        logger.info(f"Found {len(members)} users")

    except Exception as e:
        logger.error(f"Failed to fetch Entra ID users: {e}")
        return 1

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
                    partial_sync=partial_sync,
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
    """Delete a group (M365 or Exchange), with full backup first.

    Automatically detects whether the group is an M365 Unified Group or
    Exchange mail-enabled security group, backs it up, then deletes.

    Args:
        email: Email address of the group to delete
        dry_run: If True, just check if group exists

    Returns:
        Exit code
    """
    logger.info("=" * 60)
    logger.info("Delete Group (Unified)")
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

    logger.info(f"Target: {email}")

    # Extract mail_nickname from email
    if "@" not in email:
        logger.error(f"Invalid email format: {email}")
        return 1
    mail_nickname = email.split("@")[0]

    manager = UnifiedGroupSyncManager()

    try:
        # Detect group type
        group_type = await manager.detect_group_type(email, mail_nickname)

        if group_type == GroupType.NONE:
            logger.warning(f"Group not found: {email}")
            return 1

        if group_type == GroupType.BOTH:
            logger.error(
                f"Group exists in both M365 and Exchange: {email}. "
                "Please delete manually to resolve the conflict."
            )
            return 1

        logger.info(f"Detected group type: {group_type.value.upper()}")

        # Handle M365 group
        if group_type == GroupType.M365:
            return await _delete_m365_group(manager, email, mail_nickname, dry_run)

        # Handle Exchange group
        return await _delete_exchange_group(manager, email, dry_run)

    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    finally:
        await manager.close()


async def _delete_m365_group(
    manager: UnifiedGroupSyncManager,
    email: str,
    mail_nickname: str,
    dry_run: bool,
) -> int:
    """Delete an M365 Unified Group with backup.

    Args:
        manager: The sync manager with initialized clients
        email: Email address of the group
        mail_nickname: Mail nickname of the group
        dry_run: If True, just show what would happen

    Returns:
        Exit code
    """
    # Get group details
    group = await manager.entra_groups.get_group_by_mail_nickname(mail_nickname)
    if not group:
        logger.error(f"M365 group not found: {email}")
        return 1

    logger.info(f"Found M365 group: {group.display_name} ({group.mail})")

    # Get members for backup
    member_ids = await manager.entra_groups.get_group_members(group.id)
    logger.info(f"Group has {len(member_ids)} members")

    if dry_run:
        logger.info(f"Would delete M365 group: {email}")
        return 0

    # Create backup before deleting
    logger.info("Creating backup before delete...")
    memberships = {group.id: member_ids}
    backup_path = backup_entra_groups([group], memberships)
    logger.info(f"Backup saved: {backup_path}")

    # Delete the group
    if await manager.entra_groups.delete_group(group.id):
        logger.info(f"Successfully deleted M365 group: {email}")
        return 0
    else:
        logger.error(f"Failed to delete M365 group: {email}")
        return 1


async def _delete_exchange_group(
    manager: UnifiedGroupSyncManager,
    email: str,
    dry_run: bool,
) -> int:
    """Delete an Exchange mail-enabled security group with backup.

    Args:
        manager: The sync manager with initialized clients
        email: Email address of the group
        dry_run: If True, just show what would happen

    Returns:
        Exit code
    """
    # Get group details and members
    group, members = await manager.exchange_client.get_group_with_members(email)

    if not group:
        logger.error(f"Exchange group not found: {email}")
        return 1

    logger.info(f"Found Exchange group: {group.display_name} ({group.primary_smtp_address})")
    logger.info(f"Group has {len(members)} members")

    if dry_run:
        logger.info(f"Would delete Exchange group: {email}")
        return 0

    # Backup the group before deleting
    logger.info("Creating backup before delete...")
    group_data = {
        "identity": group.identity,
        "display_name": group.display_name,
        "email": group.primary_smtp_address,
        "group_type": group.group_type,
        "members": members,
    }
    backup_path = backup_mail_groups([group_data])
    logger.info(f"Backup saved: {backup_path}")

    # Delete the group
    if await manager.exchange_client.delete_distribution_group(email):
        logger.info(f"Successfully deleted Exchange group: {email}")
        return 0
    else:
        logger.error(f"Failed to delete Exchange group: {email}")
        return 1


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sync Microsoft groups (M365 or Exchange) from Entra ID user data. "
        "Uses Entra ID as the source of truth (synced from Aladtec via entra-user-sync). "
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
        help="Delete a group by email (auto-detects M365 vs Exchange, backs up first)",
    )
    parser.add_argument(
        "--partial-sync",
        action="store_true",
        help="Preserve members not in Aladtec (for mixed manual/auto groups like All Personnel)",
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
            partial_sync=args.partial_sync,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
