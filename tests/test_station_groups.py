"""Tests for group sync management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.aladtec.models import MARINE_POSITIONS, OPERATIONAL_POSITIONS, Member
from sjifire.entra.group_sync import (
    FullSyncResult,
    GroupSyncManager,
    GroupSyncResult,
    MarineGroupStrategy,
    StationGroupStrategy,
    SupportGroupStrategy,
    VolunteerGroupStrategy,
)
from sjifire.entra.groups import EntraGroup
from sjifire.entra.users import EntraUser


class TestStationGroupStrategy:
    """Tests for StationGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = StationGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "station"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "Aladtec" in notice

    def test_get_full_description(self):
        full_desc = self.strategy.get_full_description("31")
        assert "Station 31" in full_desc
        assert "Aladtec" in full_desc
        assert "automatically" in full_desc.lower()

    def test_parse_station_none(self):
        assert self.strategy._parse_station(None) is None

    def test_parse_station_empty(self):
        assert self.strategy._parse_station("") is None

    def test_parse_station_numeric(self):
        assert self.strategy._parse_station("31") == "31"
        assert self.strategy._parse_station("32") == "32"
        assert self.strategy._parse_station("1") == "1"

    def test_parse_station_with_prefix(self):
        assert self.strategy._parse_station("Station 31") == "31"
        assert self.strategy._parse_station("Station 32") == "32"

    def test_parse_station_case_insensitive(self):
        assert self.strategy._parse_station("station 31") == "31"
        assert self.strategy._parse_station("STATION 31") == "31"

    def test_parse_station_whitespace(self):
        assert self.strategy._parse_station("  31  ") == "31"
        assert self.strategy._parse_station("  Station 31  ") == "31"

    def test_parse_station_non_numeric(self):
        assert self.strategy._parse_station("Main") is None
        assert self.strategy._parse_station("Headquarters") is None

    def test_get_group_config(self):
        display_name, mail_nickname, description = self.strategy.get_group_config("31")
        assert display_name == "Station 31"
        assert mail_nickname == "station31"
        assert description == "Members assigned to Station 31"

    def test_get_group_config_single_digit(self):
        display_name, mail_nickname, _ = self.strategy.get_group_config("1")
        assert display_name == "Station 1"
        assert mail_nickname == "station1"

    def test_get_groups_to_sync_empty(self):
        assert self.strategy.get_groups_to_sync([]) == {}

    def test_get_groups_to_sync_single_member(self):
        member = Member(id="1", first_name="John", last_name="Doe", station_assignment="31")
        result = self.strategy.get_groups_to_sync([member])
        assert "31" in result
        assert len(result["31"]) == 1

    def test_get_groups_to_sync_multiple_stations(self):
        members = [
            Member(id="1", first_name="John", last_name="Doe", station_assignment="31"),
            Member(id="2", first_name="Jane", last_name="Smith", station_assignment="32"),
            Member(id="3", first_name="Bob", last_name="Wilson", station_assignment="31"),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert len(result) == 2
        assert len(result["31"]) == 2
        assert len(result["32"]) == 1

    def test_get_groups_to_sync_ignores_none(self):
        members = [
            Member(id="1", first_name="John", last_name="Doe", station_assignment="31"),
            Member(id="2", first_name="Jane", last_name="Smith", station_assignment=None),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert len(result) == 1
        assert "31" in result


class TestSupportGroupStrategy:
    """Tests for SupportGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = SupportGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "support"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "Support" in notice

    def test_get_group_config(self):
        display_name, mail_nickname, description = self.strategy.get_group_config("Support")
        assert display_name == "Support"
        assert mail_nickname == "support"
        assert "Support" in description

    def test_get_groups_to_sync_empty(self):
        assert self.strategy.get_groups_to_sync([]) == {}

    def test_get_groups_to_sync_support_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Support"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Support" in result
        assert len(result["Support"]) == 1

    def test_get_groups_to_sync_ignores_non_support(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Firefighter", "EMT"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}

    def test_get_groups_to_sync_multiple_members(self):
        members = [
            Member(id="1", first_name="John", last_name="Doe", positions=["Support"]),
            Member(id="2", first_name="Jane", last_name="Smith", positions=["Support", "Admin"]),
            Member(id="3", first_name="Bob", last_name="Wilson", positions=["Firefighter"]),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert len(result) == 1
        assert len(result["Support"]) == 2


class TestFirefighterGroupStrategy:
    """Tests for FirefighterGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        from sjifire.entra.group_sync import FirefighterGroupStrategy

        self.strategy = FirefighterGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "ff"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "Firefighter" in notice

    def test_get_group_config(self):
        display_name, mail_nickname, description = self.strategy.get_group_config("FF")
        assert display_name == "FF"
        assert mail_nickname == "ff"
        assert "Firefighter" in description

    def test_get_groups_to_sync_empty(self):
        assert self.strategy.get_groups_to_sync([]) == {}

    def test_get_groups_to_sync_firefighter_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Firefighter"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "FF" in result
        assert len(result["FF"]) == 1

    def test_get_groups_to_sync_ignores_non_firefighter(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Support", "EMT"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}

    def test_get_groups_to_sync_multiple_members(self):
        members = [
            Member(id="1", first_name="John", last_name="Doe", positions=["Firefighter"]),
            Member(
                id="2",
                first_name="Jane",
                last_name="Smith",
                positions=["Firefighter", "EMT"],
            ),
            Member(id="3", first_name="Bob", last_name="Wilson", positions=["Support"]),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert len(result) == 1
        assert len(result["FF"]) == 2


class TestWildlandFirefighterGroupStrategy:
    """Tests for WildlandFirefighterGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        from sjifire.entra.group_sync import WildlandFirefighterGroupStrategy

        self.strategy = WildlandFirefighterGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "wff"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "Wildland Firefighter" in notice

    def test_get_group_config(self):
        display_name, mail_nickname, description = self.strategy.get_group_config("WFF")
        assert display_name == "WFF"
        assert mail_nickname == "wff"
        assert "Wildland Firefighter" in description

    def test_get_groups_to_sync_empty(self):
        assert self.strategy.get_groups_to_sync([]) == {}

    def test_get_groups_to_sync_wff_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Wildland Firefighter"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "WFF" in result
        assert len(result["WFF"]) == 1

    def test_get_groups_to_sync_ignores_non_wff(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Firefighter", "Support"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}

    def test_get_groups_to_sync_multiple_members(self):
        members = [
            Member(id="1", first_name="John", last_name="Doe", positions=["Wildland Firefighter"]),
            Member(
                id="2",
                first_name="Jane",
                last_name="Smith",
                positions=["Wildland Firefighter", "Firefighter"],
            ),
            Member(id="3", first_name="Bob", last_name="Wilson", positions=["Support"]),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert len(result) == 1
        assert len(result["WFF"]) == 2


class TestApparatusOperatorGroupStrategy:
    """Tests for ApparatusOperatorGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        from sjifire.entra.group_sync import ApparatusOperatorGroupStrategy

        self.strategy = ApparatusOperatorGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "ao"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "EVIP" in notice

    def test_get_group_config(self):
        display_name, mail_nickname, description = self.strategy.get_group_config(
            "Apparatus Operator"
        )
        assert display_name == "Apparatus Operator"
        assert mail_nickname == "apparatus-operator"
        assert "EVIP" in description

    def test_get_groups_to_sync_empty(self):
        assert self.strategy.get_groups_to_sync([]) == {}

    def test_get_groups_to_sync_with_evip(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            evip="2026-06-30",
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Apparatus Operator" in result
        assert len(result["Apparatus Operator"]) == 1

    def test_get_groups_to_sync_without_evip(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            evip=None,
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}

    def test_get_groups_to_sync_multiple_members(self):
        members = [
            Member(id="1", first_name="John", last_name="Doe", evip="2026-06-30"),
            Member(id="2", first_name="Jane", last_name="Smith", evip="2026-12-31"),
            Member(id="3", first_name="Bob", last_name="Wilson", evip=None),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert len(result) == 1
        assert len(result["Apparatus Operator"]) == 2


class TestMarineGroupStrategy:
    """Tests for MarineGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MarineGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "marine"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "Marine" in notice

    def test_get_group_config(self):
        display_name, mail_nickname, description = self.strategy.get_group_config("Marine")
        assert display_name == "Marine"
        assert mail_nickname == "marine"
        assert "Marine" in description

    def test_get_groups_to_sync_empty(self):
        assert self.strategy.get_groups_to_sync([]) == {}

    def test_get_groups_to_sync_mate_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Marine: Mate"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_groups_to_sync_pilot_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Marine: Pilot"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_groups_to_sync_deckhand_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Marine: Deckhand"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_groups_to_sync_multiple_marine_positions(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Marine: Mate", "Marine: Pilot"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Marine" in result
        assert len(result["Marine"]) == 1  # Same member, not duplicated

    def test_get_groups_to_sync_ignores_non_marine(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Firefighter", "Support"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}

    def test_get_groups_to_sync_multiple_members(self):
        members = [
            Member(id="1", first_name="John", last_name="Doe", positions=["Marine: Mate"]),
            Member(id="2", first_name="Jane", last_name="Smith", positions=["Marine: Pilot"]),
            Member(id="3", first_name="Bob", last_name="Wilson", positions=["Firefighter"]),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert len(result) == 1
        assert len(result["Marine"]) == 2

    def test_marine_positions_include_all_expected(self):
        """Verify all expected marine positions are configured."""
        expected = {"Marine: Deckhand", "Marine: Mate", "Marine: Pilot"}
        assert expected == MARINE_POSITIONS


class TestVolunteerGroupStrategy:
    """Tests for VolunteerGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = VolunteerGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "volunteers"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "Work Group" in notice

    def test_get_group_config(self):
        display_name, mail_nickname, description = self.strategy.get_group_config("Volunteers")
        assert display_name == "Volunteers"
        assert mail_nickname == "volunteers"
        assert "Volunteer" in description

    def test_get_groups_to_sync_empty(self):
        assert self.strategy.get_groups_to_sync([]) == {}

    def test_get_groups_to_sync_volunteer_with_operational_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            work_group="Volunteer",
            positions=["Firefighter"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Volunteers" in result
        assert len(result["Volunteers"]) == 1

    def test_get_groups_to_sync_volunteer_without_operational_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            work_group="Volunteer",
            positions=[],  # No positions
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}

    def test_get_groups_to_sync_non_volunteer_with_operational_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            work_group="FT Line Staff",  # Not a volunteer
            positions=["Firefighter"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}

    def test_get_groups_to_sync_volunteer_with_wildland_firefighter(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            work_group="Volunteer",
            positions=["Wildland Firefighter"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Volunteers" in result
        assert len(result["Volunteers"]) == 1

    def test_get_groups_to_sync_multiple_members(self):
        members = [
            Member(
                id="1",
                first_name="John",
                last_name="Doe",
                work_group="Volunteer",
                positions=["Firefighter"],
            ),
            Member(
                id="2",
                first_name="Jane",
                last_name="Smith",
                work_group="Volunteer",
                positions=["Support"],
            ),
            Member(
                id="3",
                first_name="Bob",
                last_name="Wilson",
                work_group="Volunteer",
                positions=[],  # Excluded - no operational position
            ),
            Member(
                id="4",
                first_name="Alice",
                last_name="Brown",
                work_group="FT Line Staff",  # Excluded - not a volunteer
                positions=["Firefighter"],
            ),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert "Volunteers" in result
        assert len(result["Volunteers"]) == 2

    def test_operational_positions_include_all_expected(self):
        """Verify all expected operational positions are configured."""
        expected = {
            "Firefighter",
            "Apparatus Operator",
            "Support",
            "Marine: Deckhand",
            "Marine: Mate",
            "Marine: Pilot",
            "Wildland Firefighter",
        }
        assert expected == OPERATIONAL_POSITIONS


class TestGroupSyncResult:
    """Tests for GroupSyncResult dataclass."""

    def test_has_changes_when_created(self):
        result = GroupSyncResult(group_name="Test", group_id="123", created=True)
        assert result.has_changes is True

    def test_has_changes_when_members_added(self):
        result = GroupSyncResult(
            group_name="Test",
            group_id="123",
            created=False,
            members_added=["User1"],
        )
        assert result.has_changes is True

    def test_has_changes_when_members_removed(self):
        result = GroupSyncResult(
            group_name="Test",
            group_id="123",
            created=False,
            members_removed=["User1"],
        )
        assert result.has_changes is True

    def test_no_changes(self):
        result = GroupSyncResult(group_name="Test", group_id="123", created=False)
        assert result.has_changes is False


class TestFullSyncResult:
    """Tests for FullSyncResult dataclass."""

    def test_totals_empty(self):
        result = FullSyncResult(group_type="test")
        assert result.total_created == 0
        assert result.total_added == 0
        assert result.total_removed == 0
        assert result.total_errors == 0

    def test_totals_with_groups(self):
        result = FullSyncResult(
            group_type="test",
            groups=[
                GroupSyncResult(
                    group_name="Group1",
                    group_id="1",
                    created=True,
                    members_added=["A", "B"],
                    members_removed=["C"],
                    errors=["Error1"],
                ),
                GroupSyncResult(
                    group_name="Group2",
                    group_id="2",
                    created=False,
                    members_added=["D"],
                    members_removed=[],
                    errors=[],
                ),
            ],
        )
        assert result.total_created == 1
        assert result.total_added == 3
        assert result.total_removed == 1
        assert result.total_errors == 1


class TestGroupSyncManagerVisibility:
    """Tests for GroupSyncManager visibility functionality."""

    def test_apply_group_visibility_dry_run_returns_true(self):
        """Verify dry run mode returns True without calling the manager."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        from sjifire.entra.group_sync import GroupSyncManager
        from sjifire.entra.groups import EntraGroup

        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            manager = GroupSyncManager()
            manager.group_manager = AsyncMock()

            group = EntraGroup(
                id="test-id",
                display_name="Test Group",
                description="Test",
                mail="test@sjifire.org",
                mail_enabled=True,
                security_enabled=False,
                group_types=["Unified"],
            )

            result = asyncio.run(manager._apply_group_visibility(group, dry_run=True))

            assert result is True
            # Should not call update_group_visibility in dry run
            manager.group_manager.update_group_visibility.assert_not_called()

    def test_apply_group_visibility_calls_manager_with_public(self):
        """Verify real run calls manager with Public visibility."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        from sjifire.entra.group_sync import GroupSyncManager
        from sjifire.entra.groups import EntraGroup

        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            manager = GroupSyncManager()
            manager.group_manager = AsyncMock()
            manager.group_manager.update_group_visibility = AsyncMock(return_value=True)

            group = EntraGroup(
                id="test-group-id",
                display_name="Station 31",
                description="Test",
                mail="station31@sjifire.org",
                mail_enabled=True,
                security_enabled=False,
                group_types=["Unified"],
            )

            result = asyncio.run(manager._apply_group_visibility(group, dry_run=False))

            assert result is True
            manager.group_manager.update_group_visibility.assert_called_once_with(
                group_id="test-group-id",
                visibility="Public",
            )

    def test_apply_group_visibility_returns_false_on_failure(self):
        """Verify returns False when manager update fails."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        from sjifire.entra.group_sync import GroupSyncManager
        from sjifire.entra.groups import EntraGroup

        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            manager = GroupSyncManager()
            manager.group_manager = AsyncMock()
            manager.group_manager.update_group_visibility = AsyncMock(return_value=False)

            group = EntraGroup(
                id="test-group-id",
                display_name="Station 31",
                description="Test",
                mail="station31@sjifire.org",
                mail_enabled=True,
                security_enabled=False,
                group_types=["Unified"],
            )

            result = asyncio.run(manager._apply_group_visibility(group, dry_run=False))

            assert result is False


def make_entra_user(
    user_id="user-1",
    display_name="Test User",
    first_name="Test",
    last_name="User",
    email="test@sjifire.org",
    upn="test.user@sjifire.org",
    employee_id="123",
):
    """Helper to create an EntraUser with all required fields."""
    return EntraUser(
        id=user_id,
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        email=email,
        upn=upn,
        employee_id=employee_id,
    )


class TestGroupSyncManagerLoadUsers:
    """Tests for GroupSyncManager._load_entra_users method."""

    @pytest.fixture
    def manager(self):
        """Create a GroupSyncManager with mocked dependencies."""
        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            mgr = GroupSyncManager()
            mgr.group_manager = AsyncMock()
            mgr.user_manager = AsyncMock()
        return mgr

    async def test_load_entra_users_caches_users(self, manager):
        """Test that users are loaded and cached."""
        users = [
            make_entra_user(
                user_id="user-1",
                display_name="John Doe",
                first_name="John",
                last_name="Doe",
                email="john@sjifire.org",
                upn="john.doe@sjifire.org",
            ),
            make_entra_user(
                user_id="user-2",
                display_name="Jane Smith",
                first_name="Jane",
                last_name="Smith",
                email="jane@sjifire.org",
                upn="jane.smith@sjifire.org",
            ),
        ]
        manager.user_manager.get_users = AsyncMock(return_value=users)

        await manager._load_entra_users()

        assert manager._entra_users == users
        assert "john@sjifire.org" in manager._user_by_email
        assert "jane.smith@sjifire.org" in manager._user_by_upn

    async def test_load_entra_users_only_loads_once(self, manager):
        """Test that users are only loaded once."""
        users = [make_entra_user(user_id="user-1", display_name="John", email="john@sjifire.org")]
        manager.user_manager.get_users = AsyncMock(return_value=users)

        await manager._load_entra_users()
        await manager._load_entra_users()

        # Should only be called once
        manager.user_manager.get_users.assert_called_once()


class TestGroupSyncManagerFindUser:
    """Tests for GroupSyncManager._find_entra_user method."""

    @pytest.fixture
    def manager(self):
        """Create a GroupSyncManager with mocked dependencies."""
        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            mgr = GroupSyncManager()
            mgr.user_manager = MagicMock()
            mgr.user_manager.generate_upn = MagicMock(
                side_effect=lambda first, last: f"{first.lower()}.{last.lower()}@sjifire.org"
            )
        return mgr

    def test_find_entra_user_by_email(self, manager):
        """Test finding user by email."""
        user = make_entra_user(user_id="user-1", display_name="John", email="john@sjifire.org")
        manager._user_by_email = {"john@sjifire.org": user}
        manager._user_by_upn = {}

        member = Member(id="1", first_name="John", last_name="Doe", email="john@sjifire.org")
        result = manager._find_entra_user(member)

        assert result == user

    def test_find_entra_user_by_upn(self, manager):
        """Test finding user by UPN when email doesn't match."""
        user = make_entra_user(user_id="user-1", display_name="John", upn="john.doe@sjifire.org")
        manager._user_by_email = {}
        manager._user_by_upn = {"john.doe@sjifire.org": user}

        member = Member(id="1", first_name="John", last_name="Doe", email=None)
        result = manager._find_entra_user(member)

        assert result == user

    def test_find_entra_user_not_found(self, manager):
        """Test when user is not found."""
        manager._user_by_email = {}
        manager._user_by_upn = {}

        member = Member(id="1", first_name="Unknown", last_name="User", email="unknown@test.com")
        result = manager._find_entra_user(member)

        assert result is None


class TestGroupSyncManagerGetOrCreateGroup:
    """Tests for GroupSyncManager._get_or_create_group method."""

    @pytest.fixture
    def manager(self):
        """Create a GroupSyncManager with mocked dependencies."""
        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            mgr = GroupSyncManager()
            mgr.group_manager = AsyncMock()
        return mgr

    def _make_group(self, group_id="g1", display_name="Test", description="Desc"):
        """Helper to create an EntraGroup."""
        return EntraGroup(
            id=group_id,
            display_name=display_name,
            description=description,
            mail=f"{display_name.lower()}@sjifire.org",
            mail_enabled=True,
            security_enabled=False,
            group_types=["Unified"],
        )

    async def test_get_existing_group_by_mail_nickname(self, manager):
        """Test getting existing group by mail nickname."""
        existing = self._make_group(description="Existing desc")
        manager.group_manager.get_group_by_mail_nickname = AsyncMock(return_value=existing)

        group, created, desc_updated = await manager._get_or_create_group(
            display_name="Test",
            mail_nickname="test",
            description="Existing desc",
        )

        assert group == existing
        assert created is False
        assert desc_updated is False

    async def test_get_existing_group_updates_description(self, manager):
        """Test that description is updated when different."""
        existing = self._make_group(description="Old description")
        manager.group_manager.get_group_by_mail_nickname = AsyncMock(return_value=existing)
        manager.group_manager.update_group_description = AsyncMock(return_value=True)

        group, created, desc_updated = await manager._get_or_create_group(
            display_name="Test",
            mail_nickname="test",
            description="New description",
        )

        assert group == existing
        assert created is False
        assert desc_updated is True
        manager.group_manager.update_group_description.assert_called_once()

    async def test_create_group_when_not_exists(self, manager):
        """Test creating a new group when it doesn't exist."""
        manager.group_manager.get_group_by_mail_nickname = AsyncMock(return_value=None)
        manager.group_manager.get_group_by_name = AsyncMock(return_value=None)
        new_group = self._make_group(group_id="new-id")
        manager.group_manager.create_m365_group = AsyncMock(return_value=new_group)

        group, created, desc_updated = await manager._get_or_create_group(
            display_name="New Group",
            mail_nickname="newgroup",
            description="New group description",
        )

        assert group == new_group
        assert created is True
        assert desc_updated is False

    async def test_dry_run_does_not_create(self, manager):
        """Test dry run mode doesn't create groups."""
        manager.group_manager.get_group_by_mail_nickname = AsyncMock(return_value=None)
        manager.group_manager.get_group_by_name = AsyncMock(return_value=None)

        group, created, _desc_updated = await manager._get_or_create_group(
            display_name="New Group",
            mail_nickname="newgroup",
            description="Desc",
            dry_run=True,
        )

        assert group is None
        assert created is True
        manager.group_manager.create_m365_group.assert_not_called()


class TestGroupSyncManagerSyncMembership:
    """Tests for GroupSyncManager._sync_group_membership method."""

    @pytest.fixture
    def manager(self):
        """Create a GroupSyncManager with mocked dependencies."""
        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            mgr = GroupSyncManager()
            mgr.group_manager = AsyncMock()
            mgr._entra_users = []
        return mgr

    def _make_group(self, group_id="g1"):
        """Helper to create an EntraGroup."""
        return EntraGroup(
            id=group_id,
            display_name="Test Group",
            description="Test",
            mail="test@sjifire.org",
            mail_enabled=True,
            security_enabled=False,
            group_types=["Unified"],
        )

    async def test_sync_adds_missing_members(self, manager):
        """Test adding members who should be in the group."""
        group = self._make_group()
        manager.group_manager.get_group_members = AsyncMock(return_value=[])
        manager.group_manager.add_user_to_group = AsyncMock(return_value=True)

        # Set up user matching
        user = make_entra_user(user_id="user-1", display_name="John Doe", email="john@sjifire.org")
        manager._user_by_email = {"john@sjifire.org": user}
        manager._user_by_upn = {}

        member = Member(id="1", first_name="John", last_name="Doe", email="john@sjifire.org")

        added, removed, errors = await manager._sync_group_membership(
            group=group,
            should_be_members=[member],
        )

        assert added == ["John Doe"]
        assert removed == []
        assert errors == []

    async def test_sync_removes_extra_members(self, manager):
        """Test removing members who shouldn't be in the group."""
        group = self._make_group()
        manager.group_manager.get_group_members = AsyncMock(return_value=["user-extra"])
        manager.group_manager.remove_user_from_group = AsyncMock(return_value=True)

        # No members should be in the group
        manager._user_by_email = {}
        manager._user_by_upn = {}
        manager._entra_users = [
            make_entra_user(
                user_id="user-extra", display_name="Extra User", email="extra@sjifire.org"
            )
        ]

        added, removed, errors = await manager._sync_group_membership(
            group=group,
            should_be_members=[],
        )

        assert added == []
        assert removed == ["Extra User"]
        assert errors == []

    async def test_sync_dry_run_does_not_modify(self, manager):
        """Test dry run mode doesn't modify membership."""
        group = self._make_group()
        manager.group_manager.get_group_members = AsyncMock(return_value=["user-extra"])

        user = make_entra_user(user_id="user-new", display_name="New User", email="new@sjifire.org")
        manager._user_by_email = {"new@sjifire.org": user}
        manager._user_by_upn = {}
        manager._entra_users = [
            make_entra_user(
                user_id="user-extra", display_name="Extra User", email="extra@sjifire.org"
            )
        ]

        member = Member(id="1", first_name="New", last_name="User", email="new@sjifire.org")

        added, removed, _errors = await manager._sync_group_membership(
            group=group,
            should_be_members=[member],
            dry_run=True,
        )

        assert added == ["New User"]
        assert removed == ["Extra User"]
        manager.group_manager.add_user_to_group.assert_not_called()
        manager.group_manager.remove_user_from_group.assert_not_called()


class TestGroupSyncManagerSync:
    """Tests for GroupSyncManager.sync method."""

    @pytest.fixture
    def manager(self):
        """Create a GroupSyncManager with mocked dependencies."""
        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            mgr = GroupSyncManager()
            mgr.group_manager = AsyncMock()
            mgr.user_manager = AsyncMock()
        return mgr

    def _make_group(self, group_id="g1", display_name="Test"):
        """Helper to create an EntraGroup."""
        return EntraGroup(
            id=group_id,
            display_name=display_name,
            description="Test",
            mail=f"{display_name.lower()}@sjifire.org",
            mail_enabled=True,
            security_enabled=False,
            group_types=["Unified"],
        )

    async def test_sync_returns_empty_result_when_no_groups(self, manager):
        """Test sync returns empty result when strategy produces no groups."""
        manager.user_manager.get_users = AsyncMock(return_value=[])

        strategy = SupportGroupStrategy()
        members = [Member(id="1", first_name="John", last_name="Doe", positions=["Firefighter"])]

        result = await manager.sync(strategy=strategy, members=members)

        assert result.group_type == "support"
        assert result.groups == []

    async def test_sync_processes_groups_from_strategy(self, manager):
        """Test sync processes groups returned by strategy."""
        # Set up user matching
        entra_user = make_entra_user(
            user_id="user-1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="john@sjifire.org",
            upn="john.doe@sjifire.org",
        )
        manager.user_manager.get_users = AsyncMock(return_value=[entra_user])

        # Set up group operations
        group = self._make_group(group_id="support-id", display_name="Support")
        manager.group_manager.get_group_by_mail_nickname = AsyncMock(return_value=group)
        manager.group_manager.get_group_members = AsyncMock(return_value=[])
        manager.group_manager.add_user_to_group = AsyncMock(return_value=True)
        manager.group_manager.update_group_visibility = AsyncMock(return_value=True)

        strategy = SupportGroupStrategy()
        members = [
            Member(
                id="1",
                first_name="John",
                last_name="Doe",
                email="john@sjifire.org",
                positions=["Support"],
            )
        ]

        result = await manager.sync(strategy=strategy, members=members)

        assert result.group_type == "support"
        assert len(result.groups) == 1
        assert result.groups[0].group_name == "Support"
        assert result.groups[0].members_added == ["John Doe"]

    async def test_sync_handles_group_creation_failure(self, manager):
        """Test sync handles group creation failure gracefully."""
        manager.user_manager.get_users = AsyncMock(return_value=[])

        # Group doesn't exist and creation fails
        manager.group_manager.get_group_by_mail_nickname = AsyncMock(return_value=None)
        manager.group_manager.get_group_by_name = AsyncMock(return_value=None)
        manager.group_manager.create_m365_group = AsyncMock(return_value=None)

        strategy = SupportGroupStrategy()
        members = [Member(id="1", first_name="John", last_name="Doe", positions=["Support"])]

        result = await manager.sync(strategy=strategy, members=members)

        assert len(result.groups) == 1
        assert result.groups[0].errors == ["Failed to get or create group: Support"]


class TestGroupSyncManagerBackup:
    """Tests for GroupSyncManager.backup_all_groups method."""

    @pytest.fixture
    def manager(self):
        """Create a GroupSyncManager with mocked dependencies."""
        with (
            patch("sjifire.entra.group_sync.EntraGroupManager"),
            patch("sjifire.entra.group_sync.EntraUserManager"),
        ):
            mgr = GroupSyncManager()
            mgr.group_manager = AsyncMock()
        return mgr

    def _make_group(self, group_id="g1", display_name="Test"):
        """Helper to create an EntraGroup."""
        return EntraGroup(
            id=group_id,
            display_name=display_name,
            description="Test",
            mail=f"{display_name.lower()}@sjifire.org",
            mail_enabled=True,
            security_enabled=False,
            group_types=["Unified"],
        )

    async def test_backup_all_groups_success(self, manager):
        """Test successful backup of all groups."""
        groups = [self._make_group("g1", "Group 1"), self._make_group("g2", "Group 2")]
        manager.group_manager.get_groups = AsyncMock(return_value=groups)
        manager.group_manager.get_group_members = AsyncMock(return_value=["user-1"])

        with patch("sjifire.entra.group_sync.backup_entra_groups") as mock_backup:
            mock_backup.return_value = "/path/to/backup.json"

            result = await manager.backup_all_groups()

            assert result == "/path/to/backup.json"
            mock_backup.assert_called_once()

    async def test_backup_all_groups_handles_error(self, manager):
        """Test backup handles errors gracefully."""
        manager.group_manager.get_groups = AsyncMock(side_effect=Exception("API error"))

        result = await manager.backup_all_groups()

        assert result is None
