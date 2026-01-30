"""Tests for sjifire.aladtec.models."""

from sjifire.aladtec.models import Member


class TestMember:
    """Tests for the Member dataclass."""

    def test_display_name(self):
        member = Member(id="1", first_name="John", last_name="Doe")
        assert member.display_name == "John Doe"

    def test_display_name_with_spaces(self):
        member = Member(id="1", first_name="Mary Jane", last_name="Watson")
        assert member.display_name == "Mary Jane Watson"

    def test_is_active_when_status_none(self):
        member = Member(id="1", first_name="John", last_name="Doe", status=None)
        assert member.is_active is True

    def test_is_active_when_status_active(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="Active")
        assert member.is_active is True

    def test_is_active_when_status_active_lowercase(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="active")
        assert member.is_active is True

    def test_is_active_when_status_inactive(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="Inactive")
        assert member.is_active is False

    def test_is_active_when_status_other(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="On Leave")
        assert member.is_active is False

    def test_user_principal_name_with_email(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john.doe@sjifire.org",
        )
        assert member.user_principal_name == "john.doe@sjifire.org"

    def test_user_principal_name_without_email(self):
        member = Member(id="1", first_name="John", last_name="Doe")
        assert member.user_principal_name is None

    def test_default_positions_empty_list(self):
        member = Member(id="1", first_name="John", last_name="Doe")
        assert member.positions == []

    def test_positions_list(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            positions=["Firefighter", "EMT"],
        )
        assert member.positions == ["Firefighter", "EMT"]

    def test_all_fields(self):
        member = Member(
            id="EMP001",
            first_name="John",
            last_name="Doe",
            email="john.doe@sjifire.org",
            phone="555-1234",
            home_phone="555-5678",
            employee_type="Firefighter",
            positions=["Firefighter", "EMT"],
            title="Captain",
            status="Active",
            work_group="A Shift",
            pay_profile="Career",
            employee_id="EMP001",
            station_assignment="Station 1",
            evip="Yes",
            date_hired="2020-01-15",
        )
        assert member.id == "EMP001"
        assert member.first_name == "John"
        assert member.last_name == "Doe"
        assert member.email == "john.doe@sjifire.org"
        assert member.phone == "555-1234"
        assert member.home_phone == "555-5678"
        assert member.employee_type == "Firefighter"
        assert member.positions == ["Firefighter", "EMT"]
        assert member.title == "Captain"
        assert member.status == "Active"
        assert member.work_group == "A Shift"
        assert member.pay_profile == "Career"
        assert member.employee_id == "EMP001"
        assert member.station_assignment == "Station 1"
        assert member.evip == "Yes"
        assert member.date_hired == "2020-01-15"


class TestMemberRank:
    """Tests for the rank property."""

    def test_rank_from_position_captain(self):
        member = Member(id="1", first_name="Kyle", last_name="Dodd", employee_type="Captain")
        assert member.rank == "Captain"

    def test_rank_from_position_lieutenant(self):
        member = Member(id="1", first_name="Tom", last_name="Eades", employee_type="Lieutenant")
        assert member.rank == "Lieutenant"

    def test_rank_from_position_chief(self):
        member = Member(id="1", first_name="Mike", last_name="Hartzell", employee_type="Chief")
        assert member.rank == "Chief"

    def test_rank_from_title_when_position_not_rank(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            employee_type="Firefighter",
            title="Captain",
        )
        assert member.rank == "Captain"

    def test_title_takes_precedence_over_position(self):
        """Title field is more specific, so it takes precedence over position."""
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            employee_type="Captain",
            title="Lieutenant",
        )
        assert member.rank == "Lieutenant"

    def test_no_rank_when_not_in_whitelist(self):
        member = Member(
            id="1",
            first_name="John",
            last_name="Doe",
            employee_type="Firefighter",
            title="EMT",
        )
        assert member.rank is None

    def test_no_rank_when_fields_empty(self):
        member = Member(id="1", first_name="John", last_name="Doe")
        assert member.rank is None

    def test_rank_case_insensitive(self):
        member = Member(id="1", first_name="John", last_name="Doe", employee_type="CAPTAIN")
        assert member.rank == "Captain"

    def test_division_chief_rank(self):
        member = Member(id="1", first_name="John", last_name="Doe", employee_type="Division Chief")
        assert member.rank == "Division Chief"

    def test_battalion_chief_rank(self):
        member = Member(id="1", first_name="John", last_name="Doe", employee_type="Battalion Chief")
        assert member.rank == "Battalion Chief"


class TestMemberDisplayRank:
    """Tests for the display_rank property."""

    def test_display_rank_shortens_battalion_chief(self):
        member = Member(id="1", first_name="Mike", last_name="H", title="Battalion Chief")
        assert member.rank == "Battalion Chief"
        assert member.display_rank == "Chief"

    def test_display_rank_shortens_division_chief(self):
        member = Member(id="1", first_name="John", last_name="Doe", title="Division Chief")
        assert member.rank == "Division Chief"
        assert member.display_rank == "Chief"

    def test_display_rank_keeps_chief(self):
        member = Member(id="1", first_name="John", last_name="Doe", title="Chief")
        assert member.rank == "Chief"
        assert member.display_rank == "Chief"

    def test_display_rank_keeps_captain(self):
        member = Member(id="1", first_name="Kyle", last_name="Dodd", title="Captain")
        assert member.rank == "Captain"
        assert member.display_rank == "Captain"

    def test_display_rank_keeps_lieutenant(self):
        member = Member(id="1", first_name="John", last_name="Doe", title="Lieutenant")
        assert member.rank == "Lieutenant"
        assert member.display_rank == "Lieutenant"

    def test_display_rank_none_when_no_rank(self):
        member = Member(id="1", first_name="John", last_name="Doe")
        assert member.display_rank is None


class TestMemberJobTitle:
    """Tests for the job_title property."""

    def test_job_title_non_rank(self):
        member = Member(id="1", first_name="Tad", last_name="Lean", title="Maintenance Officer")
        assert member.job_title == "Maintenance Officer"

    def test_job_title_none_when_title_is_rank(self):
        member = Member(id="1", first_name="Kyle", last_name="Dodd", title="Captain")
        assert member.job_title is None

    def test_job_title_none_when_empty(self):
        member = Member(id="1", first_name="John", last_name="Doe")
        assert member.job_title is None

    def test_job_title_with_rank_in_position(self):
        # Title is not a rank, but position is - job_title should return title
        member = Member(
            id="1",
            first_name="Tad",
            last_name="Lean",
            employee_type="Captain",
            title="Maintenance Officer",
        )
        assert member.job_title == "Maintenance Officer"

    def test_job_title_case_insensitive_rank_check(self):
        member = Member(id="1", first_name="John", last_name="Doe", title="LIEUTENANT")
        assert member.job_title is None


class TestMemberOfficeLocation:
    """Tests for the office_location property."""

    def test_station_prefix_added_for_number(self):
        member = Member(id="1", first_name="John", last_name="Doe", station_assignment="31")
        assert member.office_location == "Station 31"

    def test_station_prefix_not_duplicated(self):
        member = Member(id="1", first_name="John", last_name="Doe", station_assignment="Station 31")
        assert member.office_location == "Station 31"

    def test_station_prefix_case_insensitive(self):
        member = Member(id="1", first_name="John", last_name="Doe", station_assignment="station 31")
        assert member.office_location == "station 31"

    def test_none_when_empty(self):
        member = Member(id="1", first_name="John", last_name="Doe")
        assert member.office_location is None

    def test_non_numeric_returned_as_is(self):
        member = Member(
            id="1", first_name="John", last_name="Doe", station_assignment="Headquarters"
        )
        assert member.office_location == "Headquarters"

    def test_whitespace_trimmed(self):
        member = Member(id="1", first_name="John", last_name="Doe", station_assignment="  33  ")
        assert member.office_location == "Station 33"
