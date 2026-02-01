"""Tests for mail-enabled security group sync."""

from sjifire.aladtec.models import Member
from sjifire.exchange.group_sync import (
    MailAllPersonnelGroupStrategy,
    MailApparatusOperatorGroupStrategy,
    MailFirefighterGroupStrategy,
    MailFullSyncResult,
    MailGroupSyncResult,
    MailMarineGroupStrategy,
    MailStationGroupStrategy,
    MailSupportGroupStrategy,
    MailVolunteerGroupStrategy,
    MailWildlandFirefighterGroupStrategy,
)


class TestMailStationGroupStrategy:
    """Tests for MailStationGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MailStationGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "mail-stations"

    def test_automation_notice(self):
        notice = self.strategy.automation_notice
        assert "automatically" in notice.lower()
        assert "Aladtec" in notice

    def test_get_group_config(self):
        display_name, alias, description = self.strategy.get_group_config("31")
        assert display_name == "Station 31"
        assert alias == "station31"
        assert description == "Members assigned to Station 31"

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


class TestMailSupportGroupStrategy:
    """Tests for MailSupportGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MailSupportGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "mail-support"

    def test_get_group_config(self):
        display_name, alias, _description = self.strategy.get_group_config("Support")
        assert display_name == "Support"
        assert alias == "support"

    def test_get_groups_to_sync_support_position(self):
        member = Member(id="1", first_name="John", last_name="Doe", positions=["Support"])
        result = self.strategy.get_groups_to_sync([member])
        assert "Support" in result
        assert len(result["Support"]) == 1

    def test_get_groups_to_sync_ignores_non_support(self):
        member = Member(id="1", first_name="John", last_name="Doe", positions=["Firefighter"])
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}


class TestMailFirefighterGroupStrategy:
    """Tests for MailFirefighterGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MailFirefighterGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "mail-ff"

    def test_get_groups_to_sync_firefighter_position(self):
        member = Member(id="1", first_name="John", last_name="Doe", positions=["Firefighter"])
        result = self.strategy.get_groups_to_sync([member])
        assert "FF" in result
        assert len(result["FF"]) == 1


class TestMailWildlandFirefighterGroupStrategy:
    """Tests for MailWildlandFirefighterGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MailWildlandFirefighterGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "mail-wff"

    def test_get_groups_to_sync_wff_position(self):
        member = Member(
            id="1", first_name="John", last_name="Doe", positions=["Wildland Firefighter"]
        )
        result = self.strategy.get_groups_to_sync([member])
        assert "WFF" in result
        assert len(result["WFF"]) == 1


class TestMailApparatusOperatorGroupStrategy:
    """Tests for MailApparatusOperatorGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MailApparatusOperatorGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "mail-ao"

    def test_get_groups_to_sync_with_evip(self):
        member = Member(id="1", first_name="John", last_name="Doe", evip="2026-06-30")
        result = self.strategy.get_groups_to_sync([member])
        assert "Apparatus Operator" in result
        assert len(result["Apparatus Operator"]) == 1

    def test_get_groups_to_sync_without_evip(self):
        member = Member(id="1", first_name="John", last_name="Doe", evip=None)
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}


class TestMailMarineGroupStrategy:
    """Tests for MailMarineGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MailMarineGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "mail-marine"

    def test_get_groups_to_sync_mate_position(self):
        # Debug: print values to diagnose CI failure
        from sjifire.core import constants
        import sjifire.exchange.group_sync as group_sync_module

        print(f"\nDEBUG: MARINE_POSITIONS from constants: {constants.MARINE_POSITIONS}")
        print(f"DEBUG: 'Mate' in MARINE_POSITIONS: {'Mate' in constants.MARINE_POSITIONS}")
        # Check what the module sees
        marine_pos_in_module = getattr(group_sync_module, "MARINE_POSITIONS", "NOT FOUND")
        print(f"DEBUG: MARINE_POSITIONS in group_sync module: {marine_pos_in_module}")

        member = Member(id="1", first_name="John", last_name="Doe", positions=["Mate"])
        print(f"DEBUG: member.positions = {member.positions}")
        result = self.strategy.get_groups_to_sync([member])
        print(f"DEBUG: result = {result}")
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_groups_to_sync_pilot_position(self):
        member = Member(id="1", first_name="John", last_name="Doe", positions=["Pilot"])
        result = self.strategy.get_groups_to_sync([member])
        assert "Marine" in result


class TestMailVolunteerGroupStrategy:
    """Tests for MailVolunteerGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MailVolunteerGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "mail-volunteers"

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
            positions=[],
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}


class TestMailAllPersonnelGroupStrategy:
    """Tests for MailAllPersonnelGroupStrategy."""

    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = MailAllPersonnelGroupStrategy()

    def test_name(self):
        assert self.strategy.name == "mail-allpersonnel"

    def test_get_group_config(self):
        display_name, alias, _description = self.strategy.get_group_config("All Personnel")
        assert display_name == "All Personnel"
        assert alias == "allpersonnel"

    def test_get_groups_to_sync_with_sjifire_email(self):
        member = Member(id="1", first_name="John", last_name="Doe", email="john.doe@sjifire.org")
        result = self.strategy.get_groups_to_sync([member])
        assert "All Personnel" in result
        assert len(result["All Personnel"]) == 1

    def test_get_groups_to_sync_excludes_non_sjifire_email(self):
        member = Member(id="1", first_name="John", last_name="Doe", email="john.doe@gmail.com")
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}

    def test_get_groups_to_sync_excludes_test_accounts(self):
        member = Member(
            id="1", first_name="Test", last_name="Admin", email="test.admin@sjifire.org"
        )
        result = self.strategy.get_groups_to_sync([member])
        assert result == {}


class TestMailGroupSyncResult:
    """Tests for MailGroupSyncResult dataclass."""

    def test_has_changes_when_created(self):
        result = MailGroupSyncResult(
            group_name="Test", group_email="test@sjifire.org", created=True
        )
        assert result.has_changes is True

    def test_has_changes_when_members_added(self):
        result = MailGroupSyncResult(
            group_name="Test",
            group_email="test@sjifire.org",
            created=False,
            members_added=["User1"],
        )
        assert result.has_changes is True

    def test_has_changes_when_members_removed(self):
        result = MailGroupSyncResult(
            group_name="Test",
            group_email="test@sjifire.org",
            created=False,
            members_removed=["User1"],
        )
        assert result.has_changes is True

    def test_no_changes(self):
        result = MailGroupSyncResult(
            group_name="Test", group_email="test@sjifire.org", created=False
        )
        assert result.has_changes is False


class TestMailFullSyncResult:
    """Tests for MailFullSyncResult dataclass."""

    def test_totals_empty(self):
        result = MailFullSyncResult(group_type="test")
        assert result.total_created == 0
        assert result.total_added == 0
        assert result.total_removed == 0
        assert result.total_errors == 0

    def test_totals_with_groups(self):
        result = MailFullSyncResult(
            group_type="test",
            groups=[
                MailGroupSyncResult(
                    group_name="Group1",
                    group_email="group1@sjifire.org",
                    created=True,
                    members_added=["A", "B"],
                    members_removed=["C"],
                    errors=["Error1"],
                ),
                MailGroupSyncResult(
                    group_name="Group2",
                    group_email="group2@sjifire.org",
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
