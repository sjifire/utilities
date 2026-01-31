"""Tests for sjifire.ispyfire module."""

from sjifire.entra.users import EntraUser
from sjifire.ispyfire.models import ISpyFirePerson
from sjifire.ispyfire.sync import (
    compare_entra_to_ispyfire,
    entra_user_to_ispyfire_person,
    fields_need_update,
    get_user_positions,
    is_managed_email,
    is_operational,
    is_utility_account,
    normalize_email,
    normalize_name,
    normalize_phone,
)


class TestISpyFirePerson:
    """Tests for the ISpyFirePerson dataclass."""

    def test_display_name(self):
        person = ISpyFirePerson(id="123", first_name="John", last_name="Doe")
        assert person.display_name == "John Doe"

    def test_display_name_with_spaces(self):
        person = ISpyFirePerson(id="123", first_name="Mary Jane", last_name="Watson")
        assert person.display_name == "Mary Jane Watson"

    def test_from_api(self):
        data = {
            "_id": "abc123",
            "firstName": "John",
            "lastName": "Doe",
            "email": "jdoe@sjifire.org",
            "cellPhone": "555-1234",
            "title": "Captain",
            "isActive": True,
            "isLoginActive": True,
            "groupSetACLs": ["Admin"],
            "messageEmail": True,
            "messageCell": False,
        }
        person = ISpyFirePerson.from_api(data)

        assert person.id == "abc123"
        assert person.first_name == "John"
        assert person.last_name == "Doe"
        assert person.email == "jdoe@sjifire.org"
        assert person.cell_phone == "555-1234"
        assert person.title == "Captain"
        assert person.is_active is True
        assert person.is_login_active is True
        assert person.group_set_acls == ["Admin"]
        assert person.message_email is True
        assert person.message_cell is False

    def test_from_api_minimal(self):
        data = {"_id": "abc123", "firstName": "John", "lastName": "Doe"}
        person = ISpyFirePerson.from_api(data)

        assert person.id == "abc123"
        assert person.first_name == "John"
        assert person.last_name == "Doe"
        assert person.email is None
        assert person.cell_phone is None
        assert person.title is None
        assert person.is_active is True
        assert person.is_login_active is False
        assert person.group_set_acls == []

    def test_from_api_empty(self):
        data = {}
        person = ISpyFirePerson.from_api(data)

        assert person.id == ""
        assert person.first_name == ""
        assert person.last_name == ""

    def test_to_api(self):
        person = ISpyFirePerson(
            id="abc123",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            cell_phone="555-1234",
            title="Captain",
            is_active=True,
            message_email=True,
            message_cell=False,
        )
        api_data = person.to_api()

        assert api_data["firstName"] == "John"
        assert api_data["lastName"] == "Doe"
        assert api_data["email"] == "jdoe@sjifire.org"
        assert api_data["cellPhone"] == "555-1234"
        assert api_data["title"] == "Captain"
        assert api_data["isActive"] is True
        assert api_data["messageEmail"] is True
        assert api_data["messageCell"] is False
        # id should not be in the API payload
        assert "_id" not in api_data
        assert "id" not in api_data


class TestNormalizePhone:
    """Tests for normalize_phone function."""

    def test_strips_dashes(self):
        assert normalize_phone("555-123-4567") == "5551234567"

    def test_strips_spaces(self):
        assert normalize_phone("555 123 4567") == "5551234567"

    def test_strips_parentheses(self):
        assert normalize_phone("(555) 123-4567") == "5551234567"

    def test_keeps_digits_only(self):
        assert normalize_phone("+1 (555) 123-4567") == "15551234567"

    def test_none_input(self):
        assert normalize_phone(None) is None

    def test_empty_string(self):
        assert normalize_phone("") is None

    def test_no_digits(self):
        assert normalize_phone("abc") is None


class TestNormalizeEmail:
    """Tests for normalize_email function."""

    def test_lowercase(self):
        assert normalize_email("John.Doe@SJIFire.org") == "john.doe@sjifire.org"

    def test_strips_whitespace(self):
        assert normalize_email("  jdoe@sjifire.org  ") == "jdoe@sjifire.org"

    def test_none_input(self):
        assert normalize_email(None) is None

    def test_empty_string(self):
        assert normalize_email("") is None


class TestNormalizeName:
    """Tests for normalize_name function."""

    def test_basic(self):
        assert normalize_name("John", "Doe") == "john doe"

    def test_strips_whitespace(self):
        assert normalize_name("  John  ", "  Doe  ") == "john doe"

    def test_lowercase(self):
        assert normalize_name("JOHN", "DOE") == "john doe"

    def test_none_first_name(self):
        assert normalize_name(None, "Doe") == " doe"

    def test_none_last_name(self):
        assert normalize_name("John", None) == "john "

    def test_both_none(self):
        assert normalize_name(None, None) == " "


class TestIsManagedEmail:
    """Tests for is_managed_email function."""

    def test_sjifire_email(self):
        assert is_managed_email("jdoe@sjifire.org") is True

    def test_sjifire_email_uppercase(self):
        assert is_managed_email("JDOE@SJIFIRE.ORG") is True

    def test_different_domain(self):
        assert is_managed_email("jdoe@sanjuanems.org") is False

    def test_custom_domain(self):
        assert is_managed_email("jdoe@sanjuanems.org", "sanjuanems.org") is True

    def test_none_email(self):
        assert is_managed_email(None) is False

    def test_empty_email(self):
        assert is_managed_email("") is False

    def test_strips_whitespace(self):
        assert is_managed_email("  jdoe@sjifire.org  ") is True


class TestIsUtilityAccount:
    """Tests for is_utility_account function."""

    def test_svc_prefix(self):
        assert is_utility_account("svc-automation@sjifire.org") is True

    def test_svc_underscore_prefix(self):
        assert is_utility_account("svc_backup@sjifire.org") is True

    def test_service_prefix(self):
        assert is_utility_account("service-account@sjifire.org") is True

    def test_automation_prefix(self):
        assert is_utility_account("automation@sjifire.org") is True

    def test_bot_prefix(self):
        assert is_utility_account("bot-notify@sjifire.org") is True

    def test_regular_user(self):
        assert is_utility_account("jdoe@sjifire.org") is False

    def test_none_email(self):
        assert is_utility_account(None) is False

    def test_empty_email(self):
        assert is_utility_account("") is False

    def test_case_insensitive(self):
        assert is_utility_account("SVC-Automation@sjifire.org") is True


class TestGetUserPositions:
    """Tests for get_user_positions function."""

    def test_single_position(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3="Firefighter",
        )
        assert get_user_positions(user) == {"Firefighter"}

    def test_multiple_positions(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3="Firefighter, Apparatus Operator, EMT",
        )
        assert get_user_positions(user) == {"Firefighter", "Apparatus Operator", "EMT"}

    def test_strips_whitespace(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3="  Firefighter  ,  EMT  ",
        )
        assert get_user_positions(user) == {"Firefighter", "EMT"}

    def test_empty_attribute(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3="",
        )
        assert get_user_positions(user) == set()

    def test_none_attribute(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3=None,
        )
        assert get_user_positions(user) == set()


class TestIsOperational:
    """Tests for is_operational function."""

    def test_operational_position(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3="Firefighter",
        )
        assert is_operational(user) is True

    def test_apparatus_operator_position(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3="Apparatus Operator",
        )
        assert is_operational(user) is True

    def test_non_operational_position(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3="Commissioner",
        )
        assert is_operational(user) is False

    def test_utility_employee_type_excluded(self):
        user = EntraUser(
            id="1",
            display_name="Svc Automation",
            first_name="Svc",
            last_name="Automation",
            email="svc-automation@sjifire.org",
            upn="svc-automation@sjifire.org",
            employee_id="1",
            employee_type="Utility",
            extension_attribute3="Firefighter",
        )
        assert is_operational(user) is False

    def test_no_positions(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute3=None,
        )
        assert is_operational(user) is False


class TestFieldsNeedUpdate:
    """Tests for fields_need_update function."""

    def test_no_differences(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            mobile_phone="5551234567",
            extension_attribute1="Captain",
        )
        person = ISpyFirePerson(
            id="abc",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            cell_phone="555-123-4567",
            title="Captain",
        )
        assert fields_need_update(user, person) == []

    def test_first_name_differs(self):
        user = EntraUser(
            id="1",
            display_name="Johnny Doe",
            first_name="Johnny",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
        )
        person = ISpyFirePerson(
            id="abc",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
        )
        assert fields_need_update(user, person) == ["firstName"]

    def test_last_name_differs(self):
        user = EntraUser(
            id="1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
        )
        person = ISpyFirePerson(
            id="abc",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
        )
        assert fields_need_update(user, person) == ["lastName"]

    def test_phone_differs(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            mobile_phone="555-999-8888",
        )
        person = ISpyFirePerson(
            id="abc",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            cell_phone="555-123-4567",
        )
        assert fields_need_update(user, person) == ["cellPhone"]

    def test_title_differs(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            extension_attribute1="Lieutenant",
        )
        person = ISpyFirePerson(
            id="abc",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            title="Captain",
        )
        assert fields_need_update(user, person) == ["title"]

    def test_multiple_differences(self):
        user = EntraUser(
            id="1",
            display_name="Johnny Smith",
            first_name="Johnny",
            last_name="Smith",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            mobile_phone="555-999-8888",
            extension_attribute1="Lieutenant",
        )
        person = ISpyFirePerson(
            id="abc",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            cell_phone="555-123-4567",
            title="Captain",
        )
        diff = fields_need_update(user, person)
        assert "firstName" in diff
        assert "lastName" in diff
        assert "cellPhone" in diff
        assert "title" in diff


class TestEntraUserToISpyFirePerson:
    """Tests for entra_user_to_ispyfire_person function."""

    def test_converts_all_fields(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            mobile_phone="555-123-4567",
            extension_attribute1="Captain",
        )
        person = entra_user_to_ispyfire_person(user)

        assert person.id == ""  # New person, no ID yet
        assert person.first_name == "John"
        assert person.last_name == "Doe"
        assert person.email == "jdoe@sjifire.org"
        assert person.cell_phone == "555-123-4567"
        assert person.title == "Captain"
        assert person.is_active is True
        assert person.message_email is True
        assert person.message_cell is True

    def test_handles_none_fields(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name=None,
            last_name=None,
            email=None,
            upn="jdoe@sjifire.org",
            employee_id="1",
        )
        person = entra_user_to_ispyfire_person(user)

        assert person.first_name == ""
        assert person.last_name == ""
        assert person.email is None
        assert person.cell_phone is None
        assert person.title is None


class TestCompareEntraToISpyFire:
    """Tests for compare_entra_to_ispyfire function."""

    def _make_entra_user(
        self,
        user_id: str,
        first: str,
        last: str,
        email: str,
        positions: str = "Firefighter",
        phone: str = "555-1234",
        rank: str | None = None,
    ) -> EntraUser:
        """Helper to create an EntraUser."""
        return EntraUser(
            id=user_id,
            display_name=f"{first} {last}",
            first_name=first,
            last_name=last,
            email=email,
            upn=email,
            employee_id=user_id,
            mobile_phone=phone,
            extension_attribute1=rank,
            extension_attribute3=positions,
        )

    def _make_ispy_person(
        self,
        person_id: str,
        first: str,
        last: str,
        email: str,
        phone: str = "555-1234",
        title: str | None = None,
        is_active: bool = True,
    ) -> ISpyFirePerson:
        """Helper to create an ISpyFirePerson."""
        return ISpyFirePerson(
            id=person_id,
            first_name=first,
            last_name=last,
            email=email,
            cell_phone=phone,
            title=title,
            is_active=is_active,
        )

    def test_matched_users(self):
        entra_users = [
            self._make_entra_user("1", "John", "Doe", "jdoe@sjifire.org"),
        ]
        ispy_people = [
            self._make_ispy_person("abc", "John", "Doe", "jdoe@sjifire.org"),
        ]

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.matched) == 1
        assert len(result.to_add) == 0
        assert len(result.to_update) == 0
        assert len(result.to_remove) == 0

    def test_user_to_add(self):
        entra_users = [
            self._make_entra_user("1", "John", "Doe", "jdoe@sjifire.org"),
        ]
        ispy_people = []

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.matched) == 0
        assert len(result.to_add) == 1
        assert result.to_add[0].email == "jdoe@sjifire.org"
        assert len(result.to_update) == 0
        assert len(result.to_remove) == 0

    def test_user_to_remove(self):
        entra_users = []
        ispy_people = [
            self._make_ispy_person("abc", "John", "Doe", "jdoe@sjifire.org"),
        ]

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.matched) == 0
        assert len(result.to_add) == 0
        assert len(result.to_update) == 0
        assert len(result.to_remove) == 1
        assert result.to_remove[0].email == "jdoe@sjifire.org"

    def test_user_to_update(self):
        entra_users = [
            self._make_entra_user(
                "1", "John", "Doe", "jdoe@sjifire.org", phone="555-9999"
            ),
        ]
        ispy_people = [
            self._make_ispy_person("abc", "John", "Doe", "jdoe@sjifire.org"),
        ]

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.matched) == 0
        assert len(result.to_add) == 0
        assert len(result.to_update) == 1
        assert len(result.to_remove) == 0

    def test_non_managed_domain_ignored_for_add(self):
        entra_users = [
            self._make_entra_user("1", "John", "Doe", "jdoe@sanjuanems.org"),
        ]
        ispy_people = []

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.to_add) == 0
        assert len(result.entra_operational) == 0

    def test_non_managed_domain_not_removed(self):
        entra_users = []
        ispy_people = [
            self._make_ispy_person("abc", "John", "Doe", "jdoe@sanjuanems.org"),
        ]

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.to_remove) == 0

    def test_utility_account_not_removed(self):
        entra_users = []
        ispy_people = [
            self._make_ispy_person(
                "abc", "Svc", "Automation", "svc-automation@sjifire.org"
            ),
        ]

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.to_remove) == 0

    def test_inactive_person_not_removed(self):
        entra_users = []
        ispy_people = [
            self._make_ispy_person(
                "abc", "John", "Doe", "jdoe@sjifire.org", is_active=False
            ),
        ]

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.to_remove) == 0

    def test_user_without_phone_not_added(self):
        entra_users = [
            self._make_entra_user("1", "John", "Doe", "jdoe@sjifire.org", phone=None),
        ]
        ispy_people = []

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.to_add) == 0

    def test_duplicate_by_name_not_added(self):
        """User exists with different email - don't add duplicate."""
        entra_users = [
            self._make_entra_user("1", "John", "Doe", "jdoe@sjifire.org"),
        ]
        ispy_people = [
            self._make_ispy_person("abc", "John", "Doe", "johndoe@sanjuanems.org"),
        ]

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.to_add) == 0
        # Person with sanjuanems email won't be removed either
        assert len(result.to_remove) == 0

    def test_non_operational_user_ignored(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
            upn="jdoe@sjifire.org",
            employee_id="1",
            mobile_phone="555-1234",
            extension_attribute3="Commissioner",  # Not operational
        )
        entra_users = [user]
        ispy_people = []

        result = compare_entra_to_ispyfire(entra_users, ispy_people)

        assert len(result.entra_operational) == 0
        assert len(result.to_add) == 0
