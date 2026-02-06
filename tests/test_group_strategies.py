"""Tests for core/group_strategies.py - backend-agnostic group membership strategies."""

import pytest

from sjifire.aladtec.models import Member
from sjifire.core.group_strategies import (
    STRATEGY_CLASSES,
    STRATEGY_NAMES,
    AllPersonnelStrategy,
    ApparatusOperatorStrategy,
    FirefighterStrategy,
    GroupConfig,
    GroupMember,
    GroupStrategy,
    MarineStrategy,
    MobeScheduleStrategy,
    StaffStrategy,
    StationStrategy,
    SupportStrategy,
    VolunteerStrategy,
    WildlandFirefighterStrategy,
    get_strategy,
)
from sjifire.entra.users import EntraUser

# =============================================================================
# Test Fixtures
# =============================================================================


def make_member(
    member_id: str = "1",
    first_name: str = "John",
    last_name: str = "Doe",
    email: str | None = "john.doe@sjifire.org",
    positions: list[str] | None = None,
    schedules: list[str] | None = None,
    station_assignment: str | None = None,
    work_group: str | None = None,
    evip: str | None = None,
) -> Member:
    """Helper to create test members with defaults."""
    return Member(
        id=member_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        positions=positions or [],
        schedules=schedules or [],
        station_assignment=station_assignment,
        work_group=work_group,
        evip=evip,
    )


def make_entra_user(
    user_id: str = "user-id-1",
    display_name: str = "John Doe",
    email: str | None = "john.doe@sjifire.org",
    positions: str | None = None,  # Comma-delimited string for extension_attribute3
    schedules: str | None = None,  # Comma-delimited string for extension_attribute4
    office_location: str | None = None,  # "Station XX" format
    employee_type: str | None = None,  # work_group
    evip: str | None = None,  # extension_attribute2
    account_enabled: bool = True,
    employee_id: str | None = "EMP001",
) -> EntraUser:
    """Helper to create test EntraUser objects with defaults."""
    return EntraUser(
        id=user_id,
        display_name=display_name,
        first_name=display_name.split()[0] if display_name else None,
        last_name=display_name.split()[-1] if display_name else None,
        email=email,
        upn=email,
        employee_id=employee_id,
        account_enabled=account_enabled,
        office_location=office_location,
        employee_type=employee_type,
        extension_attribute2=evip,
        extension_attribute3=positions,
        extension_attribute4=schedules,
    )


# =============================================================================
# Test Strategy Registry
# =============================================================================


class TestStrategyRegistry:
    """Tests for strategy registration and lookup."""

    def test_strategy_classes_contains_all_strategies(self):
        """All strategies should be registered."""
        expected = {
            "stations",
            "support",
            "ff",
            "wff",
            "ao",
            "marine",
            "volunteers",
            "staff",
            "mobe",
            "all-personnel",
        }
        assert set(STRATEGY_CLASSES.keys()) == expected

    def test_strategy_names_matches_classes(self):
        """STRATEGY_NAMES should match STRATEGY_CLASSES keys."""
        assert set(STRATEGY_NAMES) == set(STRATEGY_CLASSES.keys())

    def test_get_strategy_returns_instance(self):
        """get_strategy should return instantiated strategy."""
        strategy = get_strategy("ff")
        assert isinstance(strategy, FirefighterStrategy)
        assert isinstance(strategy, GroupStrategy)

    def test_get_strategy_all_names(self):
        """get_strategy should work for all registered names."""
        for name in STRATEGY_NAMES:
            strategy = get_strategy(name)
            assert isinstance(strategy, GroupStrategy)
            assert strategy.name == name

    def test_get_strategy_unknown_raises(self):
        """get_strategy should raise KeyError for unknown strategy."""
        with pytest.raises(KeyError) as exc_info:
            get_strategy("unknown")
        assert "Unknown strategy: unknown" in str(exc_info.value)
        assert "Available:" in str(exc_info.value)


# =============================================================================
# Test GroupConfig
# =============================================================================


class TestGroupConfig:
    """Tests for GroupConfig dataclass."""

    def test_group_config_all_fields(self):
        """GroupConfig should store all fields."""
        config = GroupConfig(
            display_name="Test Group",
            mail_nickname="testgroup",
            description="A test group",
        )
        assert config.display_name == "Test Group"
        assert config.mail_nickname == "testgroup"
        assert config.description == "A test group"

    def test_group_config_optional_description(self):
        """GroupConfig description should be optional."""
        config = GroupConfig(
            display_name="Test Group",
            mail_nickname="testgroup",
        )
        assert config.description is None

    def test_group_config_enforce_calendar_visibility_default_false(self):
        """enforce_calendar_visibility should default to False."""
        config = GroupConfig(
            display_name="Test Group",
            mail_nickname="testgroup",
        )
        assert config.enforce_calendar_visibility is False

    def test_group_config_enforce_calendar_visibility_can_be_set(self):
        """enforce_calendar_visibility can be set to True."""
        config = GroupConfig(
            display_name="Test Group",
            mail_nickname="testgroup",
            enforce_calendar_visibility=True,
        )
        assert config.enforce_calendar_visibility is True


# =============================================================================
# Test StationStrategy
# =============================================================================


class TestStationStrategy:
    """Tests for StationStrategy - groups members by station assignment."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = StationStrategy()

    def test_name(self):
        """Strategy name should be 'stations'."""
        assert self.strategy.name == "stations"

    def test_membership_criteria(self):
        """Membership criteria should describe station assignment."""
        assert "Station Assignment" in self.strategy.membership_criteria

    def test_automation_notice_includes_criteria(self):
        """Automation notice should include membership criteria."""
        notice = self.strategy.automation_notice
        assert "automatically managed" in notice.lower()
        assert self.strategy.membership_criteria in notice

    def test_get_members_empty_list(self):
        """Empty member list should return empty dict."""
        result = self.strategy.get_members([])
        assert result == {}

    def test_get_members_no_station_assignments(self):
        """Members without station assignments should not be grouped."""
        members = [
            make_member(member_id="1", station_assignment=None),
            make_member(member_id="2", station_assignment=""),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_numeric_station(self):
        """Numeric station assignment should be parsed correctly."""
        members = [make_member(member_id="1", station_assignment="31")]
        result = self.strategy.get_members(members)
        assert "31" in result
        assert len(result["31"]) == 1
        assert result["31"][0].id == "1"

    def test_get_members_station_prefix(self):
        """'Station XX' format should be parsed correctly."""
        members = [make_member(member_id="1", station_assignment="Station 31")]
        result = self.strategy.get_members(members)
        assert "31" in result
        assert len(result["31"]) == 1

    def test_get_members_station_prefix_lowercase(self):
        """'station XX' (lowercase) should be parsed correctly."""
        members = [make_member(member_id="1", station_assignment="station 32")]
        result = self.strategy.get_members(members)
        assert "32" in result

    def test_get_members_multiple_stations(self):
        """Members should be grouped by their station."""
        members = [
            make_member(member_id="1", station_assignment="31"),
            make_member(member_id="2", station_assignment="32"),
            make_member(member_id="3", station_assignment="31"),
        ]
        result = self.strategy.get_members(members)
        assert len(result) == 2
        assert len(result["31"]) == 2
        assert len(result["32"]) == 1

    def test_get_members_invalid_station_ignored(self):
        """Non-numeric station values should be ignored."""
        members = [
            make_member(member_id="1", station_assignment="Headquarters"),
            make_member(member_id="2", station_assignment="N/A"),
            make_member(member_id="3", station_assignment="31"),
        ]
        result = self.strategy.get_members(members)
        assert len(result) == 1
        assert "31" in result

    def test_get_members_whitespace_handling(self):
        """Whitespace in station assignment should be handled."""
        members = [make_member(member_id="1", station_assignment="  31  ")]
        result = self.strategy.get_members(members)
        assert "31" in result

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("31")
        assert config.display_name == "Station 31"
        assert config.mail_nickname == "station31"
        assert "Station 31" in config.description


# =============================================================================
# Test SupportStrategy
# =============================================================================


class TestSupportStrategy:
    """Tests for SupportStrategy - members with Support position."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = SupportStrategy()

    def test_name(self):
        """Strategy name should be 'support'."""
        assert self.strategy.name == "support"

    def test_membership_criteria(self):
        """Membership criteria should describe Support position."""
        assert "Support" in self.strategy.membership_criteria

    def test_get_members_empty_list(self):
        """Empty member list should return empty dict."""
        result = self.strategy.get_members([])
        assert result == {}

    def test_get_members_no_support(self):
        """Members without Support position should not be included."""
        members = [
            make_member(member_id="1", positions=["Firefighter"]),
            make_member(member_id="2", positions=["EMT"]),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_with_support(self):
        """Members with Support position should be included."""
        members = [
            make_member(member_id="1", positions=["Support"]),
            make_member(member_id="2", positions=["Firefighter"]),
        ]
        result = self.strategy.get_members(members)
        assert "Support" in result
        assert len(result["Support"]) == 1
        assert result["Support"][0].id == "1"

    def test_get_members_support_with_other_positions(self):
        """Members with Support plus other positions should be included."""
        members = [make_member(member_id="1", positions=["Firefighter", "Support", "EMT"])]
        result = self.strategy.get_members(members)
        assert len(result["Support"]) == 1

    def test_get_members_null_positions(self):
        """Members with None positions should be handled."""
        member = make_member(member_id="1")
        member.positions = None
        result = self.strategy.get_members([member])
        assert result == {}

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("Support")
        assert config.display_name == "Support"
        assert config.mail_nickname == "support"


# =============================================================================
# Test FirefighterStrategy
# =============================================================================


class TestFirefighterStrategy:
    """Tests for FirefighterStrategy - members with Firefighter position."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = FirefighterStrategy()

    def test_name(self):
        """Strategy name should be 'ff'."""
        assert self.strategy.name == "ff"

    def test_membership_criteria(self):
        """Membership criteria should describe Firefighter position."""
        assert "Firefighter" in self.strategy.membership_criteria

    def test_get_members_empty_list(self):
        """Empty member list should return empty dict."""
        result = self.strategy.get_members([])
        assert result == {}

    def test_get_members_no_firefighters(self):
        """Members without Firefighter position should not be included."""
        members = [
            make_member(member_id="1", positions=["Support"]),
            make_member(member_id="2", positions=["EMT"]),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_with_firefighter(self):
        """Members with Firefighter position should be included."""
        members = [
            make_member(member_id="1", positions=["Firefighter"]),
            make_member(member_id="2", positions=["Support"]),
        ]
        result = self.strategy.get_members(members)
        assert "FF" in result
        assert len(result["FF"]) == 1
        assert result["FF"][0].id == "1"

    def test_get_members_multiple_firefighters(self):
        """All firefighters should be included."""
        members = [
            make_member(member_id="1", positions=["Firefighter"]),
            make_member(member_id="2", positions=["Firefighter", "EMT"]),
            make_member(member_id="3", positions=["Support"]),
        ]
        result = self.strategy.get_members(members)
        assert len(result["FF"]) == 2

    def test_get_members_wildland_not_included(self):
        """Wildland Firefighter should NOT match Firefighter strategy."""
        members = [make_member(member_id="1", positions=["Wildland Firefighter"])]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("FF")
        assert config.display_name == "Firefighters"
        assert config.mail_nickname == "firefighters"


# =============================================================================
# Test WildlandFirefighterStrategy
# =============================================================================


class TestWildlandFirefighterStrategy:
    """Tests for WildlandFirefighterStrategy."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = WildlandFirefighterStrategy()

    def test_name(self):
        """Strategy name should be 'wff'."""
        assert self.strategy.name == "wff"

    def test_membership_criteria(self):
        """Membership criteria should describe Wildland Firefighter position."""
        assert "Wildland Firefighter" in self.strategy.membership_criteria

    def test_get_members_empty_list(self):
        """Empty member list should return empty dict."""
        result = self.strategy.get_members([])
        assert result == {}

    def test_get_members_no_wildland(self):
        """Members without Wildland Firefighter should not be included."""
        members = [
            make_member(member_id="1", positions=["Firefighter"]),
            make_member(member_id="2", positions=["Support"]),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_with_wildland(self):
        """Members with Wildland Firefighter position should be included."""
        members = [
            make_member(member_id="1", positions=["Wildland Firefighter"]),
            make_member(member_id="2", positions=["Firefighter"]),
        ]
        result = self.strategy.get_members(members)
        assert "WFF" in result
        assert len(result["WFF"]) == 1
        assert result["WFF"][0].id == "1"

    def test_get_members_both_ff_and_wff(self):
        """Members with both positions should appear in WFF group."""
        members = [make_member(member_id="1", positions=["Firefighter", "Wildland Firefighter"])]
        result = self.strategy.get_members(members)
        assert "WFF" in result
        assert len(result["WFF"]) == 1

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("WFF")
        assert config.display_name == "Wildland Firefighters"
        assert config.mail_nickname == "wildlandffs"


# =============================================================================
# Test ApparatusOperatorStrategy
# =============================================================================


class TestApparatusOperatorStrategy:
    """Tests for ApparatusOperatorStrategy - members with EVIP certification."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = ApparatusOperatorStrategy()

    def test_name(self):
        """Strategy name should be 'ao'."""
        assert self.strategy.name == "ao"

    def test_membership_criteria(self):
        """Membership criteria should describe EVIP certification."""
        assert "EVIP" in self.strategy.membership_criteria

    def test_get_members_empty_list(self):
        """Empty member list should return empty dict."""
        result = self.strategy.get_members([])
        assert result == {}

    def test_get_members_no_evip(self):
        """Members without EVIP should not be included."""
        members = [
            make_member(member_id="1", evip=None),
            make_member(member_id="2", evip=""),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_with_evip(self):
        """Members with EVIP certification should be included."""
        members = [
            make_member(member_id="1", evip="2025-12-31"),
            make_member(member_id="2", evip=None),
        ]
        result = self.strategy.get_members(members)
        assert "Apparatus Operator" in result
        assert len(result["Apparatus Operator"]) == 1
        assert result["Apparatus Operator"][0].id == "1"

    def test_get_members_any_evip_value(self):
        """Any non-empty EVIP value should qualify."""
        members = [
            make_member(member_id="1", evip="Yes"),
            make_member(member_id="2", evip="2024-01-01"),
            make_member(member_id="3", evip="Certified"),
        ]
        result = self.strategy.get_members(members)
        assert len(result["Apparatus Operator"]) == 3

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("Apparatus Operator")
        assert config.display_name == "Apparatus Operators"
        assert config.mail_nickname == "apparatus-operators"
        assert "EVIP" in config.description


# =============================================================================
# Test MarineStrategy
# =============================================================================


class TestMarineStrategy:
    """Tests for MarineStrategy - members with marine positions."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = MarineStrategy()

    def test_name(self):
        """Strategy name should be 'marine'."""
        assert self.strategy.name == "marine"

    def test_membership_criteria(self):
        """Membership criteria should describe marine positions."""
        criteria = self.strategy.membership_criteria
        assert "Marine" in criteria or "Mate" in criteria or "Pilot" in criteria

    def test_get_members_empty_list(self):
        """Empty member list should return empty dict."""
        result = self.strategy.get_members([])
        assert result == {}

    def test_get_members_no_marine(self):
        """Members without marine positions should not be included."""
        members = [
            make_member(member_id="1", positions=["Firefighter"]),
            make_member(member_id="2", positions=["Support"]),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_with_deckhand(self):
        """Members with Marine: Deckhand should be included."""
        members = [make_member(member_id="1", positions=["Marine: Deckhand"])]
        result = self.strategy.get_members(members)
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_members_with_mate(self):
        """Members with Marine: Mate should be included."""
        members = [make_member(member_id="1", positions=["Marine: Mate"])]
        result = self.strategy.get_members(members)
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_members_with_pilot(self):
        """Members with Marine: Pilot should be included."""
        members = [make_member(member_id="1", positions=["Marine: Pilot"])]
        result = self.strategy.get_members(members)
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_members_multiple_marine_positions(self):
        """Members with multiple marine positions should appear once."""
        members = [make_member(member_id="1", positions=["Marine: Deckhand", "Marine: Mate"])]
        result = self.strategy.get_members(members)
        assert len(result["Marine"]) == 1

    def test_get_members_marine_plus_other(self):
        """Members with marine plus other positions should be included."""
        members = [make_member(member_id="1", positions=["Firefighter", "Marine: Pilot"])]
        result = self.strategy.get_members(members)
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("Marine")
        assert config.display_name == "Marine"
        assert config.mail_nickname == "marine"


# =============================================================================
# Test VolunteerStrategy
# =============================================================================


class TestVolunteerStrategy:
    """Tests for VolunteerStrategy - volunteers with operational positions."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = VolunteerStrategy()

    def test_name(self):
        """Strategy name should be 'volunteers'."""
        assert self.strategy.name == "volunteers"

    def test_membership_criteria(self):
        """Membership criteria should describe volunteer + operational."""
        criteria = self.strategy.membership_criteria
        assert "Volunteer" in criteria
        assert "operational" in criteria.lower()

    def test_get_members_empty_list(self):
        """Empty member list should return empty dict."""
        result = self.strategy.get_members([])
        assert result == {}

    def test_get_members_not_volunteer_work_group(self):
        """Members not in Volunteer work group should not be included."""
        members = [
            make_member(member_id="1", work_group="Career", positions=["Firefighter"]),
            make_member(member_id="2", work_group="Admin", positions=["Firefighter"]),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_volunteer_no_operational(self):
        """Volunteers without operational positions should not be included."""
        members = [
            make_member(member_id="1", work_group="Volunteer", positions=["Admin"]),
            make_member(member_id="2", work_group="Volunteer", positions=[]),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_volunteer_with_firefighter(self):
        """Volunteer with Firefighter position should be included."""
        members = [make_member(member_id="1", work_group="Volunteer", positions=["Firefighter"])]
        result = self.strategy.get_members(members)
        assert "Volunteers" in result
        assert len(result["Volunteers"]) == 1

    def test_get_members_volunteer_with_support(self):
        """Volunteer with Support position should be included."""
        members = [make_member(member_id="1", work_group="Volunteer", positions=["Support"])]
        result = self.strategy.get_members(members)
        assert "Volunteers" in result
        assert len(result["Volunteers"]) == 1

    def test_get_members_volunteer_with_apparatus_operator(self):
        """Volunteer with Apparatus Operator position should be included."""
        members = [
            make_member(member_id="1", work_group="Volunteer", positions=["Apparatus Operator"])
        ]
        result = self.strategy.get_members(members)
        assert "Volunteers" in result

    def test_get_members_volunteer_with_wildland(self):
        """Volunteer with Wildland Firefighter position should be included."""
        members = [
            make_member(member_id="1", work_group="Volunteer", positions=["Wildland Firefighter"])
        ]
        result = self.strategy.get_members(members)
        assert "Volunteers" in result

    def test_get_members_volunteer_with_marine(self):
        """Volunteer with Marine position should be included."""
        members = [
            make_member(member_id="1", work_group="Volunteer", positions=["Marine: Deckhand"])
        ]
        result = self.strategy.get_members(members)
        assert "Volunteers" in result

    def test_get_members_multiple_volunteers(self):
        """Multiple qualifying volunteers should all be included."""
        members = [
            make_member(member_id="1", work_group="Volunteer", positions=["Firefighter"]),
            make_member(member_id="2", work_group="Volunteer", positions=["Support"]),
            make_member(member_id="3", work_group="Career", positions=["Firefighter"]),
        ]
        result = self.strategy.get_members(members)
        assert len(result["Volunteers"]) == 2

    def test_get_members_null_work_group(self):
        """Members with null work_group should not be included."""
        members = [make_member(member_id="1", work_group=None, positions=["Firefighter"])]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_null_positions(self):
        """Volunteers with null positions should not be included."""
        member = make_member(member_id="1", work_group="Volunteer")
        member.positions = None
        result = self.strategy.get_members([member])
        assert result == {}

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("Volunteers")
        assert config.display_name == "Volunteers"
        assert config.mail_nickname == "volunteers"


# =============================================================================
# Test StaffStrategy
# =============================================================================


class TestStaffStrategy:
    """Tests for StaffStrategy - non-volunteer members."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = StaffStrategy()

    def test_name(self):
        """Strategy name should be 'staff'."""
        assert self.strategy.name == "staff"

    def test_membership_criteria(self):
        """Membership criteria should describe non-volunteer."""
        criteria = self.strategy.membership_criteria
        assert "Volunteer" in criteria

    def test_get_members_empty_list(self):
        """Empty member list should return empty dict."""
        result = self.strategy.get_members([])
        assert result == {}

    def test_get_members_volunteer_work_group_excluded(self):
        """Members in Volunteer work group should not be included."""
        members = [
            make_member(member_id="1", work_group="Volunteer", positions=["Firefighter"]),
            make_member(member_id="2", work_group="Volunteer", positions=["Support"]),
        ]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_members_career(self):
        """Career members should be included."""
        members = [make_member(member_id="1", work_group="Career")]
        result = self.strategy.get_members(members)
        assert "Staff" in result
        assert len(result["Staff"]) == 1

    def test_get_members_any_non_volunteer(self):
        """Any non-volunteer work group should be included."""
        members = [
            make_member(member_id="1", work_group="Career"),
            make_member(member_id="2", work_group="Staff"),
            make_member(member_id="3", work_group="Admin"),
        ]
        result = self.strategy.get_members(members)
        assert "Staff" in result
        assert len(result["Staff"]) == 3

    def test_get_members_mixed(self):
        """Only non-volunteers should be included from mixed list."""
        members = [
            make_member(member_id="1", work_group="Career"),
            make_member(member_id="2", work_group="Volunteer"),
            make_member(member_id="3", work_group="Staff"),
        ]
        result = self.strategy.get_members(members)
        assert len(result["Staff"]) == 2

    def test_get_members_null_work_group(self):
        """Members with null work_group should not be included."""
        members = [make_member(member_id="1", work_group=None)]
        result = self.strategy.get_members(members)
        assert result == {}

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("Staff")
        assert config.display_name == "Staff"
        assert config.mail_nickname == "staff"


# =============================================================================
# Test MobeScheduleStrategy
# =============================================================================


class TestMobeScheduleStrategy:
    """Tests for MobeScheduleStrategy - members with State Mobe schedule access."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = MobeScheduleStrategy()

    def test_name(self):
        """Strategy name should be 'mobe'."""
        assert self.strategy.name == "mobe"

    def test_membership_criteria(self):
        """Membership criteria should describe State Mobe schedule."""
        criteria = self.strategy.membership_criteria
        assert "Mobe" in criteria or "mobe" in criteria.lower()

    def test_get_members_empty_list(self):
        """Empty member list should return group with empty list."""
        result = self.strategy.get_members([])
        # MobeScheduleStrategy always returns the group key even if empty
        assert "mobe" in result
        assert result["mobe"] == []

    def test_get_members_no_mobe_schedule(self):
        """Members without mobe schedule should not be in the list."""
        members = [
            make_member(member_id="1", schedules=["Daily", "Weekly"]),
            make_member(member_id="2", schedules=["Administration"]),
        ]
        result = self.strategy.get_members(members)
        assert "mobe" in result
        assert len(result["mobe"]) == 0

    def test_get_members_with_state_mobe(self):
        """Members with 'State Mobe' schedule should be included."""
        members = [
            make_member(member_id="1", schedules=["State Mobe"]),
            make_member(member_id="2", schedules=["Daily"]),
        ]
        result = self.strategy.get_members(members)
        assert len(result["mobe"]) == 1
        assert result["mobe"][0].id == "1"

    def test_get_members_case_insensitive(self):
        """Mobe matching should be case-insensitive."""
        members = [
            make_member(member_id="1", schedules=["state mobe"]),
            make_member(member_id="2", schedules=["STATE MOBE"]),
            make_member(member_id="3", schedules=["State Mobe"]),
        ]
        result = self.strategy.get_members(members)
        assert len(result["mobe"]) == 3

    def test_get_members_mobe_substring(self):
        """Any schedule containing 'mobe' should match."""
        members = [
            make_member(member_id="1", schedules=["State Mobe Schedule"]),
            make_member(member_id="2", schedules=["Mobe Team"]),
            make_member(member_id="3", schedules=["Mobilization"]),  # Does NOT contain 'mobe'
        ]
        result = self.strategy.get_members(members)
        # Only first two contain 'mobe' as substring ('Mobilization' has 'mobi' not 'mobe')
        assert len(result["mobe"]) == 2

    def test_get_members_mobe_with_other_schedules(self):
        """Members with mobe plus other schedules should be included."""
        members = [make_member(member_id="1", schedules=["Daily", "State Mobe", "Admin"])]
        result = self.strategy.get_members(members)
        assert len(result["mobe"]) == 1

    def test_get_members_null_schedules(self):
        """Members with null schedules should be handled gracefully."""
        member = make_member(member_id="1")
        member.schedules = None
        result = self.strategy.get_members([member])
        assert "mobe" in result
        assert len(result["mobe"]) == 0

    def test_get_members_empty_schedules(self):
        """Members with empty schedules list should not match."""
        members = [make_member(member_id="1", schedules=[])]
        result = self.strategy.get_members(members)
        assert len(result["mobe"]) == 0

    def test_always_returns_group_key(self):
        """Strategy should always return group key even with no members."""
        # This ensures the group is created/maintained even if empty
        result = self.strategy.get_members([])
        assert "mobe" in result

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("mobe")
        assert config.display_name == "State Mobilization"
        assert config.mail_nickname == "statemobe"
        assert "mobilization" in config.description.lower()


class TestAllPersonnelStrategy:
    """Tests for AllPersonnelStrategy - all personnel with operational positions."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = AllPersonnelStrategy()

    def test_name(self):
        """Strategy name should be 'all-personnel'."""
        assert self.strategy.name == "all-personnel"

    def test_membership_criteria_test_mode(self):
        """In test mode, criteria should mention test users."""
        criteria = self.strategy.membership_criteria
        assert "Test mode" in criteria or "agreene@sjifire.org" in criteria

    def test_get_members_test_mode_only_specific_emails(self):
        """In test mode, only specific test emails should be included."""
        members = [
            make_member(member_id="1", email="agreene@sjifire.org", positions=["Firefighter"]),
            make_member(member_id="2", email="other@sjifire.org", positions=["Firefighter"]),
        ]
        result = self.strategy.get_members(members)
        assert "all-personnel" in result
        assert len(result["all-personnel"]) == 1
        assert result["all-personnel"][0].email == "agreene@sjifire.org"

    def test_get_members_test_mode_case_insensitive(self):
        """Test mode email matching should be case-insensitive."""
        members = [
            make_member(member_id="1", email="AGreene@sjifire.org", positions=["Firefighter"]),
        ]
        result = self.strategy.get_members(members)
        assert len(result["all-personnel"]) == 1

    def test_get_members_test_mode_no_match(self):
        """In test mode, non-test users should not be included."""
        members = [
            make_member(member_id="1", email="other@sjifire.org", positions=["Firefighter"]),
            make_member(member_id="2", email="another@sjifire.org", positions=["Support"]),
        ]
        result = self.strategy.get_members(members)
        assert "all-personnel" in result
        assert len(result["all-personnel"]) == 0

    def test_get_config(self):
        """get_config should return proper GroupConfig."""
        config = self.strategy.get_config("all-personnel")
        assert config.display_name == "All Personnel"
        assert config.mail_nickname == "all-personnel"
        assert "calendar" in config.description.lower() or "email" in config.description.lower()

    def test_get_config_enforce_calendar_visibility(self):
        """get_config should set enforce_calendar_visibility=True for M365 calendar visibility."""
        config = self.strategy.get_config("all-personnel")
        assert config.enforce_calendar_visibility is True


# =============================================================================
# AllPersonnelStrategy Internal Methods Tests
# =============================================================================


class TestAllPersonnelStrategyInternalMethods:
    """Tests for AllPersonnelStrategy internal helper methods."""

    def setup_method(self):
        """Create strategy instance for each test."""
        self.strategy = AllPersonnelStrategy()

    # _is_active tests

    def test_is_active_returns_true_for_active_entra_user(self):
        """_is_active returns True for active EntraUser."""
        user = make_entra_user(account_enabled=True)
        assert self.strategy._is_active(user) is True

    def test_is_active_returns_false_for_disabled_entra_user(self):
        """_is_active returns False for disabled EntraUser."""
        user = make_entra_user(
            display_name="Disabled User",
            email="disabled@sjifire.org",
            account_enabled=False,
        )
        assert self.strategy._is_active(user) is False

    def test_is_active_returns_true_for_member_without_is_active(self):
        """_is_active defaults to True for objects without is_active property."""
        member = make_member()  # Aladtec Member doesn't have is_active
        assert self.strategy._is_active(member) is True

    def test_is_active_returns_true_when_attribute_missing(self):
        """_is_active defaults to True when attribute is missing."""

        class MockMember:
            email = "test@sjifire.org"
            display_name = "Test"
            positions = ["Firefighter"]  # noqa: RUF012
            schedules = []  # noqa: RUF012
            evip = None
            work_group = None
            station_number = None

        mock = MockMember()
        assert self.strategy._is_active(mock) is True

    # _is_employee tests

    def test_is_employee_returns_true_for_employee_entra_user(self):
        """_is_employee returns True for EntraUser with employee_id."""
        user = make_entra_user(employee_id="EMP001")
        assert self.strategy._is_employee(user) is True

    def test_is_employee_returns_false_for_non_employee_entra_user(self):
        """_is_employee returns False for EntraUser without employee_id."""
        user = EntraUser(
            id="user-1",
            display_name="Guest User",
            email="guest@sjifire.org",
            upn="guest@sjifire.org",
            first_name="Guest",
            last_name="User",
            employee_id=None,
            account_enabled=True,
        )
        assert self.strategy._is_employee(user) is False

    def test_is_employee_returns_true_for_member_without_is_employee(self):
        """_is_employee defaults to True for objects without is_employee property."""
        member = make_member()  # Aladtec Member doesn't have is_employee
        assert self.strategy._is_employee(member) is True

    # _has_operational_position tests

    def test_has_operational_position_firefighter(self):
        """_has_operational_position returns True for Firefighter."""
        member = make_member(positions=["Firefighter"])
        assert self.strategy._has_operational_position(member) is True

    def test_has_operational_position_apparatus_operator(self):
        """_has_operational_position returns True for Apparatus Operator."""
        member = make_member(positions=["Apparatus Operator"])
        assert self.strategy._has_operational_position(member) is True

    def test_has_operational_position_support(self):
        """_has_operational_position returns True for Support."""
        member = make_member(positions=["Support"])
        assert self.strategy._has_operational_position(member) is True

    def test_has_operational_position_wildland_firefighter(self):
        """_has_operational_position returns True for Wildland Firefighter."""
        member = make_member(positions=["Wildland Firefighter"])
        assert self.strategy._has_operational_position(member) is True

    def test_has_operational_position_marine_pilot(self):
        """_has_operational_position returns True for Marine: Pilot."""
        member = make_member(positions=["Marine: Pilot"])
        assert self.strategy._has_operational_position(member) is True

    def test_has_operational_position_marine_mate(self):
        """_has_operational_position returns True for Marine: Mate."""
        member = make_member(positions=["Marine: Mate"])
        assert self.strategy._has_operational_position(member) is True

    def test_has_operational_position_deckhand(self):
        """_has_operational_position returns True for Marine: Deckhand."""
        member = make_member(positions=["Marine: Deckhand"])
        assert self.strategy._has_operational_position(member) is True

    def test_has_operational_position_returns_false_for_non_operational(self):
        """_has_operational_position returns False for non-operational positions."""
        member = make_member(positions=["Administrative"])
        assert self.strategy._has_operational_position(member) is False

    def test_has_operational_position_returns_false_for_empty_positions(self):
        """_has_operational_position returns False for empty positions."""
        member = make_member(positions=[])
        assert self.strategy._has_operational_position(member) is False

    def test_has_operational_position_returns_false_for_none_positions(self):
        """_has_operational_position returns False for None positions."""
        member = make_member(positions=None)
        assert self.strategy._has_operational_position(member) is False

    def test_has_operational_position_multiple_with_one_operational(self):
        """_has_operational_position returns True if any position is operational."""
        member = make_member(positions=["Administrative", "Firefighter"])
        assert self.strategy._has_operational_position(member) is True

    def test_has_operational_position_works_with_entra_user(self):
        """_has_operational_position works with EntraUser objects."""
        user = make_entra_user(positions="Firefighter,Support")
        assert self.strategy._has_operational_position(user) is True

    def test_has_operational_position_entra_user_non_operational(self):
        """_has_operational_position returns False for EntraUser with non-operational."""
        user = make_entra_user(positions="Administrative")
        assert self.strategy._has_operational_position(user) is False


# =============================================================================
# Test GroupMember Protocol and EntraUser Compatibility
# =============================================================================


class TestGroupMemberProtocol:
    """Tests for GroupMember protocol implementation."""

    def test_member_implements_protocol(self):
        """Member should implement GroupMember protocol."""
        member = make_member(station_assignment="31")
        assert isinstance(member, GroupMember)

    def test_entra_user_implements_protocol(self):
        """EntraUser should implement GroupMember protocol."""
        user = make_entra_user(office_location="Station 31")
        assert isinstance(user, GroupMember)

    def test_member_station_number_plain(self):
        """Member.station_number should parse plain number."""
        member = make_member(station_assignment="31")
        assert member.station_number == "31"

    def test_member_station_number_prefixed(self):
        """Member.station_number should parse 'Station XX' format."""
        member = make_member(station_assignment="Station 32")
        assert member.station_number == "32"

    def test_member_station_number_none(self):
        """Member.station_number should return None when not set."""
        member = make_member(station_assignment=None)
        assert member.station_number is None

    def test_entra_user_station_number_plain(self):
        """EntraUser.station_number should parse plain number."""
        user = make_entra_user(office_location="31")
        assert user.station_number == "31"

    def test_entra_user_station_number_prefixed(self):
        """EntraUser.station_number should parse 'Station XX' format."""
        user = make_entra_user(office_location="Station 32")
        assert user.station_number == "32"

    def test_entra_user_station_number_none(self):
        """EntraUser.station_number should return None when not set."""
        user = make_entra_user(office_location=None)
        assert user.station_number is None

    def test_entra_user_work_group(self):
        """EntraUser.work_group should return employee_type."""
        user = make_entra_user(employee_type="Volunteer")
        assert user.work_group == "Volunteer"

    def test_entra_user_positions_as_set(self):
        """EntraUser.positions should parse comma-delimited string to set."""
        user = make_entra_user(positions="Firefighter, Support, EMT")
        assert user.positions == {"Firefighter", "Support", "EMT"}

    def test_entra_user_schedules_as_set(self):
        """EntraUser.schedules should parse comma-delimited string to set."""
        user = make_entra_user(schedules="State Mobe, Daily")
        assert user.schedules == {"State Mobe", "Daily"}


# =============================================================================
# Test Strategies with EntraUser Objects
# =============================================================================


class TestStrategiesWithEntraUser:
    """Tests verifying strategies work with EntraUser objects."""

    def test_station_strategy_with_entra_user(self):
        """StationStrategy should work with EntraUser."""
        strategy = StationStrategy()
        user = make_entra_user(office_location="Station 31")
        result = strategy.get_members([user])
        assert "31" in result
        assert len(result["31"]) == 1
        assert result["31"][0].email == "john.doe@sjifire.org"

    def test_firefighter_strategy_with_entra_user(self):
        """FirefighterStrategy should work with EntraUser."""
        strategy = FirefighterStrategy()
        user = make_entra_user(positions="Firefighter, EMT")
        result = strategy.get_members([user])
        assert "FF" in result
        assert len(result["FF"]) == 1

    def test_support_strategy_with_entra_user(self):
        """SupportStrategy should work with EntraUser."""
        strategy = SupportStrategy()
        user = make_entra_user(positions="Support")
        result = strategy.get_members([user])
        assert "Support" in result
        assert len(result["Support"]) == 1

    def test_wildland_strategy_with_entra_user(self):
        """WildlandFirefighterStrategy should work with EntraUser."""
        strategy = WildlandFirefighterStrategy()
        user = make_entra_user(positions="Wildland Firefighter")
        result = strategy.get_members([user])
        assert "WFF" in result
        assert len(result["WFF"]) == 1

    def test_apparatus_operator_strategy_with_entra_user(self):
        """ApparatusOperatorStrategy should work with EntraUser."""
        strategy = ApparatusOperatorStrategy()
        user = make_entra_user(evip="2025-12-31")
        result = strategy.get_members([user])
        assert "Apparatus Operator" in result
        assert len(result["Apparatus Operator"]) == 1

    def test_marine_strategy_with_entra_user(self):
        """MarineStrategy should work with EntraUser."""
        strategy = MarineStrategy()
        user = make_entra_user(positions="Marine: Pilot, Firefighter")
        result = strategy.get_members([user])
        assert "Marine" in result
        assert len(result["Marine"]) == 1

    def test_volunteer_strategy_with_entra_user(self):
        """VolunteerStrategy should work with EntraUser."""
        strategy = VolunteerStrategy()
        user = make_entra_user(employee_type="Volunteer", positions="Firefighter")
        result = strategy.get_members([user])
        assert "Volunteers" in result
        assert len(result["Volunteers"]) == 1

    def test_staff_strategy_with_entra_user(self):
        """StaffStrategy should work with EntraUser."""
        strategy = StaffStrategy()
        user = make_entra_user(employee_type="Career")
        result = strategy.get_members([user])
        assert "Staff" in result
        assert len(result["Staff"]) == 1

    def test_mobe_strategy_with_entra_user(self):
        """MobeScheduleStrategy should work with EntraUser."""
        strategy = MobeScheduleStrategy()
        user = make_entra_user(schedules="State Mobe, Daily")
        result = strategy.get_members([user])
        assert "mobe" in result
        assert len(result["mobe"]) == 1

    def test_mixed_member_types(self):
        """Strategies should work with mixed Member and EntraUser lists."""
        strategy = FirefighterStrategy()
        member = make_member(positions=["Firefighter"])
        user = make_entra_user(positions="Firefighter")
        result = strategy.get_members([member, user])
        assert "FF" in result
        assert len(result["FF"]) == 2


# =============================================================================
# Test Base Class Contract
# =============================================================================


class TestGroupStrategyContract:
    """Tests ensuring all strategies follow the base class contract."""

    @pytest.mark.parametrize("strategy_name", STRATEGY_NAMES)
    def test_strategy_has_name(self, strategy_name):
        """All strategies should have a name property."""
        strategy = get_strategy(strategy_name)
        assert isinstance(strategy.name, str)
        assert len(strategy.name) > 0

    @pytest.mark.parametrize("strategy_name", STRATEGY_NAMES)
    def test_strategy_has_membership_criteria(self, strategy_name):
        """All strategies should have membership_criteria property."""
        strategy = get_strategy(strategy_name)
        assert isinstance(strategy.membership_criteria, str)
        assert len(strategy.membership_criteria) > 0

    @pytest.mark.parametrize("strategy_name", STRATEGY_NAMES)
    def test_strategy_has_automation_notice(self, strategy_name):
        """All strategies should have automation_notice property."""
        strategy = get_strategy(strategy_name)
        assert isinstance(strategy.automation_notice, str)
        assert "automatically managed" in strategy.automation_notice.lower()

    @pytest.mark.parametrize("strategy_name", STRATEGY_NAMES)
    def test_strategy_get_members_returns_dict(self, strategy_name):
        """get_members should return a dict."""
        strategy = get_strategy(strategy_name)
        result = strategy.get_members([])
        assert isinstance(result, dict)

    @pytest.mark.parametrize("strategy_name", STRATEGY_NAMES)
    def test_strategy_get_members_values_are_lists(self, strategy_name):
        """get_members dict values should be lists of GroupMember objects."""
        strategy = get_strategy(strategy_name)
        # Create a member that might match any strategy
        member = make_member(
            member_id="1",
            positions=["Firefighter", "Support", "Wildland Firefighter", "Marine: Pilot"],
            schedules=["State Mobe"],
            station_assignment="31",
            work_group="Volunteer",
            evip="2025-12-31",
        )
        result = strategy.get_members([member])
        for value in result.values():
            assert isinstance(value, list)
            for item in value:
                assert isinstance(item, GroupMember)

    @pytest.mark.parametrize("strategy_name", STRATEGY_NAMES)
    def test_strategy_works_with_entra_user(self, strategy_name):
        """All strategies should work with EntraUser objects."""
        strategy = get_strategy(strategy_name)
        # Create an EntraUser that might match any strategy
        user = make_entra_user(
            positions="Firefighter, Support, Wildland Firefighter, Marine: Pilot",
            schedules="State Mobe",
            office_location="Station 31",
            employee_type="Volunteer",
            evip="2025-12-31",
        )
        result = strategy.get_members([user])
        assert isinstance(result, dict)
        for value in result.values():
            assert isinstance(value, list)
            for item in value:
                assert isinstance(item, GroupMember)
