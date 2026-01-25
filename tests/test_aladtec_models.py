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
            position="Firefighter",
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
        assert member.position == "Firefighter"
        assert member.positions == ["Firefighter", "EMT"]
        assert member.title == "Captain"
        assert member.status == "Active"
        assert member.work_group == "A Shift"
        assert member.pay_profile == "Career"
        assert member.employee_id == "EMP001"
        assert member.station_assignment == "Station 1"
        assert member.evip == "Yes"
        assert member.date_hired == "2020-01-15"
