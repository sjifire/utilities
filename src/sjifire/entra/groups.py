"""Entra ID group management operations."""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder
from msgraph.generated.models.group import Group

from sjifire.core.msgraph_client import get_graph_client

logger = logging.getLogger(__name__)


class GroupType(Enum):
    """Types of Entra ID groups."""

    SECURITY = "security"
    MICROSOFT_365 = "microsoft365"
    DISTRIBUTION = "distribution"
    MAIL_ENABLED_SECURITY = "mail_enabled_security"
    UNKNOWN = "unknown"


@dataclass
class EntraGroup:
    """Represents an Entra ID group."""

    id: str
    display_name: str
    description: str | None
    mail: str | None
    mail_enabled: bool
    security_enabled: bool
    group_types: list[str]
    member_count: int | None = None

    @property
    def group_type(self) -> GroupType:
        """Determine the type of group."""
        # Microsoft 365 groups have "Unified" in groupTypes
        if "Unified" in self.group_types:
            return GroupType.MICROSOFT_365
        # Mail-enabled security group
        if self.mail_enabled and self.security_enabled:
            return GroupType.MAIL_ENABLED_SECURITY
        # Distribution list (mail-enabled, not security)
        if self.mail_enabled and not self.security_enabled:
            return GroupType.DISTRIBUTION
        # Pure security group
        if self.security_enabled and not self.mail_enabled:
            return GroupType.SECURITY
        return GroupType.UNKNOWN


class EntraGroupManager:
    """Manage groups in Entra ID."""

    def __init__(self) -> None:
        """Initialize the group manager."""
        self.client: GraphServiceClient = get_graph_client()

    async def get_groups(
        self,
        include_types: list[GroupType] | None = None,
    ) -> list[EntraGroup]:
        """Fetch groups from Entra ID.

        Args:
            include_types: Filter to specific group types. If None, returns all.

        Returns:
            List of EntraGroup objects
        """
        logger.info("Fetching Entra ID groups")

        query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
            select=[
                "id",
                "displayName",
                "description",
                "mail",
                "mailEnabled",
                "securityEnabled",
                "groupTypes",
            ],
            top=999,
        )
        config = RequestConfiguration(query_parameters=query_params)
        result = await self.client.groups.get(request_configuration=config)

        groups = []
        if result and result.value:
            for group in result.value:
                entra_group = self._to_entra_group(group)
                if include_types is None or entra_group.group_type in include_types:
                    groups.append(entra_group)

        # Handle pagination
        while result and result.odata_next_link:
            result = await self.client.groups.with_url(result.odata_next_link).get()
            if result and result.value:
                for group in result.value:
                    entra_group = self._to_entra_group(group)
                    if include_types is None or entra_group.group_type in include_types:
                        groups.append(entra_group)

        logger.info(f"Found {len(groups)} groups")
        return groups

    def _to_entra_group(self, group: Group) -> EntraGroup:
        """Convert MS Graph Group to EntraGroup.

        Args:
            group: MS Graph Group object

        Returns:
            EntraGroup object
        """
        return EntraGroup(
            id=group.id or "",
            display_name=group.display_name or "",
            description=group.description,
            mail=group.mail,
            mail_enabled=group.mail_enabled or False,
            security_enabled=group.security_enabled or False,
            group_types=group.group_types or [],
        )

    async def get_group_members(self, group_id: str) -> list[str]:
        """Get member IDs for a group.

        Args:
            group_id: The group ID

        Returns:
            List of member user IDs
        """
        result = await self.client.groups.by_group_id(group_id).members.get()
        if result and result.value:
            return [member.id for member in result.value if member.id]
        return []

    async def add_user_to_group(self, group_id: str, user_id: str) -> bool:
        """Add a user to a group.

        Args:
            group_id: The group ID
            user_id: The user ID to add

        Returns:
            True if successful
        """
        from msgraph.generated.models.reference_create import ReferenceCreate

        request_body = ReferenceCreate(
            odata_id=f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}",
        )

        try:
            await self.client.groups.by_group_id(group_id).members.ref.post(request_body)
            logger.info(f"Added user {user_id} to group {group_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to add user {user_id} to group {group_id}: {e}")
            return False

    async def remove_user_from_group(self, group_id: str, user_id: str) -> bool:
        """Remove a user from a group.

        Args:
            group_id: The group ID
            user_id: The user ID to remove

        Returns:
            True if successful
        """
        try:
            await (
                self.client.groups.by_group_id(group_id)
                .members.by_directory_object_id(user_id)
                .ref.delete()
            )
            logger.info(f"Removed user {user_id} from group {group_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove user {user_id} from group {group_id}: {e}")
            return False

    async def delete_group(self, group_id: str) -> bool:
        """Delete a group from Entra ID.

        Args:
            group_id: The group ID to delete

        Returns:
            True if successful
        """
        try:
            await self.client.groups.by_group_id(group_id).delete()
            logger.info(f"Deleted group: {group_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete group {group_id}: {e}")
            return False

    async def delete_security_groups_from_config(
        self,
        config_path: Path | str,
        dry_run: bool = False,
    ) -> dict[str, bool]:
        """Delete security groups defined in the mapping config file.

        Args:
            config_path: Path to the group_mappings.json config file
            dry_run: If True, don't delete groups, just report what would be done

        Returns:
            Dict mapping group name to success status
        """
        config_path = Path(config_path)
        with config_path.open() as f:
            config = json.load(f)

        security_groups = config.get("ms_security_group_ids", {})
        results: dict[str, bool] = {}

        for group_name in security_groups:
            # Check if group exists (by name, since ID might be "TODO")
            existing = await self.get_group_by_name(group_name)
            if not existing:
                logger.info(f"Security group does not exist: {group_name}")
                results[group_name] = True  # Nothing to delete
                continue

            if dry_run:
                logger.info(f"Would delete security group: {group_name} (ID: {existing.id})")
                results[group_name] = True
            else:
                success = await self.delete_group(existing.id)
                results[group_name] = success
                if success:
                    logger.info(f"Deleted security group: {group_name}")
                else:
                    logger.error(f"Failed to delete security group: {group_name}")

        return results

    async def create_security_group(
        self,
        display_name: str,
        description: str | None = None,
        mail_nickname: str | None = None,
    ) -> EntraGroup | None:
        """Create a new security group in Entra ID.

        Args:
            display_name: The display name for the group
            description: Optional description
            mail_nickname: Mail nickname (defaults to display_name with special chars removed)

        Returns:
            Created EntraGroup or None on failure
        """
        if not mail_nickname:
            # Generate mail nickname from display name (alphanumeric only)
            mail_nickname = "".join(c for c in display_name if c.isalnum() or c == "-")

        group = Group(
            display_name=display_name,
            description=description,
            mail_enabled=False,
            mail_nickname=mail_nickname,
            security_enabled=True,
            group_types=[],  # Empty for security groups
        )

        try:
            created = await self.client.groups.post(group)
            if created:
                logger.info(f"Created security group: {display_name} (ID: {created.id})")
                return self._to_entra_group(created)
        except Exception as e:
            logger.error(f"Failed to create security group {display_name}: {e}")

        return None

    async def get_group_by_name(self, display_name: str) -> EntraGroup | None:
        """Find a group by display name.

        Args:
            display_name: The display name to search for

        Returns:
            EntraGroup if found, None otherwise
        """
        query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
            filter=f"displayName eq '{display_name}'",
            select=[
                "id",
                "displayName",
                "description",
                "mail",
                "mailEnabled",
                "securityEnabled",
                "groupTypes",
            ],
        )
        config = RequestConfiguration(query_parameters=query_params)

        try:
            result = await self.client.groups.get(request_configuration=config)
            if result and result.value and len(result.value) > 0:
                return self._to_entra_group(result.value[0])
        except Exception as e:
            logger.error(f"Failed to find group {display_name}: {e}")

        return None

    async def create_security_groups_from_config(
        self,
        config_path: Path | str,
        dry_run: bool = False,
    ) -> dict[str, str | None]:
        """Create security groups defined in the mapping config file.

        Args:
            config_path: Path to the group_mappings.json config file
            dry_run: If True, don't create groups, just report what would be done

        Returns:
            Dict mapping group name to created group ID (or None if failed/skipped)
        """
        config_path = Path(config_path)
        with config_path.open() as f:
            config = json.load(f)

        security_groups = config.get("ms_security_group_ids", {})
        descriptions = config.get("ms_security_group_descriptions", {})
        results: dict[str, str | None] = {}

        for group_name, group_id in security_groups.items():
            # Skip if already has a valid UUID (not "TODO")
            if group_id != "TODO":
                logger.info(f"Security group already configured: {group_name} (ID: {group_id})")
                results[group_name] = group_id
                continue

            # Check if group already exists in Entra
            existing = await self.get_group_by_name(group_name)
            if existing:
                logger.info(f"Security group already exists: {group_name} (ID: {existing.id})")
                results[group_name] = existing.id
                continue

            description = descriptions.get(group_name, "")

            if dry_run:
                logger.info(f"Would create security group: {group_name}")
                logger.info(f"  Description: {description}")
                results[group_name] = None
            else:
                created = await self.create_security_group(
                    display_name=group_name,
                    description=description,
                )
                results[group_name] = created.id if created else None

        return results


def load_group_mappings(config_path: Path | str | None = None) -> dict:
    """Load group mappings from config file.

    Args:
        config_path: Path to config file. If None, uses default location.

    Returns:
        Parsed config dict
    """
    if config_path is None:
        # Default to config/group_mappings.json relative to project root
        config_path = Path(__file__).parent.parent.parent.parent / "config" / "group_mappings.json"

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open() as f:
        return json.load(f)
