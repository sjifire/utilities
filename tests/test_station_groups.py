"""Tests for group sync management."""

from sjifire.aladtec.models import Member
from sjifire.entra.group_sync import (
    FullSyncResult,
    GroupSyncResult,
    PositionGroupStrategy,
    StationGroupStrategy,
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


class TestPositionGroupStrategy:
    """Tests for PositionGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = PositionGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "positions"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "Positions" in notice

    def test_get_group_config_support(self):
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

    def test_get_groups_to_sync_ignores_unmapped_positions(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Firefighter", "EMT"],  # Not in POSITION_GROUPS
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
