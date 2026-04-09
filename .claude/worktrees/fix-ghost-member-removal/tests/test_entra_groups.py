"""Tests for sjifire.entra.groups."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.entra.groups import EntraGroup, EntraGroupManager, GroupType


class TestEntraGroup:
    """Tests for the EntraGroup dataclass."""

    def test_microsoft_365_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Test Group",
            description=None,
            mail="test@sjifire.org",
            mail_enabled=True,
            security_enabled=False,
            group_types=["Unified"],
        )
        assert group.group_type == GroupType.MICROSOFT_365

    def test_security_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Test Security Group",
            description=None,
            mail=None,
            mail_enabled=False,
            security_enabled=True,
            group_types=[],
        )
        assert group.group_type == GroupType.SECURITY

    def test_distribution_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Test Distribution List",
            description=None,
            mail="dist@sjifire.org",
            mail_enabled=True,
            security_enabled=False,
            group_types=[],
        )
        assert group.group_type == GroupType.DISTRIBUTION

    def test_mail_enabled_security_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Mail Enabled Security",
            description=None,
            mail="security@sjifire.org",
            mail_enabled=True,
            security_enabled=True,
            group_types=[],
        )
        assert group.group_type == GroupType.MAIL_ENABLED_SECURITY

    def test_unknown_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Unknown Group",
            description=None,
            mail=None,
            mail_enabled=False,
            security_enabled=False,
            group_types=[],
        )
        assert group.group_type == GroupType.UNKNOWN

    def test_m365_takes_precedence(self):
        # Even with security_enabled=True, Unified groups are M365
        group = EntraGroup(
            id="group-1",
            display_name="M365 Group",
            description=None,
            mail="m365@sjifire.org",
            mail_enabled=True,
            security_enabled=True,
            group_types=["Unified"],
        )
        assert group.group_type == GroupType.MICROSOFT_365


class TestGroupType:
    """Tests for GroupType enum."""

    def test_enum_values(self):
        assert GroupType.SECURITY.value == "security"
        assert GroupType.MICROSOFT_365.value == "microsoft365"
        assert GroupType.DISTRIBUTION.value == "distribution"
        assert GroupType.MAIL_ENABLED_SECURITY.value == "mail_enabled_security"
        assert GroupType.UNKNOWN.value == "unknown"


class TestEntraGroupManager:
    """Tests for EntraGroupManager class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Graph client."""
        return MagicMock()

    @pytest.fixture
    def manager(self, mock_client):
        """Create an EntraGroupManager with mocked client."""
        with patch("sjifire.entra.groups.get_graph_client", return_value=mock_client):
            manager = EntraGroupManager()
        return manager

    def _make_graph_group(
        self,
        group_id="group-123",
        display_name="Test Group",
        description="Test description",
        mail="test@sjifire.org",
        mail_enabled=True,
        security_enabled=False,
        group_types=None,
    ):
        """Helper to create a mock Graph Group object."""
        group = MagicMock()
        group.id = group_id
        group.display_name = display_name
        group.description = description
        group.mail = mail
        group.mail_enabled = mail_enabled
        group.security_enabled = security_enabled
        group.group_types = group_types if group_types is not None else ["Unified"]
        return group


class TestEntraGroupManagerGetGroups(TestEntraGroupManager):
    """Tests for get_groups method."""

    async def test_get_groups_returns_all_groups(self, manager, mock_client):
        """Test fetching all groups."""
        mock_result = MagicMock()
        mock_result.value = [
            self._make_graph_group(group_id="g1", display_name="Group 1"),
            self._make_graph_group(group_id="g2", display_name="Group 2"),
        ]
        mock_result.odata_next_link = None
        mock_client.groups.get = AsyncMock(return_value=mock_result)

        groups = await manager.get_groups()

        assert len(groups) == 2
        assert groups[0].id == "g1"
        assert groups[1].id == "g2"

    async def test_get_groups_filters_by_type(self, manager, mock_client):
        """Test filtering groups by type."""
        mock_result = MagicMock()
        mock_result.value = [
            self._make_graph_group(
                group_id="m365",
                display_name="M365 Group",
                group_types=["Unified"],
                mail_enabled=True,
                security_enabled=False,
            ),
            self._make_graph_group(
                group_id="sec",
                display_name="Security Group",
                group_types=[],
                mail_enabled=False,
                security_enabled=True,
            ),
        ]
        mock_result.odata_next_link = None
        mock_client.groups.get = AsyncMock(return_value=mock_result)

        groups = await manager.get_groups(include_types=[GroupType.MICROSOFT_365])

        assert len(groups) == 1
        assert groups[0].id == "m365"

    async def test_get_groups_handles_pagination(self, manager, mock_client):
        """Test handling paginated results."""
        # First page
        first_result = MagicMock()
        first_result.value = [self._make_graph_group(group_id="g1")]
        first_result.odata_next_link = "https://graph.microsoft.com/next"

        # Second page
        second_result = MagicMock()
        second_result.value = [self._make_graph_group(group_id="g2")]
        second_result.odata_next_link = None

        mock_client.groups.get = AsyncMock(return_value=first_result)
        mock_client.groups.with_url = MagicMock(
            return_value=MagicMock(get=AsyncMock(return_value=second_result))
        )

        groups = await manager.get_groups()

        assert len(groups) == 2

    async def test_get_groups_empty_result(self, manager, mock_client):
        """Test handling empty result."""
        mock_result = MagicMock()
        mock_result.value = []
        mock_result.odata_next_link = None
        mock_client.groups.get = AsyncMock(return_value=mock_result)

        groups = await manager.get_groups()

        assert len(groups) == 0


class TestEntraGroupManagerGetGroupMembers(TestEntraGroupManager):
    """Tests for get_group_members method."""

    async def test_get_group_members_returns_ids(self, manager, mock_client):
        """Test fetching member IDs."""
        member1 = MagicMock()
        member1.id = "user-1"
        member2 = MagicMock()
        member2.id = "user-2"

        mock_result = MagicMock()
        mock_result.value = [member1, member2]

        mock_client.groups.by_group_id.return_value.members.get = AsyncMock(
            return_value=mock_result
        )

        members = await manager.get_group_members("group-123")

        assert members == ["user-1", "user-2"]

    async def test_get_group_members_empty(self, manager, mock_client):
        """Test empty member list."""
        mock_result = MagicMock()
        mock_result.value = []

        mock_client.groups.by_group_id.return_value.members.get = AsyncMock(
            return_value=mock_result
        )

        members = await manager.get_group_members("group-123")

        assert members == []

    async def test_get_group_members_filters_none_ids(self, manager, mock_client):
        """Test filtering out members with None IDs."""
        member1 = MagicMock()
        member1.id = "user-1"
        member2 = MagicMock()
        member2.id = None

        mock_result = MagicMock()
        mock_result.value = [member1, member2]

        mock_client.groups.by_group_id.return_value.members.get = AsyncMock(
            return_value=mock_result
        )

        members = await manager.get_group_members("group-123")

        assert members == ["user-1"]


class TestEntraGroupManagerAddUser(TestEntraGroupManager):
    """Tests for add_user_to_group method."""

    async def test_add_user_to_group_success(self, manager, mock_client):
        """Test successfully adding a user to a group."""
        mock_client.groups.by_group_id.return_value.members.ref.post = AsyncMock()

        result = await manager.add_user_to_group("group-123", "user-456")

        assert result is True
        mock_client.groups.by_group_id.return_value.members.ref.post.assert_called_once()

    async def test_add_user_to_group_failure(self, manager, mock_client):
        """Test handling failure when adding a user."""
        mock_client.groups.by_group_id.return_value.members.ref.post = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.add_user_to_group("group-123", "user-456")

        assert result is False


class TestEntraGroupManagerRemoveUser(TestEntraGroupManager):
    """Tests for remove_user_from_group method."""

    async def test_remove_user_from_group_success(self, manager, mock_client):
        """Test successfully removing a user from a group."""
        mock_client.groups.by_group_id.return_value.members.by_directory_object_id.return_value.ref.delete = AsyncMock()

        result = await manager.remove_user_from_group("group-123", "user-456")

        assert result is True

    async def test_remove_user_from_group_failure(self, manager, mock_client):
        """Test handling failure when removing a user."""
        mock_client.groups.by_group_id.return_value.members.by_directory_object_id.return_value.ref.delete = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.remove_user_from_group("group-123", "user-456")

        assert result is False


class TestEntraGroupManagerCreateM365Group(TestEntraGroupManager):
    """Tests for create_m365_group method."""

    async def test_create_m365_group_success(self, manager, mock_client):
        """Test successfully creating an M365 group."""
        created_group = self._make_graph_group(
            group_id="new-group-id",
            display_name="New Group",
            mail="newgroup@sjifire.org",
        )
        mock_client.groups.post = AsyncMock(return_value=created_group)

        result = await manager.create_m365_group(
            display_name="New Group",
            mail_nickname="newgroup",
            description="Test description",
        )

        assert result is not None
        assert result.id == "new-group-id"
        assert result.display_name == "New Group"

    async def test_create_m365_group_with_owners(self, manager, mock_client):
        """Test creating an M365 group with owners."""
        created_group = self._make_graph_group(group_id="new-group-id")
        mock_client.groups.post = AsyncMock(return_value=created_group)

        result = await manager.create_m365_group(
            display_name="New Group",
            mail_nickname="newgroup",
            owner_ids=["owner-1", "owner-2"],
        )

        assert result is not None
        # Verify the post was called with owner data
        call_args = mock_client.groups.post.call_args
        group_arg = call_args[0][0]
        assert "owners@odata.bind" in group_arg.additional_data

    async def test_create_m365_group_failure(self, manager, mock_client):
        """Test handling failure when creating a group."""
        mock_client.groups.post = AsyncMock(side_effect=Exception("API error"))

        result = await manager.create_m365_group(
            display_name="New Group",
            mail_nickname="newgroup",
        )

        assert result is None


class TestEntraGroupManagerUpdateDescription(TestEntraGroupManager):
    """Tests for update_group_description method."""

    async def test_update_description_success(self, manager, mock_client):
        """Test successfully updating group description."""
        mock_client.groups.by_group_id.return_value.patch = AsyncMock()

        result = await manager.update_group_description("group-123", "New description")

        assert result is True
        mock_client.groups.by_group_id.return_value.patch.assert_called_once()

    async def test_update_description_failure(self, manager, mock_client):
        """Test handling failure when updating description."""
        mock_client.groups.by_group_id.return_value.patch = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.update_group_description("group-123", "New description")

        assert result is False


class TestEntraGroupManagerUpdateVisibility(TestEntraGroupManager):
    """Tests for update_group_visibility method."""

    async def test_update_visibility_success(self, manager, mock_client):
        """Test successfully updating group visibility."""
        mock_client.groups.by_group_id.return_value.patch = AsyncMock()

        result = await manager.update_group_visibility("group-123", "Public")

        assert result is True

    async def test_update_visibility_failure(self, manager, mock_client):
        """Test handling failure when updating visibility."""
        mock_client.groups.by_group_id.return_value.patch = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.update_group_visibility("group-123", "Public")

        assert result is False


class TestEntraGroupManagerGetGroupByMailNickname(TestEntraGroupManager):
    """Tests for get_group_by_mail_nickname method."""

    async def test_get_group_by_mail_nickname_found(self, manager, mock_client):
        """Test finding a group by mail nickname."""
        mock_result = MagicMock()
        mock_result.value = [
            self._make_graph_group(group_id="found-group", display_name="Found Group")
        ]
        mock_client.groups.get = AsyncMock(return_value=mock_result)

        result = await manager.get_group_by_mail_nickname("foundgroup")

        assert result is not None
        assert result.id == "found-group"

    async def test_get_group_by_mail_nickname_not_found(self, manager, mock_client):
        """Test when group is not found by mail nickname."""
        mock_result = MagicMock()
        mock_result.value = []
        mock_client.groups.get = AsyncMock(return_value=mock_result)

        result = await manager.get_group_by_mail_nickname("nonexistent")

        assert result is None

    async def test_get_group_by_mail_nickname_error(self, manager, mock_client):
        """Test handling API error."""
        mock_client.groups.get = AsyncMock(side_effect=Exception("API error"))

        result = await manager.get_group_by_mail_nickname("test")

        assert result is None


class TestEntraGroupManagerGetGroupByName(TestEntraGroupManager):
    """Tests for get_group_by_name method."""

    async def test_get_group_by_name_found(self, manager, mock_client):
        """Test finding a group by display name."""
        mock_result = MagicMock()
        mock_result.value = [
            self._make_graph_group(group_id="found-group", display_name="Test Group")
        ]
        mock_client.groups.get = AsyncMock(return_value=mock_result)

        result = await manager.get_group_by_name("Test Group")

        assert result is not None
        assert result.id == "found-group"

    async def test_get_group_by_name_not_found(self, manager, mock_client):
        """Test when group is not found by name."""
        mock_result = MagicMock()
        mock_result.value = []
        mock_client.groups.get = AsyncMock(return_value=mock_result)

        result = await manager.get_group_by_name("Nonexistent Group")

        assert result is None

    async def test_get_group_by_name_error(self, manager, mock_client):
        """Test handling API error."""
        mock_client.groups.get = AsyncMock(side_effect=Exception("API error"))

        result = await manager.get_group_by_name("Test Group")

        assert result is None


class TestEntraGroupManagerDeleteGroup(TestEntraGroupManager):
    """Tests for delete_group method."""

    async def test_delete_group_success(self, manager, mock_client):
        """Test successfully deleting a group."""
        mock_client.groups.by_group_id.return_value.delete = AsyncMock()

        result = await manager.delete_group("group-123")

        assert result is True

    async def test_delete_group_failure(self, manager, mock_client):
        """Test handling failure when deleting a group."""
        mock_client.groups.by_group_id.return_value.delete = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.delete_group("group-123")

        assert result is False
