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

    async def create_m365_group(
        self,
        display_name: str,
        mail_nickname: str,
        description: str | None = None,
        owner_ids: list[str] | None = None,
    ) -> EntraGroup | None:
        """Create a new Microsoft 365 group in Entra ID.

        M365 groups provide shared mailbox, calendar, files, and Teams integration.

        Args:
            display_name: The display name for the group (e.g., "Station 31")
            mail_nickname: Mail prefix without domain (e.g., "station31")
            description: Optional description
            owner_ids: Optional list of user IDs to set as owners

        Returns:
            Created EntraGroup or None on failure
        """
        group = Group(
            display_name=display_name,
            description=description,
            mail_enabled=True,
            mail_nickname=mail_nickname,
            security_enabled=False,
            group_types=["Unified"],  # "Unified" = M365 group
        )

        # Add owners if provided
        if owner_ids:
            group.additional_data = {
                "owners@odata.bind": [
                    f"https://graph.microsoft.com/v1.0/users/{uid}" for uid in owner_ids
                ]
            }

        try:
            created = await self.client.groups.post(group)
            if created:
                logger.info(
                    f"Created M365 group: {display_name} ({mail_nickname}@...) (ID: {created.id})"
                )
                return self._to_entra_group(created)
        except Exception as e:
            logger.error(f"Failed to create M365 group {display_name}: {e}")

        return None

    async def update_group_description(
        self,
        group_id: str,
        description: str,
    ) -> bool:
        """Update a group's description.

        Args:
            group_id: The group ID
            description: New description

        Returns:
            True if successful
        """
        group = Group(description=description)

        try:
            await self.client.groups.by_group_id(group_id).patch(group)
            logger.info(f"Updated description for group {group_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update group {group_id} description: {e}")
            return False

    async def update_group_visibility(
        self,
        group_id: str,
        visibility: str,
    ) -> bool:
        """Update M365 group visibility.

        Args:
            group_id: The group ID
            visibility: "Public" or "Private"

        Returns:
            True if successful

        Note:
            Other group settings like allowExternalSenders, autoSubscribeNewMembers,
            hideFromAddressLists, and hideFromOutlookClients require Exchange Online
            PowerShell or the Exchange Admin Center - they cannot be set via Graph API.
        """
        group = Group(visibility=visibility)

        try:
            await self.client.groups.by_group_id(group_id).patch(group)
            logger.info(f"Updated visibility for group {group_id} to {visibility}")
            return True
        except Exception as e:
            logger.warning(f"Failed to update group {group_id} visibility: {e}")
            return False

    async def get_group_by_mail_nickname(self, mail_nickname: str) -> EntraGroup | None:
        """Find a group by mail nickname.

        Args:
            mail_nickname: The mail nickname to search for (e.g., "station31")

        Returns:
            EntraGroup if found, None otherwise
        """
        query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
            filter=f"mailNickname eq '{mail_nickname}'",
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
            logger.error(f"Failed to find group by mail nickname {mail_nickname}: {e}")

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
