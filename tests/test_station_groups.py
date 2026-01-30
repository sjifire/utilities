"""Tests for group sync management."""

from sjifire.aladtec.models import MARINE_POSITIONS, OPERATIONAL_POSITIONS, Member
from sjifire.entra.group_sync import (
    FullSyncResult,
    GroupSyncResult,
    MarineGroupStrategy,
    StationGroupStrategy,
    SupportGroupStrategy,
    VolunteerGroupStrategy,
)


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
        assert "Mate" in notice or "Pilot" in notice

    def test_get_group_config(self):
        display_name, mail_nickname, description = self.strategy.get_group_config("Marine")
        assert display_name == "Marine"
        assert mail_nickname == "marine"
        assert "Mate" in description or "Pilot" in description

    def test_get_groups_to_sync_empty(self):
        assert self.strategy.get_groups_to_sync([]) == {}

    def test_get_groups_to_sync_mate_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Mate"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_groups_to_sync_pilot_position(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Pilot"],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_groups_to_sync_both_positions(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Mate", "Pilot"],
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
            Member(id="1", first_name="John", last_name="Doe", positions=["Mate"]),
            Member(id="2", first_name="Jane", last_name="Smith", positions=["Pilot"]),
            Member(id="3", first_name="Bob", last_name="Wilson", positions=["Firefighter"]),
        ]
        result = self.strategy.get_groups_to_sync(members)
        assert len(result) == 1
        assert len(result["Marine"]) == 2

    def test_marine_positions_include_all_expected(self):
        """Verify all expected marine positions are configured."""
        expected = {"Mate", "Pilot"}
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
            "Mate",
            "Pilot",
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
        from unittest.mock import AsyncMock

        from sjifire.entra.group_sync import GroupSyncManager
        from sjifire.entra.groups import EntraGroup

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
        from unittest.mock import AsyncMock

        from sjifire.entra.group_sync import GroupSyncManager
        from sjifire.entra.groups import EntraGroup

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
        from unittest.mock import AsyncMock

        from sjifire.entra.group_sync import GroupSyncManager
        from sjifire.entra.groups import EntraGroup

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