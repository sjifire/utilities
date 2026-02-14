"""Tests for sjifire.neris.client module."""

from unittest.mock import MagicMock, patch

import pytest
from neris_api_client.models import TypeIncidentStatusPayloadValue

from sjifire.core.config import get_org_config
from sjifire.neris.client import NerisClient, get_neris_credentials

ENTITY_ID = get_org_config().neris_entity_id


class TestGetNerisCredentials:
    """Tests for get_neris_credentials."""

    def test_returns_credentials_from_env(self):
        with patch.dict(
            "os.environ",
            {"NERIS_CLIENT_ID": "test-id", "NERIS_CLIENT_SECRET": "test-secret"},
        ):
            client_id, client_secret = get_neris_credentials()
        assert client_id == "test-id"
        assert client_secret == "test-secret"

    def test_raises_when_client_id_missing(self):
        with (
            patch("sjifire.neris.client.load_dotenv"),
            patch.dict("os.environ", {"NERIS_CLIENT_SECRET": "test-secret"}, clear=True),
            pytest.raises(ValueError, match="NERIS_CLIENT_ID"),
        ):
            get_neris_credentials()

    def test_raises_when_client_secret_missing(self):
        with (
            patch("sjifire.neris.client.load_dotenv"),
            patch.dict("os.environ", {"NERIS_CLIENT_ID": "test-id"}, clear=True),
            pytest.raises(ValueError, match="NERIS_CLIENT_SECRET"),
        ):
            get_neris_credentials()

    def test_raises_when_both_missing(self):
        with (
            patch("sjifire.neris.client.load_dotenv"),
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="NERIS credentials not set"),
        ):
            get_neris_credentials()


@pytest.fixture
def mock_credentials():
    """Mock the credentials function."""
    with patch("sjifire.neris.client.get_neris_credentials") as mock:
        mock.return_value = ("test-client-id", "test-client-secret")
        yield mock


@pytest.fixture
def mock_api():
    """Mock the NerisApiClient constructor."""
    with patch("sjifire.neris.client.NerisApiClient") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        yield mock_instance


class TestNerisClientInit:
    """Tests for client initialization."""

    def test_default_entity_id(self):
        client = NerisClient()
        assert client.entity_id == ENTITY_ID

    def test_custom_entity_id(self):
        client = NerisClient(entity_id="FD99999999")
        assert client.entity_id == "FD99999999"

    def test_client_not_connected_before_enter(self):
        client = NerisClient()
        assert client._client is None


class TestNerisClientContextManager:
    """Tests for context manager behavior."""

    def test_enter_creates_api_client(self, mock_credentials, mock_api):
        with NerisClient() as client:
            assert client._client is not None

    def test_exit_clears_client(self, mock_credentials, mock_api):
        client = NerisClient()
        with client:
            assert client._client is not None
        assert client._client is None

    def test_enter_passes_credentials_to_config(self, mock_credentials):
        with patch("sjifire.neris.client.NerisApiClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            with NerisClient():
                pass

            config = mock_cls.call_args[0][0]
            assert config.client_id == "test-client-id"
            assert config.client_secret == "test-client-secret"


class TestApiProperty:
    """Tests for the api property."""

    def test_api_returns_client_inside_context(self, mock_credentials, mock_api):
        with NerisClient() as client:
            assert client.api is mock_api

    def test_api_raises_outside_context(self):
        client = NerisClient()
        with pytest.raises(RuntimeError, match="context manager"):
            _ = client.api


class TestHealth:
    """Tests for health method."""

    def test_health_returns_api_result(self, mock_credentials, mock_api):
        mock_api.health.return_value = "OK"

        with NerisClient() as client:
            result = client.health()

        assert result == "OK"
        mock_api.health.assert_called_once()


class TestGetEntity:
    """Tests for get_entity method."""

    def test_get_entity_default_id(self, mock_credentials, mock_api):
        mock_api.get_entity.return_value = {"name": "Test FD", "neris_id": ENTITY_ID}

        with NerisClient() as client:
            result = client.get_entity()

        assert result["name"] == "Test FD"
        mock_api.get_entity.assert_called_once_with(ENTITY_ID)

    def test_get_entity_custom_id(self, mock_credentials, mock_api):
        mock_api.get_entity.return_value = {"name": "Other FD", "neris_id": "FD99999999"}

        with NerisClient() as client:
            result = client.get_entity(neris_id="FD99999999")

        assert result["neris_id"] == "FD99999999"
        mock_api.get_entity.assert_called_once_with("FD99999999")

    def test_get_entity_raises_outside_context(self):
        client = NerisClient()
        with pytest.raises(RuntimeError, match="context manager"):
            client.get_entity()


class TestListIncidents:
    """Tests for list_incidents method."""

    def test_list_incidents_default_entity(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {"incidents": [{"neris_id": "inc1"}]}

        with NerisClient() as client:
            result = client.list_incidents()

        assert len(result["incidents"]) == 1
        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity=ENTITY_ID,
            page_size=100,
            cursor=None,
        )

    def test_list_incidents_custom_entity(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {"incidents": []}

        with NerisClient() as client:
            client.list_incidents(neris_id="FD99999999")

        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity="FD99999999",
            page_size=100,
            cursor=None,
        )

    def test_list_incidents_with_page_size(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {"incidents": []}

        with NerisClient() as client:
            client.list_incidents(page_size=10)

        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity=ENTITY_ID,
            page_size=10,
            cursor=None,
        )

    def test_list_incidents_with_cursor(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {"incidents": []}

        with NerisClient() as client:
            client.list_incidents(cursor="abc123")

        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity=ENTITY_ID,
            page_size=100,
            cursor="abc123",
        )

    def test_list_incidents_passes_kwargs(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {"incidents": []}

        with NerisClient() as client:
            client.list_incidents(status=["APPROVED"])

        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity=ENTITY_ID,
            page_size=100,
            cursor=None,
            status=["APPROVED"],
        )


class TestGetAllIncidents:
    """Tests for get_all_incidents method."""

    def test_single_page(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {
            "incidents": [{"neris_id": "inc1"}, {"neris_id": "inc2"}],
            "next_cursor": None,
        }

        with NerisClient() as client:
            result = client.get_all_incidents()

        assert len(result) == 2
        assert mock_api.list_incidents.call_count == 1

    def test_multiple_pages(self, mock_credentials, mock_api):
        mock_api.list_incidents.side_effect = [
            {
                "incidents": [{"neris_id": "inc1"}, {"neris_id": "inc2"}],
                "next_cursor": "cursor1",
            },
            {
                "incidents": [{"neris_id": "inc3"}],
                "next_cursor": None,
            },
        ]

        with NerisClient() as client:
            result = client.get_all_incidents()

        assert len(result) == 3
        assert result[0]["neris_id"] == "inc1"
        assert result[2]["neris_id"] == "inc3"
        assert mock_api.list_incidents.call_count == 2

        # Verify cursor was passed on second call
        second_call = mock_api.list_incidents.call_args_list[1]
        assert second_call.kwargs["cursor"] == "cursor1"

    def test_empty_result(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {
            "incidents": [],
            "next_cursor": None,
        }

        with NerisClient() as client:
            result = client.get_all_incidents()

        assert result == []
        assert mock_api.list_incidents.call_count == 1

    def test_custom_entity_id(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {
            "incidents": [{"neris_id": "inc1"}],
            "next_cursor": None,
        }

        with NerisClient() as client:
            client.get_all_incidents(neris_id="FD99999999")

        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity="FD99999999",
            page_size=100,
            cursor=None,
        )

    def test_passes_kwargs(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {
            "incidents": [],
            "next_cursor": None,
        }

        with NerisClient() as client:
            client.get_all_incidents(status=["APPROVED"])

        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity=ENTITY_ID,
            page_size=100,
            cursor=None,
            status=["APPROVED"],
        )

    def test_stops_on_empty_incidents(self, mock_credentials, mock_api):
        """Pagination stops when incidents list is empty even if cursor present."""
        mock_api.list_incidents.return_value = {
            "incidents": [],
            "next_cursor": "stale-cursor",
        }

        with NerisClient() as client:
            result = client.get_all_incidents()

        assert result == []
        assert mock_api.list_incidents.call_count == 1


class TestGetPendingIncidents:
    """Tests for get_pending_incidents method."""

    def test_filters_by_pending_approval(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {
            "incidents": [{"neris_id": "inc1"}],
            "next_cursor": None,
        }

        with NerisClient() as client:
            result = client.get_pending_incidents()

        assert len(result) == 1
        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity=ENTITY_ID,
            page_size=100,
            cursor=None,
            status=["PENDING_APPROVAL"],
        )

    def test_custom_entity_id(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {
            "incidents": [],
            "next_cursor": None,
        }

        with NerisClient() as client:
            client.get_pending_incidents(neris_id="FD99999999")

        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity="FD99999999",
            page_size=100,
            cursor=None,
            status=["PENDING_APPROVAL"],
        )


SAMPLE_NERIS_ID = f"{ENTITY_ID}|26SJ0020|1770457554"


class TestGetIncident:
    """Tests for get_incident — scans all incidents and matches by neris_id."""

    def test_get_incident_found(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {
            "incidents": [
                {"neris_id": "FD53055879|OTHER|9999"},
                {"neris_id": SAMPLE_NERIS_ID},
            ],
        }

        with NerisClient() as client:
            result = client.get_incident(SAMPLE_NERIS_ID)

        assert result is not None
        assert result["neris_id"] == SAMPLE_NERIS_ID

    def test_get_incident_not_found(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {
            "incidents": [{"neris_id": "FD53055879|OTHER|9999"}],
        }

        with NerisClient() as client:
            result = client.get_incident(SAMPLE_NERIS_ID)

        assert result is None

    def test_get_incident_empty_results(self, mock_credentials, mock_api):
        mock_api.list_incidents.return_value = {"incidents": []}

        with NerisClient() as client:
            result = client.get_incident(SAMPLE_NERIS_ID)

        assert result is None

    def test_get_incident_custom_entity(self, mock_credentials, mock_api):
        custom_id = "FD99999999|INC001|1234567890"
        mock_api.list_incidents.return_value = {
            "incidents": [{"neris_id": custom_id}],
        }

        with NerisClient() as client:
            result = client.get_incident(custom_id, neris_id="FD99999999")

        assert result is not None
        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity="FD99999999",
            page_size=100,
            cursor=None,
        )


class TestPatchIncident:
    """Tests for patch_incident method."""

    def test_patch_incident_builds_correct_body(self, mock_credentials, mock_api):
        mock_api.patch_incident.return_value = {"neris_id": SAMPLE_NERIS_ID}

        properties = {
            "base": {
                "outcome_narrative": {
                    "action": "set",
                    "value": "Updated narrative",
                }
            }
        }

        with NerisClient() as client:
            result = client.patch_incident(SAMPLE_NERIS_ID, properties)

        assert result["neris_id"] == SAMPLE_NERIS_ID
        mock_api.patch_incident.assert_called_once_with(
            ENTITY_ID,
            SAMPLE_NERIS_ID,
            {
                "neris_id": SAMPLE_NERIS_ID,
                "action": "patch",
                "properties": properties,
            },
        )

    def test_patch_incident_custom_entity(self, mock_credentials, mock_api):
        custom_id = "FD99999999|INC001|1234567890"
        mock_api.patch_incident.return_value = {"neris_id": custom_id}

        with NerisClient() as client:
            client.patch_incident(custom_id, {"base": {}}, neris_id="FD99999999")

        mock_api.patch_incident.assert_called_once_with(
            "FD99999999",
            custom_id,
            {
                "neris_id": custom_id,
                "action": "patch",
                "properties": {"base": {}},
            },
        )


class TestApproveIncident:
    """Tests for approve_incident method."""

    def test_approve_calls_update_status_with_approved(self, mock_credentials, mock_api):
        mock_api.update_incident_status.return_value = {
            "neris_id": SAMPLE_NERIS_ID,
            "incident_status": {"status": "APPROVED"},
        }

        with NerisClient() as client:
            result = client.approve_incident(SAMPLE_NERIS_ID)

        assert result["incident_status"]["status"] == "APPROVED"
        mock_api.update_incident_status.assert_called_once_with(
            ENTITY_ID,
            SAMPLE_NERIS_ID,
            TypeIncidentStatusPayloadValue.APPROVED,
        )

    def test_approve_custom_entity(self, mock_credentials, mock_api):
        custom_id = "FD99999999|INC001|1234567890"
        mock_api.update_incident_status.return_value = {"neris_id": custom_id}

        with NerisClient() as client:
            client.approve_incident(custom_id, neris_id="FD99999999")

        mock_api.update_incident_status.assert_called_once_with(
            "FD99999999",
            custom_id,
            TypeIncidentStatusPayloadValue.APPROVED,
        )


class TestRejectIncident:
    """Tests for reject_incident method."""

    def test_reject_calls_update_status_with_rejected(self, mock_credentials, mock_api):
        mock_api.update_incident_status.return_value = {
            "neris_id": SAMPLE_NERIS_ID,
            "incident_status": {"status": "REJECTED"},
        }

        with NerisClient() as client:
            result = client.reject_incident(SAMPLE_NERIS_ID)

        assert result["incident_status"]["status"] == "REJECTED"
        mock_api.update_incident_status.assert_called_once_with(
            ENTITY_ID,
            SAMPLE_NERIS_ID,
            TypeIncidentStatusPayloadValue.REJECTED,
        )

    def test_reject_custom_entity(self, mock_credentials, mock_api):
        custom_id = "FD99999999|INC001|1234567890"
        mock_api.update_incident_status.return_value = {"neris_id": custom_id}

        with NerisClient() as client:
            client.reject_incident(custom_id, neris_id="FD99999999")

        mock_api.update_incident_status.assert_called_once_with(
            "FD99999999",
            custom_id,
            TypeIncidentStatusPayloadValue.REJECTED,
        )
