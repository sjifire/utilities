"""Tests for sjifire.ispyfire.client module."""

from unittest.mock import patch

import httpx
import pytest
import respx

from sjifire.ispyfire.client import ISpyFireClient
from sjifire.ispyfire.models import ISpyFirePerson


@pytest.fixture
def mock_credentials():
    """Mock the credentials function."""
    with patch("sjifire.ispyfire.client.get_ispyfire_credentials") as mock:
        mock.return_value = ("https://test.ispyfire.com", "testuser", "testpass")
        yield mock


@pytest.fixture
def sample_person_data():
    """Sample person data from API."""
    return {
        "_id": "abc123",
        "firstName": "John",
        "lastName": "Doe",
        "email": "jdoe@sjifire.org",
        "cellPhone": "555-1234",
        "title": "Captain",
        "isActive": True,
        "isLoginActive": True,
        "isUtility": False,
        "groupSetACLs": [],
        "messageEmail": True,
        "messageCell": True,
    }


class TestISpyFireClientInit:
    """Tests for client initialization."""

    def test_init_loads_credentials(self, mock_credentials):
        client = ISpyFireClient()
        assert client.base_url == "https://test.ispyfire.com"
        assert client.username == "testuser"
        assert client.password == "testpass"
        assert client.client is None


class TestISpyFireClientContextManager:
    """Tests for context manager behavior."""

    @respx.mock
    def test_enter_creates_client_and_logs_in(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))

        with ISpyFireClient() as client:
            assert client.client is not None

    @respx.mock
    def test_exit_closes_client(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))

        client = ISpyFireClient()
        with client:
            assert client.client is not None
        assert client.client is None


class TestISpyFireClientLogin:
    """Tests for login functionality."""

    @respx.mock
    def test_login_success(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))

        with ISpyFireClient() as client:
            # Login happens in __enter__, so if we get here it worked
            assert client.client is not None

    @respx.mock
    def test_login_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(401))

        with ISpyFireClient() as client:
            # Login returns False but doesn't raise - client still created
            assert client.client is not None


class TestGetPeople:
    """Tests for get_people method."""

    @respx.mock
    def test_get_people_success(self, mock_credentials, sample_person_data):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/ddui/people").mock(
            return_value=httpx.Response(200, json={"results": [sample_person_data]})
        )

        with ISpyFireClient() as client:
            people = client.get_people()

        assert len(people) == 1
        assert people[0].id == "abc123"
        assert people[0].first_name == "John"
        assert people[0].last_name == "Doe"

    @respx.mock
    def test_get_people_with_include_inactive(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/ddui/people?includeInactive=true").mock(
            return_value=httpx.Response(200, json={"results": []})
        )

        with ISpyFireClient() as client:
            people = client.get_people(include_inactive=True)

        assert people == []

    @respx.mock
    def test_get_people_with_include_deleted(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/ddui/people?includeDeleted=true").mock(
            return_value=httpx.Response(200, json={"results": []})
        )

        with ISpyFireClient() as client:
            people = client.get_people(include_deleted=True)

        assert people == []

    @respx.mock
    def test_get_people_with_both_flags(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get(
            "https://test.ispyfire.com/api/ddui/people?includeInactive=true&includeDeleted=true"
        ).mock(return_value=httpx.Response(200, json={"results": []}))

        with ISpyFireClient() as client:
            people = client.get_people(include_inactive=True, include_deleted=True)

        assert people == []

    @respx.mock
    def test_get_people_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/ddui/people").mock(
            return_value=httpx.Response(500)
        )

        with ISpyFireClient() as client:
            people = client.get_people()

        assert people == []

    def test_get_people_without_context_manager(self, mock_credentials):
        client = ISpyFireClient()
        with pytest.raises(RuntimeError, match="context manager"):
            client.get_people()


class TestGetPersonByEmail:
    """Tests for get_person_by_email method."""

    @respx.mock
    def test_get_person_by_email_found(self, mock_credentials, sample_person_data):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/ddui/people/email/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200, json={"results": [sample_person_data]})
        )

        with ISpyFireClient() as client:
            person = client.get_person_by_email("jdoe@sjifire.org")

        assert person is not None
        assert person.email == "jdoe@sjifire.org"

    @respx.mock
    def test_get_person_by_email_not_found(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/ddui/people/email/nobody@sjifire.org").mock(
            return_value=httpx.Response(404)
        )

        with ISpyFireClient() as client:
            person = client.get_person_by_email("nobody@sjifire.org")

        assert person is None

    @respx.mock
    def test_get_person_by_email_empty_results(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/ddui/people/email/nobody@sjifire.org").mock(
            return_value=httpx.Response(200, json={"results": []})
        )

        with ISpyFireClient() as client:
            person = client.get_person_by_email("nobody@sjifire.org")

        assert person is None


class TestCreatePerson:
    """Tests for create_person method."""

    @respx.mock
    def test_create_person_success(self, mock_credentials, sample_person_data):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people").mock(
            return_value=httpx.Response(201, json={"results": [sample_person_data]})
        )

        person = ISpyFirePerson(
            id="",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
        )

        with ISpyFireClient() as client:
            result = client.create_person(person)

        assert result is not None
        assert result.id == "abc123"

    @respx.mock
    def test_create_person_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people").mock(
            return_value=httpx.Response(400)
        )

        person = ISpyFirePerson(id="", first_name="John", last_name="Doe")

        with ISpyFireClient() as client:
            result = client.create_person(person)

        assert result is None


class TestUpdatePerson:
    """Tests for update_person method."""

    @respx.mock
    def test_update_person_success(self, mock_credentials, sample_person_data):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people/abc123").mock(
            return_value=httpx.Response(200, json={"results": [sample_person_data]})
        )

        person = ISpyFirePerson(
            id="abc123",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
        )

        with ISpyFireClient() as client:
            result = client.update_person(person)

        assert result is not None
        assert result.id == "abc123"

    @respx.mock
    def test_update_person_without_id(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))

        person = ISpyFirePerson(id="", first_name="John", last_name="Doe")

        with ISpyFireClient() as client:
            result = client.update_person(person)

        assert result is None

    @respx.mock
    def test_update_person_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people/abc123").mock(
            return_value=httpx.Response(500)
        )

        person = ISpyFirePerson(id="abc123", first_name="John", last_name="Doe")

        with ISpyFireClient() as client:
            result = client.update_person(person)

        assert result is None


class TestLogoutPushNotifications:
    """Tests for logout_push_notifications method."""

    @respx.mock
    def test_logout_push_notifications_success(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/iosregids/user/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )
        respx.put("https://test.ispyfire.com/api/ddui/gcmregids/user/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )
        respx.get(
            "https://test.ispyfire.com/api/mobile/clearallispyidnotifications/jdoe@sjifire.org/test"
        ).mock(return_value=httpx.Response(200))

        with ISpyFireClient() as client:
            result = client.logout_push_notifications("jdoe@sjifire.org")

        assert result is True

    @respx.mock
    def test_logout_push_notifications_partial_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/iosregids/user/jdoe@sjifire.org").mock(
            return_value=httpx.Response(500)  # iOS fails
        )
        respx.put("https://test.ispyfire.com/api/ddui/gcmregids/user/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )
        respx.get(
            "https://test.ispyfire.com/api/mobile/clearallispyidnotifications/jdoe@sjifire.org/test"
        ).mock(return_value=httpx.Response(200))

        with ISpyFireClient() as client:
            result = client.logout_push_notifications("jdoe@sjifire.org")

        assert result is False


class TestRemoveAllDevices:
    """Tests for remove_all_devices method."""

    @respx.mock
    def test_remove_all_devices_success(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/mobile/clearalluserdevices/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )

        with ISpyFireClient() as client:
            result = client.remove_all_devices("jdoe@sjifire.org")

        assert result is True

    @respx.mock
    def test_remove_all_devices_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.get("https://test.ispyfire.com/api/mobile/clearalluserdevices/jdoe@sjifire.org").mock(
            return_value=httpx.Response(500)
        )

        with ISpyFireClient() as client:
            result = client.remove_all_devices("jdoe@sjifire.org")

        assert result is False


class TestSendInviteEmail:
    """Tests for send_invite_email method."""

    @respx.mock
    def test_send_invite_email_success(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/login/passinvite/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )

        with ISpyFireClient() as client:
            result = client.send_invite_email("jdoe@sjifire.org")

        assert result is True

    @respx.mock
    def test_send_invite_email_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/login/passinvite/jdoe@sjifire.org").mock(
            return_value=httpx.Response(500)
        )

        with ISpyFireClient() as client:
            result = client.send_invite_email("jdoe@sjifire.org")

        assert result is False


class TestDeactivatePerson:
    """Tests for deactivate_person method."""

    @respx.mock
    def test_deactivate_person_success_with_email(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        # Push notification logout
        respx.put("https://test.ispyfire.com/api/ddui/iosregids/user/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )
        respx.put("https://test.ispyfire.com/api/ddui/gcmregids/user/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )
        respx.get(
            "https://test.ispyfire.com/api/mobile/clearallispyidnotifications/jdoe@sjifire.org/test"
        ).mock(return_value=httpx.Response(200))
        # Remove devices
        respx.get("https://test.ispyfire.com/api/mobile/clearalluserdevices/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )
        # Deactivate
        respx.put("https://test.ispyfire.com/api/ddui/people/abc123").mock(
            return_value=httpx.Response(200)
        )

        with ISpyFireClient() as client:
            result = client.deactivate_person("abc123", email="jdoe@sjifire.org")

        assert result is True

    @respx.mock
    def test_deactivate_person_without_email(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people/abc123").mock(
            return_value=httpx.Response(200)
        )

        with ISpyFireClient() as client:
            result = client.deactivate_person("abc123")

        assert result is True

    @respx.mock
    def test_deactivate_person_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people/abc123").mock(
            return_value=httpx.Response(500)
        )

        with ISpyFireClient() as client:
            result = client.deactivate_person("abc123")

        assert result is False


class TestReactivatePerson:
    """Tests for reactivate_person method."""

    @respx.mock
    def test_reactivate_person_success_with_email(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people/abc123").mock(
            return_value=httpx.Response(200)
        )
        respx.put("https://test.ispyfire.com/api/login/passinvite/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )

        with ISpyFireClient() as client:
            result = client.reactivate_person("abc123", email="jdoe@sjifire.org")

        assert result is True

    @respx.mock
    def test_reactivate_person_without_email(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people/abc123").mock(
            return_value=httpx.Response(200)
        )

        with ISpyFireClient() as client:
            result = client.reactivate_person("abc123")

        assert result is True

    @respx.mock
    def test_reactivate_person_failure(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people/abc123").mock(
            return_value=httpx.Response(500)
        )

        with ISpyFireClient() as client:
            result = client.reactivate_person("abc123", email="jdoe@sjifire.org")

        assert result is False


class TestCreateAndInvite:
    """Tests for create_and_invite method."""

    @respx.mock
    def test_create_and_invite_success(self, mock_credentials, sample_person_data):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people").mock(
            return_value=httpx.Response(201, json={"results": [sample_person_data]})
        )
        respx.put("https://test.ispyfire.com/api/login/passinvite/jdoe@sjifire.org").mock(
            return_value=httpx.Response(200)
        )

        person = ISpyFirePerson(
            id="",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
        )

        with ISpyFireClient() as client:
            result = client.create_and_invite(person)

        assert result is not None
        assert result.id == "abc123"
        # Verify active flags were set
        assert person.is_active is True
        assert person.is_login_active is True

    @respx.mock
    def test_create_and_invite_create_fails(self, mock_credentials):
        respx.post("https://test.ispyfire.com/login").mock(return_value=httpx.Response(200))
        respx.put("https://test.ispyfire.com/api/ddui/people").mock(
            return_value=httpx.Response(400)
        )

        person = ISpyFirePerson(
            id="",
            first_name="John",
            last_name="Doe",
            email="jdoe@sjifire.org",
        )

        with ISpyFireClient() as client:
            result = client.create_and_invite(person)

        assert result is None


class TestGetIspyId:
    """Tests for _get_ispyid helper method."""

    def test_extracts_ispyid_from_url(self, mock_credentials):
        with patch("sjifire.ispyfire.client.get_ispyfire_credentials") as mock:
            mock.return_value = ("https://sjf3.ispyfire.com", "user", "pass")
            client = ISpyFireClient()
            assert client._get_ispyid() == "sjf3"

    def test_extracts_ispyid_lowercase(self, mock_credentials):
        with patch("sjifire.ispyfire.client.get_ispyfire_credentials") as mock:
            mock.return_value = ("https://SJF3.ispyfire.com", "user", "pass")
            client = ISpyFireClient()
            assert client._get_ispyid() == "sjf3"

    def test_returns_empty_for_invalid_url(self, mock_credentials):
        with patch("sjifire.ispyfire.client.get_ispyfire_credentials") as mock:
            mock.return_value = ("https://example.com", "user", "pass")
            client = ISpyFireClient()
            assert client._get_ispyid() == ""
