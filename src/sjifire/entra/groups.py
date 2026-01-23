"""Entra ID group management operations."""

import logging
from dataclasses import dataclass
from enum import Enum

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
        member_ids = []
        if result and result.value:
            for member in result.value:
                if member.id:
                    member_ids.append(member.id)
        return member_ids

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
