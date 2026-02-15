"""Tests for the lookup_location chat tool."""

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.chat.engine import _summarize_tool_result
from sjifire.ops.chat.tools import _lookup_location, execute_tool


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


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

NOMINATIM_RESPONSE = {
    "display_name": "589, Old Farm Road, San Juan County, Washington, 98250, United States",
    "address": {
        "house_number": "589",
        "road": "Old Farm Road",
        "county": "San Juan County",
        "state": "Washington",
        "city": "Friday Harbor",
    },
}

OVERPASS_RESPONSE = {
    "elements": [
        {"type": "way", "id": 1, "tags": {"name": "Cattle Point Road", "highway": "secondary"}},
        {"type": "way", "id": 2, "tags": {"name": "Old Farm Road", "highway": "residential"}},
        {"type": "way", "id": 3, "tags": {"name": "Pear Point Road", "highway": "tertiary"}},
        {"type": "way", "id": 4, "tags": {"name": "Driveway Access", "highway": "service"}},
        {"type": "way", "id": 5, "tags": {"name": "Deer Trail Lane", "highway": "residential"}},
    ],
}


def _mock_response(data: dict, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("GET", "https://example.com"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLookupLocation:
    async def test_returns_road_and_cross_streets(self):
        """Should return the main road and nearby cross streets."""
        with patch("sjifire.ops.chat.tools.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=_mock_response(NOMINATIM_RESPONSE))
            client.post = AsyncMock(return_value=_mock_response(OVERPASS_RESPONSE))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _lookup_location(48.464012, -123.037876)

        assert result["road"] == "Old Farm Road"
        assert result["city"] == "Friday Harbor"
        assert result["state"] == "Washington"

        # Cross streets should not include the main road itself
        cross_names = [c["name"] for c in result["cross_streets"]]
        assert "Old Farm Road" not in cross_names
        assert "Cattle Point Road" in cross_names
        assert "Pear Point Road" in cross_names

    async def test_cross_streets_sorted_by_importance(self):
        """Major roads (secondary/tertiary) should sort before residential."""
        with patch("sjifire.ops.chat.tools.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=_mock_response(NOMINATIM_RESPONSE))
            client.post = AsyncMock(return_value=_mock_response(OVERPASS_RESPONSE))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _lookup_location(48.464012, -123.037876)

        cross = result["cross_streets"]
        # Secondary road should come first
        assert cross[0]["name"] == "Cattle Point Road"
        assert cross[0]["type"] == "secondary"
        # Tertiary should come before residential
        assert cross[1]["name"] == "Pear Point Road"
        assert cross[1]["type"] == "tertiary"

    async def test_includes_road_type(self):
        """Each cross street entry should have name and type fields."""
        with patch("sjifire.ops.chat.tools.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=_mock_response(NOMINATIM_RESPONSE))
            client.post = AsyncMock(return_value=_mock_response(OVERPASS_RESPONSE))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _lookup_location(48.464012, -123.037876)

        for cross in result["cross_streets"]:
            assert "name" in cross
            assert "type" in cross

    async def test_deduplicates_roads(self):
        """Same road name appearing in multiple elements should only appear once."""
        overpass_dupes = {
            "elements": [
                {
                    "type": "way",
                    "id": 1,
                    "tags": {"name": "Cattle Point Road", "highway": "secondary"},
                },
                {
                    "type": "way",
                    "id": 2,
                    "tags": {"name": "Cattle Point Road", "highway": "secondary"},
                },
                {"type": "way", "id": 3, "tags": {"name": "Side Lane", "highway": "residential"}},
            ],
        }
        with patch("sjifire.ops.chat.tools.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=_mock_response(NOMINATIM_RESPONSE))
            client.post = AsyncMock(return_value=_mock_response(overpass_dupes))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _lookup_location(48.464012, -123.037876)

        names = [c["name"] for c in result["cross_streets"]]
        assert names.count("Cattle Point Road") == 1

    async def test_overpass_failure_returns_empty_cross_streets(self):
        """If Overpass fails, should still return address with empty cross streets."""
        with patch("sjifire.ops.chat.tools.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=_mock_response(NOMINATIM_RESPONSE))
            client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _lookup_location(48.464012, -123.037876)

        assert result["road"] == "Old Farm Road"
        assert result["cross_streets"] == []
        assert result["city"] == "Friday Harbor"

    async def test_skips_unnamed_roads(self):
        """Roads without a name tag should be excluded."""
        overpass_unnamed = {
            "elements": [
                {"type": "way", "id": 1, "tags": {"name": "Main St", "highway": "primary"}},
                {"type": "way", "id": 2, "tags": {"highway": "service"}},
                {"type": "way", "id": 3, "tags": {"name": "", "highway": "residential"}},
            ],
        }
        with patch("sjifire.ops.chat.tools.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=_mock_response(NOMINATIM_RESPONSE))
            client.post = AsyncMock(return_value=_mock_response(overpass_unnamed))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _lookup_location(48.464012, -123.037876)

        names = [c["name"] for c in result["cross_streets"]]
        assert "Main St" in names
        assert "" not in names
        assert len(names) == 1  # Only "Main St" (Old Farm Road excluded as main)

    async def test_city_fallback_to_town(self):
        """If no city in address, should fall back to town then village."""
        nominatim_town = {
            "display_name": "123 Main St, Town, WA",
            "address": {
                "road": "Main Street",
                "town": "Friday Harbor",
                "state": "Washington",
            },
        }
        with patch("sjifire.ops.chat.tools.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=_mock_response(nominatim_town))
            client.post = AsyncMock(return_value=_mock_response({"elements": []}))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _lookup_location(48.5, -123.0)

        assert result["city"] == "Friday Harbor"


class TestLookupLocationDispatch:
    """Test that lookup_location is properly wired in execute_tool."""

    async def test_dispatch_routes_correctly(self, auth_user):
        """execute_tool should route 'lookup_location' to _lookup_location."""
        with patch(
            "sjifire.ops.chat.tools._lookup_location",
            new_callable=AsyncMock,
            return_value={"road": "Test Rd", "cross_streets": []},
        ) as mock_lookup:
            raw = await execute_tool(
                "lookup_location",
                {"latitude": 48.5, "longitude": -123.0},
                auth_user,
            )
            result = json.loads(raw)

        mock_lookup.assert_called_once_with(48.5, -123.0)
        assert result["road"] == "Test Rd"


class TestSummarizeLocationResult:
    """Test the tool result summary shown in the chat UI."""

    def test_with_cross_streets(self):
        result = _summarize_tool_result(
            "lookup_location",
            {
                "road": "Old Farm Road",
                "cross_streets": [
                    {"name": "Cattle Point Road", "type": "secondary"},
                    {"name": "Pear Point Road", "type": "tertiary"},
                ],
            },
        )
        assert "Old Farm Road" in result
        assert "Cattle Point Road" in result

    def test_no_cross_streets(self):
        result = _summarize_tool_result(
            "lookup_location",
            {"road": "Remote Lane", "cross_streets": []},
        )
        assert "Remote Lane" in result
        assert "no cross streets" in result

    def test_truncates_long_list(self):
        result = _summarize_tool_result(
            "lookup_location",
            {
                "road": "Center Rd",
                "cross_streets": [{"name": f"Road {i}", "type": "residential"} for i in range(10)],
            },
        )
        # Should only show first 3
        assert "Road 0" in result
        assert "Road 2" in result
        assert "Road 5" not in result
