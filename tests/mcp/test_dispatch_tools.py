"""Tests for MCP dispatch tools."""

import os
from unittest.mock import patch

import pytest

from sjifire.ispyfire.models import DispatchCall, UnitResponse
from sjifire.mcp.auth import UserContext, set_current_user
from sjifire.mcp.dispatch.tools import (
    _call_to_dict,
    get_dispatch_call,
    get_dispatch_call_log,
    get_open_dispatch_calls,
    list_dispatch_calls,
)


@pytest.fixture(autouse=True)
def _dev_mode():
    """Ensure dev mode (no Entra config) so get_current_user() works."""
    with patch.dict(os.environ, {"ENTRA_MCP_API_CLIENT_ID": ""}, clear=False):
        set_current_user(None)
        yield


@pytest.fixture
def auth_user():
    user = UserContext(email="ff@sjifire.org", name="Firefighter", user_id="user-1")
    set_current_user(user)
    return user


@pytest.fixture
def sample_call():
    return DispatchCall(
        id="call-uuid-123",
        long_term_call_id="26-001678",
        nature="Medical Aid",
        address="200 Spring St",
        agency_code="SJF",
        type="EMS",
        zone_code="Z1",
        time_reported="2026-02-12 14:30:00",
        is_completed=False,
        comments="Patient fall, possible hip injury",
        joined_responders="E31,M31",
        responder_details=[
            UnitResponse(
                unit_number="E31",
                agency_code="SJF",
                status="Dispatched",
                time_of_status_change="2026-02-12 14:30:15",
                radio_log="E31 dispatched",
            ),
        ],
        ispy_responders={"user1": "responding"},
        city="Friday Harbor",
        state="WA",
        zip_code="98250",
        geo_location="48.5343,-123.0170",
        created_timestamp=1739388600,
    )


@pytest.fixture
def sample_call_dict(sample_call):
    return _call_to_dict(sample_call)


class TestCallToDict:
    def test_converts_dataclass_to_dict(self, sample_call):
        result = _call_to_dict(sample_call)
        assert isinstance(result, dict)
        assert result["id"] == "call-uuid-123"
        assert result["long_term_call_id"] == "26-001678"
        assert result["nature"] == "Medical Aid"
        assert result["address"] == "200 Spring St"

    def test_nested_responder_details(self, sample_call):
        result = _call_to_dict(sample_call)
        assert len(result["responder_details"]) == 1
        assert result["responder_details"][0]["unit_number"] == "E31"
        assert result["responder_details"][0]["status"] == "Dispatched"

    def test_preserves_all_fields(self, sample_call):
        result = _call_to_dict(sample_call)
        assert result["city"] == "Friday Harbor"
        assert result["state"] == "WA"
        assert result["zip_code"] == "98250"
        assert result["geo_location"] == "48.5343,-123.0170"
        assert result["ispy_responders"] == {"user1": "responding"}
        assert result["is_completed"] is False


class TestListDispatchCalls:
    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    async def test_returns_calls(self, mock_fetch, auth_user, sample_call_dict):
        mock_fetch.return_value = [sample_call_dict]

        result = await list_dispatch_calls(days=30)

        assert result["count"] == 1
        assert result["calls"][0]["nature"] == "Medical Aid"
        mock_fetch.assert_called_once_with(30)

    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    async def test_returns_empty_list(self, mock_fetch, auth_user):
        mock_fetch.return_value = []

        result = await list_dispatch_calls(days=7)

        assert result["count"] == 0
        assert result["calls"] == []
        mock_fetch.assert_called_once_with(7)

    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    async def test_default_days(self, mock_fetch, auth_user):
        mock_fetch.return_value = []

        await list_dispatch_calls()

        mock_fetch.assert_called_once_with(30)

    @patch("sjifire.mcp.dispatch.tools._fetch_calls")
    async def test_handles_exception(self, mock_fetch, auth_user):
        mock_fetch.side_effect = RuntimeError("connection failed")

        result = await list_dispatch_calls()

        assert "error" in result
        assert "connection failed" in result["error"]

    async def test_requires_auth(self):
        """In production mode, unauthenticated requests fail."""
        set_current_user(None)
        with (
            patch.dict(os.environ, {"ENTRA_MCP_API_CLIENT_ID": "real-client-id"}),
            pytest.raises(RuntimeError, match="No authenticated user"),
        ):
            await list_dispatch_calls()


class TestGetDispatchCall:
    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_returns_call_by_uuid(self, mock_fetch, auth_user, sample_call_dict):
        mock_fetch.return_value = sample_call_dict

        result = await get_dispatch_call("call-uuid-123")

        assert result["nature"] == "Medical Aid"
        assert result["long_term_call_id"] == "26-001678"
        mock_fetch.assert_called_once_with("call-uuid-123")

    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_returns_call_by_dispatch_id(self, mock_fetch, auth_user, sample_call_dict):
        mock_fetch.return_value = sample_call_dict

        result = await get_dispatch_call("26-001678")

        assert result["nature"] == "Medical Aid"
        mock_fetch.assert_called_once_with("26-001678")

    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_not_found(self, mock_fetch, auth_user):
        mock_fetch.return_value = None

        result = await get_dispatch_call("nonexistent")

        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.mcp.dispatch.tools._fetch_call_details")
    async def test_handles_exception(self, mock_fetch, auth_user):
        mock_fetch.side_effect = RuntimeError("API error")

        result = await get_dispatch_call("call-uuid-123")

        assert "error" in result
        assert "API error" in result["error"]


class TestGetOpenDispatchCalls:
    @patch("sjifire.mcp.dispatch.tools._fetch_open_calls")
    async def test_returns_open_calls(self, mock_fetch, auth_user, sample_call_dict):
        mock_fetch.return_value = [sample_call_dict]

        result = await get_open_dispatch_calls()

        assert result["count"] == 1
        assert result["calls"][0]["is_completed"] is False

    @patch("sjifire.mcp.dispatch.tools._fetch_open_calls")
    async def test_returns_empty_when_no_open(self, mock_fetch, auth_user):
        mock_fetch.return_value = []

        result = await get_open_dispatch_calls()

        assert result["count"] == 0
        assert result["calls"] == []

    @patch("sjifire.mcp.dispatch.tools._fetch_open_calls")
    async def test_handles_exception(self, mock_fetch, auth_user):
        mock_fetch.side_effect = RuntimeError("timeout")

        result = await get_open_dispatch_calls()

        assert "error" in result
        assert "timeout" in result["error"]


class TestGetDispatchCallLog:
    @patch("sjifire.mcp.dispatch.tools._fetch_call_log")
    async def test_returns_log_entries(self, mock_fetch, auth_user):
        mock_fetch.return_value = [
            {
                "email": "chief@sjifire.org",
                "commenttype": "viewed",
                "timestamp": "2026-02-12T15:00:00Z",
            },
            {
                "email": "ff@sjifire.org",
                "commenttype": "viewed",
                "timestamp": "2026-02-12T15:05:00Z",
            },
        ]

        result = await get_dispatch_call_log("call-uuid-123")

        assert result["count"] == 2
        assert result["entries"][0]["email"] == "chief@sjifire.org"
        mock_fetch.assert_called_once_with("call-uuid-123")

    @patch("sjifire.mcp.dispatch.tools._fetch_call_log")
    async def test_empty_log(self, mock_fetch, auth_user):
        mock_fetch.return_value = []

        result = await get_dispatch_call_log("call-uuid-123")

        assert result["count"] == 0
        assert result["entries"] == []

    @patch("sjifire.mcp.dispatch.tools._fetch_call_log")
    async def test_handles_exception(self, mock_fetch, auth_user):
        mock_fetch.side_effect = RuntimeError("not found")

        result = await get_dispatch_call_log("bad-id")

        assert "error" in result
        assert "not found" in result["error"]
