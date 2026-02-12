"""Tests for sjifire.neris.client module."""

from unittest.mock import MagicMock, patch

import pytest

from sjifire.neris.client import ENTITY_ID, NerisClient, get_neris_credentials


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
