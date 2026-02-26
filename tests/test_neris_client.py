"""Tests for sjifire.neris.client module."""

from unittest.mock import MagicMock, patch

import pytest

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
            patch.dict("os.environ", {"NERIS_CLIENT_SECRET": "test-secret"}, clear=True),
            pytest.raises(ValueError, match="NERIS_CLIENT_ID"),
        ):
            get_neris_credentials()

    def test_raises_when_client_secret_missing(self):
        with (
            patch.dict("os.environ", {"NERIS_CLIENT_ID": "test-id"}, clear=True),
            pytest.raises(ValueError, match="NERIS_CLIENT_SECRET"),
        ):
            get_neris_credentials()

    def test_raises_when_both_missing(self):
        with (
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


class TestGetIncidentByNerisId:
    """Tests for get_incident with compound NERIS ID (contains '|')."""

    def test_found_via_targeted_filter(self, mock_credentials, mock_api):
        """Targeted incident_number filter finds the incident on first try."""
        mock_api.list_incidents.return_value = {
            "incidents": [{"neris_id": SAMPLE_NERIS_ID}],
        }

        with NerisClient() as client:
            result = client.get_incident(SAMPLE_NERIS_ID)

        assert result is not None
        assert result["neris_id"] == SAMPLE_NERIS_ID
        # Should use incident_number filter (middle segment "26SJ0020")
        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity=ENTITY_ID,
            page_size=100,
            cursor=None,
            incident_number="26SJ0020",
        )

    def test_falls_back_to_full_scan(self, mock_credentials, mock_api):
        """Falls back to full scan when targeted filter misses."""
        mock_api.list_incidents.side_effect = [
            # First call: targeted filter returns no match
            {"incidents": []},
            # Second call: full scan finds it
            {"incidents": [{"neris_id": SAMPLE_NERIS_ID}]},
        ]

        with NerisClient() as client:
            result = client.get_incident(SAMPLE_NERIS_ID)

        assert result is not None
        assert result["neris_id"] == SAMPLE_NERIS_ID
        assert mock_api.list_incidents.call_count == 2

    def test_not_found(self, mock_credentials, mock_api):
        """Returns None when incident doesn't exist."""
        mock_api.list_incidents.return_value = {"incidents": []}

        with NerisClient() as client:
            result = client.get_incident(SAMPLE_NERIS_ID)

        assert result is None

    def test_custom_entity(self, mock_credentials, mock_api):
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
            incident_number="INC001",
        )


class TestGetIncidentByNumber:
    """Tests for get_incident with a local CAD number (no '|')."""

    def test_found_by_incident_number(self, mock_credentials, mock_api):
        """Exact incident_number match works."""
        mock_api.list_incidents.return_value = {
            "incidents": [{"neris_id": SAMPLE_NERIS_ID, "incident_number": "26-002358"}],
        }

        with NerisClient() as client:
            result = client.get_incident("26-002358")

        assert result is not None
        mock_api.list_incidents.assert_called_once_with(
            neris_id_entity=ENTITY_ID,
            page_size=100,
            cursor=None,
            incident_number="26-002358",
        )

    def test_found_by_stripped_number(self, mock_credentials, mock_api):
        """Tries with dashes removed when exact match fails."""
        mock_api.list_incidents.side_effect = [
            # First: exact "26-002358" → no results
            {"incidents": []},
            # Second: stripped "26002358" → found
            {"incidents": [{"neris_id": SAMPLE_NERIS_ID}]},
        ]

        with NerisClient() as client:
            result = client.get_incident("26-002358")

        assert result is not None
        assert mock_api.list_incidents.call_count == 2

    def test_found_by_dispatch_incident_number(self, mock_credentials, mock_api):
        """Falls through to dispatch_incident_number filter."""
        mock_api.list_incidents.side_effect = [
            # incident_number exact → miss
            {"incidents": []},
            # incident_number stripped → miss
            {"incidents": []},
            # dispatch_incident_number exact → found
            {"incidents": [{"neris_id": SAMPLE_NERIS_ID}]},
        ]

        with NerisClient() as client:
            result = client.get_incident("26-002358")

        assert result is not None
        assert mock_api.list_incidents.call_count == 3

    def test_found_by_determinant_code(self, mock_credentials, mock_api):
        """Falls through to determinant_code scan as last resort."""
        mock_api.list_incidents.side_effect = [
            # incident_number exact → miss
            {"incidents": []},
            # incident_number stripped → miss
            {"incidents": []},
            # dispatch_incident_number exact → miss
            {"incidents": []},
            # dispatch_incident_number stripped → miss
            {"incidents": []},
            # full scan → found via determinant_code
            {
                "incidents": [
                    {
                        "neris_id": SAMPLE_NERIS_ID,
                        "dispatch": {"determinant_code": "26002358"},
                    }
                ]
            },
        ]

        with NerisClient() as client:
            result = client.get_incident("26-002358")

        assert result is not None
        assert result["neris_id"] == SAMPLE_NERIS_ID
        assert mock_api.list_incidents.call_count == 5

    def test_not_found(self, mock_credentials, mock_api):
        """Returns None when no filter matches."""
        mock_api.list_incidents.return_value = {"incidents": []}

        with NerisClient() as client:
            result = client.get_incident("99-999999")

        assert result is None


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
