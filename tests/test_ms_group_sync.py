"""Tests for sjifire/scripts/ms_group_sync.py - unified group sync functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.core.group_strategies import FirefighterStrategy
from sjifire.entra.users import EntraUser
from sjifire.scripts.ms_group_sync import (
    GroupSyncResult,
    GroupType,
    UnifiedGroupSyncManager,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_entra_groups():
    """Mock EntraGroupManager."""
    with patch("sjifire.scripts.ms_group_sync.EntraGroupManager") as mock:
        instance = AsyncMock()
        mock.return_value = instance
        yield instance


@pytest.fixture
def mock_entra_users():
    """Mock EntraUserManager."""
    with patch("sjifire.scripts.ms_group_sync.EntraUserManager") as mock:
        instance = AsyncMock()
        mock.return_value = instance
        yield instance


@pytest.fixture
def mock_exchange_client():
    """Mock ExchangeOnlineClient."""
    with patch("sjifire.scripts.ms_group_sync.ExchangeOnlineClient") as mock:
        instance = AsyncMock()
        mock.return_value = instance
        yield instance


@pytest.fixture
def manager():
    """Create a UnifiedGroupSyncManager for testing."""
    return UnifiedGroupSyncManager(domain="test.org")


# =============================================================================
# GroupType Enum Tests
# =============================================================================


class TestGroupType:
    """Tests for GroupType enum."""

    def test_m365_value(self):
        """M365 type has correct value."""
        assert GroupType.M365.value == "m365"

    def test_exchange_value(self):
        """Exchange type has correct value."""
        assert GroupType.EXCHANGE.value == "exchange"

    def test_both_value(self):
        """Both type has correct value."""
        assert GroupType.BOTH.value == "both"

    def test_none_value(self):
        """None type has correct value."""
        assert GroupType.NONE.value == "none"


# =============================================================================
# UnifiedGroupSyncManager Tests
# =============================================================================


class TestUnifiedGroupSyncManager:
    """Tests for UnifiedGroupSyncManager class."""

    def test_init_sets_domain(self):
        """Manager initializes with correct domain."""
        manager = UnifiedGroupSyncManager(domain="example.org")
        assert manager.domain == "example.org"

    def test_init_default_domain(self):
        """Manager uses default domain if not specified."""
        manager = UnifiedGroupSyncManager()
        assert manager.domain == "sjifire.org"

    def test_lazy_load_entra_groups(self, manager, mock_entra_groups):
        """EntraGroupManager is lazy-loaded."""
        # Access the property to trigger lazy load
        _ = manager.entra_groups
        assert manager._entra_groups is not None

    def test_lazy_load_entra_users(self, manager, mock_entra_users):
        """EntraUserManager is lazy-loaded."""
        _ = manager.entra_users
        assert manager._entra_users is not None

    def test_lazy_load_exchange_client(self, manager, mock_exchange_client):
        """ExchangeOnlineClient is lazy-loaded."""
        _ = manager.exchange_client
        assert manager._exchange_client is not None


# =============================================================================
# detect_group_type Tests
# =============================================================================


class TestDetectGroupType:
    """Tests for detect_group_type method."""

    @pytest.mark.asyncio
    async def test_detects_m365_unified_group(self, manager, mock_entra_groups):
        """Detects M365 Unified groups correctly."""
        mock_group = MagicMock()
        mock_group.group_types = ["Unified"]
        mock_group.mail_enabled = True
        mock_group.security_enabled = False

        mock_entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=mock_group)
        manager._entra_groups = mock_entra_groups

        result = await manager.detect_group_type("test@test.org", "test")

        assert result == GroupType.M365

    @pytest.mark.asyncio
    async def test_detects_exchange_mail_security_group(self, manager, mock_entra_groups):
        """Detects Exchange mail-enabled security groups correctly."""
        mock_group = MagicMock()
        mock_group.group_types = []  # Not Unified
        mock_group.mail_enabled = True
        mock_group.security_enabled = True

        mock_entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=mock_group)
        manager._entra_groups = mock_entra_groups

        result = await manager.detect_group_type("test@test.org", "test")

        assert result == GroupType.EXCHANGE

    @pytest.mark.asyncio
    async def test_detects_no_group(self, manager, mock_entra_groups, mock_exchange_client):
        """Returns NONE when group doesn't exist."""
        mock_entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=None)
        mock_exchange_client.get_distribution_group = AsyncMock(return_value=None)

        manager._entra_groups = mock_entra_groups
        manager._exchange_client = mock_exchange_client

        result = await manager.detect_group_type("test@test.org", "test")

        assert result == GroupType.NONE

    @pytest.mark.asyncio
    async def test_handles_entra_error_gracefully(
        self, manager, mock_entra_groups, mock_exchange_client
    ):
        """Handles Entra API errors gracefully."""
        mock_entra_groups.get_group_by_mail_nickname = AsyncMock(side_effect=Exception("API Error"))
        mock_exchange_client.get_distribution_group = AsyncMock(return_value=None)

        manager._entra_groups = mock_entra_groups
        manager._exchange_client = mock_exchange_client

        result = await manager.detect_group_type("test@test.org", "test")

        assert result == GroupType.NONE


# =============================================================================
# _add_svc_automations_to_group Tests
# =============================================================================


class TestAddSvcAutomationsToGroup:
    """Tests for _add_svc_automations_to_group method."""

    @pytest.mark.asyncio
    async def test_adds_svc_automations_successfully(
        self, manager, mock_entra_users, mock_entra_groups
    ):
        """Successfully adds svc-automations to group."""
        mock_svc_user = MagicMock()
        mock_svc_user.id = "svc-user-id"
        mock_svc_user.email = "svc-automations@sjifire.org"

        mock_entra_users.get_users = AsyncMock(return_value=[mock_svc_user])
        mock_entra_groups.add_user_to_group = AsyncMock(return_value=True)

        manager._entra_users = mock_entra_users
        manager._entra_groups = mock_entra_groups

        result = await manager._add_svc_automations_to_group("group-id")

        assert result is True
        mock_entra_groups.add_user_to_group.assert_called_with("group-id", "svc-user-id")

    @pytest.mark.asyncio
    async def test_returns_false_when_svc_user_not_found(self, manager, mock_entra_users):
        """Returns False when svc-automations user doesn't exist."""
        mock_entra_users.get_users = AsyncMock(return_value=[])
        manager._entra_users = mock_entra_users

        result = await manager._add_svc_automations_to_group("group-id")

        assert result is False

    @pytest.mark.asyncio
    async def test_retries_on_failure(self, manager, mock_entra_users, mock_entra_groups):
        """Retries adding user when initial attempts fail."""
        mock_svc_user = MagicMock()
        mock_svc_user.id = "svc-user-id"
        mock_svc_user.email = "svc-automations@sjifire.org"

        mock_entra_users.get_users = AsyncMock(return_value=[mock_svc_user])

        # Fail first 2 times, succeed on 3rd
        mock_entra_groups.add_user_to_group = AsyncMock(side_effect=[False, False, True])

        manager._entra_users = mock_entra_users
        manager._entra_groups = mock_entra_groups

        result = await manager._add_svc_automations_to_group("group-id")

        assert result is True
        assert mock_entra_groups.add_user_to_group.call_count == 3

    @pytest.mark.asyncio
    async def test_returns_false_after_max_retries(
        self, manager, mock_entra_users, mock_entra_groups
    ):
        """Returns False after exhausting all retries."""
        mock_svc_user = MagicMock()
        mock_svc_user.id = "svc-user-id"
        mock_svc_user.email = "svc-automations@sjifire.org"

        mock_entra_users.get_users = AsyncMock(return_value=[mock_svc_user])
        mock_entra_groups.add_user_to_group = AsyncMock(return_value=False)

        manager._entra_users = mock_entra_users
        manager._entra_groups = mock_entra_groups

        result = await manager._add_svc_automations_to_group("group-id")

        assert result is False
        # Should have retried 5 times (max attempts)
        assert mock_entra_groups.add_user_to_group.call_count == 5


# =============================================================================
# _get_group_members_with_retry Tests
# =============================================================================


class TestGetGroupMembersWithRetry:
    """Tests for _get_group_members_with_retry method."""

    @pytest.mark.asyncio
    async def test_returns_members_directly_for_existing_group(self, manager, mock_entra_groups):
        """For existing groups, returns members without retry logic."""
        mock_entra_groups.get_group_members = AsyncMock(return_value=["user1", "user2", "user3"])
        manager._entra_groups = mock_entra_groups

        result = await manager._get_group_members_with_retry("group-id", newly_created=False)

        assert result == {"user1", "user2", "user3"}
        assert mock_entra_groups.get_group_members.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_for_newly_created_group(self, manager, mock_entra_groups):
        """For newly created groups, retries on failure."""
        # Fail first 2 times (404), succeed on 3rd
        mock_entra_groups.get_group_members = AsyncMock(
            side_effect=[
                Exception("404 Not Found"),
                Exception("404 Not Found"),
                ["user1", "user2"],
            ]
        )
        manager._entra_groups = mock_entra_groups

        result = await manager._get_group_members_with_retry("group-id", newly_created=True)

        assert result == {"user1", "user2"}
        assert mock_entra_groups.get_group_members.call_count == 3

    @pytest.mark.asyncio
    async def test_returns_empty_set_after_max_retries(self, manager, mock_entra_groups):
        """Returns empty set after exhausting retries."""
        mock_entra_groups.get_group_members = AsyncMock(side_effect=Exception("404 Not Found"))
        manager._entra_groups = mock_entra_groups

        result = await manager._get_group_members_with_retry("group-id", newly_created=True)

        assert result == set()
        assert mock_entra_groups.get_group_members.call_count == 5


# =============================================================================
# Delete Group Tests
# =============================================================================


class TestDeleteGroup:
    """Tests for delete_group function."""

    @pytest.mark.asyncio
    async def test_delete_detects_invalid_email_format(self):
        """Returns error for invalid email format."""
        from sjifire.scripts.ms_group_sync import delete_group

        result = await delete_group("invalid-email", dry_run=True)

        assert result == 1  # Error exit code

    @pytest.mark.asyncio
    async def test_delete_returns_error_for_nonexistent_group(self):
        """Returns error when group doesn't exist."""
        from sjifire.scripts.ms_group_sync import delete_group

        with patch.object(
            UnifiedGroupSyncManager, "detect_group_type", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = GroupType.NONE

            with patch.object(UnifiedGroupSyncManager, "close", new_callable=AsyncMock):
                result = await delete_group("test@test.org", dry_run=True)

        assert result == 1

    @pytest.mark.asyncio
    async def test_delete_returns_error_for_conflict(self):
        """Returns error when group exists in both M365 and Exchange."""
        from sjifire.scripts.ms_group_sync import delete_group

        with patch.object(
            UnifiedGroupSyncManager, "detect_group_type", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = GroupType.BOTH

            with patch.object(UnifiedGroupSyncManager, "close", new_callable=AsyncMock):
                result = await delete_group("test@test.org", dry_run=False)

        assert result == 1


# =============================================================================
# _sync_m365_group Integration Tests
# =============================================================================


class TestSyncM365Group:
    """Integration tests for _sync_m365_group method."""

    def _make_entra_user(
        self,
        user_id: str = "user-1",
        display_name: str = "John Doe",
        email: str = "john@test.org",
    ) -> EntraUser:
        """Helper to create mock EntraUser."""
        return EntraUser(
            id=user_id,
            display_name=display_name,
            email=email,
            upn=email,
            first_name=display_name.split()[0],
            last_name=display_name.split()[-1] if " " in display_name else "",
            account_enabled=True,
            employee_id=None,
        )

    @pytest.mark.asyncio
    async def test_creates_new_m365_group(self, manager, mock_entra_groups, mock_entra_users):
        """Creates new M365 group when it doesn't exist."""
        mock_group = MagicMock()
        mock_group.id = "new-group-id"

        mock_entra_groups.create_m365_group = AsyncMock(return_value=mock_group)
        mock_entra_groups.get_group_members = AsyncMock(return_value=[])
        mock_entra_groups.add_user_to_group = AsyncMock(return_value=True)

        # Mock svc-automations user for auto-add
        mock_svc = MagicMock()
        mock_svc.id = "svc-id"
        mock_svc.email = "svc-automations@sjifire.org"
        mock_entra_users.get_users = AsyncMock(return_value=[mock_svc])

        manager._entra_groups = mock_entra_groups
        manager._entra_users = mock_entra_users

        strategy = FirefighterStrategy()
        user = self._make_entra_user()

        result = await manager._sync_m365_group(
            strategy=strategy,
            group_key="FF",
            group_members=[user],
            dry_run=False,
            creating=True,
        )

        assert result.created is True
        assert result.group_type == GroupType.M365
        mock_entra_groups.create_m365_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_adds_members_to_existing_group(self, manager, mock_entra_groups):
        """Adds missing members to existing M365 group."""
        mock_group = MagicMock()
        mock_group.id = "existing-group-id"

        mock_entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=mock_group)
        mock_entra_groups.get_group_members = AsyncMock(return_value=[])  # No current members
        mock_entra_groups.add_user_to_group = AsyncMock(return_value=True)

        manager._entra_groups = mock_entra_groups
        manager._entra_users_cache = []  # Empty cache for removal lookup

        strategy = FirefighterStrategy()
        user = self._make_entra_user()

        result = await manager._sync_m365_group(
            strategy=strategy,
            group_key="FF",
            group_members=[user],
            dry_run=False,
            creating=False,
        )

        assert "John Doe" in result.members_added
        mock_entra_groups.add_user_to_group.assert_called_with("existing-group-id", "user-1")

    @pytest.mark.asyncio
    async def test_removes_extra_members(self, manager, mock_entra_groups):
        """Removes members who shouldn't be in the group."""
        mock_group = MagicMock()
        mock_group.id = "group-id"

        # Current group has user-2, but only user-1 should be there
        mock_entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=mock_group)
        mock_entra_groups.get_group_members = AsyncMock(return_value=["user-2"])
        mock_entra_groups.remove_user_from_group = AsyncMock(return_value=True)

        # Set up user cache for removal lookup
        extra_user = self._make_entra_user(user_id="user-2", display_name="Extra User")
        manager._entra_groups = mock_entra_groups
        manager._entra_users_cache = [extra_user]

        strategy = FirefighterStrategy()
        user = self._make_entra_user(user_id="user-1")

        result = await manager._sync_m365_group(
            strategy=strategy,
            group_key="FF",
            group_members=[user],
            dry_run=False,
            creating=False,
        )

        assert "Extra User" in result.members_removed
        mock_entra_groups.remove_user_from_group.assert_called_with("group-id", "user-2")

    @pytest.mark.asyncio
    async def test_dry_run_does_not_modify(self, manager, mock_entra_groups):
        """Dry run shows changes without modifying."""
        mock_entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=None)

        manager._entra_groups = mock_entra_groups

        strategy = FirefighterStrategy()
        user = self._make_entra_user()

        await manager._sync_m365_group(
            strategy=strategy,
            group_key="FF",
            group_members=[user],
            dry_run=True,
            creating=True,
        )

        # Should indicate it would create, but not actually call create
        mock_entra_groups.create_m365_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_sync_preserves_non_source_members(self, manager, mock_entra_groups):
        """Partial sync preserves members not in source data."""
        mock_group = MagicMock()
        mock_group.id = "group-id"

        # Group has user-2 who is not in source
        mock_entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=mock_group)
        mock_entra_groups.get_group_members = AsyncMock(return_value=["user-2"])

        # user-2 is NOT in source_emails, so should be preserved
        extra_user = self._make_entra_user(
            user_id="user-2", display_name="Board Member", email="board@test.org"
        )
        manager._entra_groups = mock_entra_groups
        manager._entra_users_cache = [extra_user]

        strategy = FirefighterStrategy()
        user = self._make_entra_user(user_id="user-1", email="john@test.org")

        # source_emails only has john@test.org, not board@test.org
        result = await manager._sync_m365_group(
            strategy=strategy,
            group_key="FF",
            group_members=[user],
            dry_run=False,
            creating=False,
            partial_sync=True,
            source_emails={"john@test.org"},
        )

        # Board member should NOT be removed
        assert "Board Member" not in result.members_removed
        mock_entra_groups.remove_user_from_group.assert_not_called()


# =============================================================================
# _sync_exchange_group Integration Tests
# =============================================================================


class TestSyncExchangeGroup:
    """Integration tests for _sync_exchange_group method."""

    def _make_entra_user(
        self,
        user_id: str = "user-1",
        display_name: str = "John Doe",
        email: str = "john@test.org",
    ) -> EntraUser:
        """Helper to create mock EntraUser."""
        return EntraUser(
            id=user_id,
            display_name=display_name,
            email=email,
            upn=email,
            first_name=display_name.split()[0],
            last_name=display_name.split()[-1] if " " in display_name else "",
            account_enabled=True,
            employee_id=None,
        )

    @pytest.mark.asyncio
    async def test_creates_new_exchange_group(self, manager, mock_exchange_client):
        """Creates new Exchange group when it doesn't exist."""
        mock_group = MagicMock()
        mock_group.identity = "new-group"

        mock_exchange_client.create_mail_enabled_security_group = AsyncMock(return_value=mock_group)
        mock_exchange_client.sync_group = AsyncMock(
            return_value={"group": mock_group, "added": [], "removed": [], "errors": []}
        )

        manager._exchange_client = mock_exchange_client

        strategy = FirefighterStrategy()
        user = self._make_entra_user()

        result = await manager._sync_exchange_group(
            strategy=strategy,
            group_key="FF",
            group_members=[user],
            dry_run=False,
            creating=True,
        )

        assert result.created is True
        assert result.group_type == GroupType.EXCHANGE
        mock_exchange_client.create_mail_enabled_security_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_syncs_existing_exchange_group(self, manager, mock_exchange_client):
        """Syncs members to existing Exchange group."""
        mock_group = MagicMock()

        mock_exchange_client.sync_group = AsyncMock(
            return_value={
                "group": mock_group,
                "added": ["john@test.org"],
                "removed": ["old@test.org"],
                "errors": [],
            }
        )

        manager._exchange_client = mock_exchange_client

        strategy = FirefighterStrategy()
        user = self._make_entra_user()

        result = await manager._sync_exchange_group(
            strategy=strategy,
            group_key="FF",
            group_members=[user],
            dry_run=False,
            creating=False,
        )

        assert "John Doe" in result.members_added
        assert "old@test.org" in result.members_removed

    @pytest.mark.asyncio
    async def test_dry_run_shows_changes(self, manager, mock_exchange_client):
        """Dry run shows what would change without modifying."""
        mock_group = MagicMock()
        mock_exchange_client.get_group_with_members = AsyncMock(
            return_value=(mock_group, ["existing@test.org"])
        )

        manager._exchange_client = mock_exchange_client

        strategy = FirefighterStrategy()
        user = self._make_entra_user()

        result = await manager._sync_exchange_group(
            strategy=strategy,
            group_key="FF",
            group_members=[user],
            dry_run=True,
            creating=False,
        )

        # Should show john would be added, existing would be removed
        assert "John Doe" in result.members_added
        assert "existing@test.org" in result.members_removed
        # Should not call sync_group
        mock_exchange_client.sync_group.assert_not_called()


# =============================================================================
# sync_group Integration Tests
# =============================================================================


class TestSyncGroup:
    """Integration tests for sync_group method."""

    def _make_entra_user(
        self,
        user_id: str = "user-1",
        display_name: str = "John Doe",
        email: str = "john@test.org",
    ) -> EntraUser:
        """Helper to create mock EntraUser."""
        return EntraUser(
            id=user_id,
            display_name=display_name,
            email=email,
            upn=email,
            first_name=display_name.split()[0],
            last_name=display_name.split()[-1] if " " in display_name else "",
            account_enabled=True,
            employee_id=None,
        )

    @pytest.mark.asyncio
    async def test_routes_to_m365_for_existing_m365_group(self, manager):
        """Routes to M365 sync for existing M365 groups."""
        with patch.object(manager, "detect_group_type", new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = GroupType.M365

            with patch.object(manager, "_sync_m365_group", new_callable=AsyncMock) as mock_sync:
                mock_sync.return_value = GroupSyncResult(
                    group_name="Test",
                    group_email="test@test.org",
                    group_type=GroupType.M365,
                )

                strategy = FirefighterStrategy()
                user = self._make_entra_user()

                await manager.sync_group(
                    strategy=strategy,
                    group_key="FF",
                    group_members=[user],
                    new_group_type=GroupType.EXCHANGE,  # Should be ignored
                    dry_run=False,
                )

                mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_to_exchange_for_existing_exchange_group(self, manager):
        """Routes to Exchange sync for existing Exchange groups."""
        with patch.object(manager, "detect_group_type", new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = GroupType.EXCHANGE

            with patch.object(manager, "_sync_exchange_group", new_callable=AsyncMock) as mock_sync:
                mock_sync.return_value = GroupSyncResult(
                    group_name="Test",
                    group_email="test@test.org",
                    group_type=GroupType.EXCHANGE,
                )

                strategy = FirefighterStrategy()
                user = self._make_entra_user()

                await manager.sync_group(
                    strategy=strategy,
                    group_key="FF",
                    group_members=[user],
                    new_group_type=GroupType.M365,  # Should be ignored
                    dry_run=False,
                )

                mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_new_group_type_for_new_groups(self, manager):
        """Uses specified new_group_type when creating new groups."""
        with patch.object(manager, "detect_group_type", new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = GroupType.NONE  # Group doesn't exist

            with patch.object(manager, "_sync_m365_group", new_callable=AsyncMock) as mock_sync:
                mock_sync.return_value = GroupSyncResult(
                    group_name="Test",
                    group_email="test@test.org",
                    group_type=GroupType.M365,
                )

                strategy = FirefighterStrategy()
                user = self._make_entra_user()

                await manager.sync_group(
                    strategy=strategy,
                    group_key="FF",
                    group_members=[user],
                    new_group_type=GroupType.M365,
                    dry_run=False,
                )

                mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_group_with_conflict(self, manager):
        """Skips groups that exist in both M365 and Exchange."""
        with patch.object(manager, "detect_group_type", new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = GroupType.BOTH

            strategy = FirefighterStrategy()
            user = self._make_entra_user()

            result = await manager.sync_group(
                strategy=strategy,
                group_key="FF",
                group_members=[user],
                new_group_type=GroupType.M365,
                dry_run=False,
            )

            assert result.skipped is True
            assert "both" in result.skip_reason.lower()


# =============================================================================
# Delete M365/Exchange Group Tests
# =============================================================================


class TestDeleteM365Group:
    """Tests for _delete_m365_group function."""

    @pytest.mark.asyncio
    async def test_deletes_m365_group_with_backup(self):
        """Deletes M365 group after creating backup."""
        from sjifire.scripts.ms_group_sync import _delete_m365_group

        mock_manager = MagicMock()
        mock_manager.entra_groups = AsyncMock()

        mock_group = MagicMock()
        mock_group.id = "group-id"
        mock_group.display_name = "Test Group"
        mock_group.mail = "test@test.org"

        mock_manager.entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=mock_group)
        mock_manager.entra_groups.get_group_members = AsyncMock(return_value=["user1"])
        mock_manager.entra_groups.delete_group = AsyncMock(return_value=True)

        with patch("sjifire.scripts.ms_group_sync.backup_entra_groups") as mock_backup:
            mock_backup.return_value = "/path/to/backup.json"

            result = await _delete_m365_group(mock_manager, "test@test.org", "test", False)

        assert result == 0
        mock_backup.assert_called_once()
        mock_manager.entra_groups.delete_group.assert_called_with("group-id")

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self):
        """Dry run shows info but doesn't delete."""
        from sjifire.scripts.ms_group_sync import _delete_m365_group

        mock_manager = MagicMock()
        mock_manager.entra_groups = AsyncMock()

        mock_group = MagicMock()
        mock_group.id = "group-id"
        mock_group.display_name = "Test Group"
        mock_group.mail = "test@test.org"

        mock_manager.entra_groups.get_group_by_mail_nickname = AsyncMock(return_value=mock_group)
        mock_manager.entra_groups.get_group_members = AsyncMock(return_value=[])

        result = await _delete_m365_group(mock_manager, "test@test.org", "test", True)

        assert result == 0
        mock_manager.entra_groups.delete_group.assert_not_called()


class TestDeleteExchangeGroup:
    """Tests for _delete_exchange_group function."""

    @pytest.mark.asyncio
    async def test_deletes_exchange_group_with_backup(self):
        """Deletes Exchange group after creating backup."""
        from sjifire.scripts.ms_group_sync import _delete_exchange_group

        mock_manager = MagicMock()
        mock_manager.exchange_client = AsyncMock()

        mock_group = MagicMock()
        mock_group.identity = "group-id"
        mock_group.display_name = "Test Group"
        mock_group.primary_smtp_address = "test@test.org"
        mock_group.group_type = "MailEnabledSecurity"

        mock_manager.exchange_client.get_group_with_members = AsyncMock(
            return_value=(mock_group, ["user1@test.org"])
        )
        mock_manager.exchange_client.delete_distribution_group = AsyncMock(return_value=True)

        with patch("sjifire.scripts.ms_group_sync.backup_mail_groups") as mock_backup:
            mock_backup.return_value = "/path/to/backup.json"

            result = await _delete_exchange_group(mock_manager, "test@test.org", False)

        assert result == 0
        mock_backup.assert_called_once()
        mock_manager.exchange_client.delete_distribution_group.assert_called_with("test@test.org")

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self):
        """Dry run shows info but doesn't delete."""
        from sjifire.scripts.ms_group_sync import _delete_exchange_group

        mock_manager = MagicMock()
        mock_manager.exchange_client = AsyncMock()

        mock_group = MagicMock()
        mock_group.identity = "group-id"
        mock_group.display_name = "Test Group"
        mock_group.primary_smtp_address = "test@test.org"

        mock_manager.exchange_client.get_group_with_members = AsyncMock(
            return_value=(mock_group, [])
        )

        result = await _delete_exchange_group(mock_manager, "test@test.org", True)

        assert result == 0
        mock_manager.exchange_client.delete_distribution_group.assert_not_called()
