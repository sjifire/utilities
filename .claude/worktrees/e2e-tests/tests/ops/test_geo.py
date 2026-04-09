"""Tests for the Azure Maps geo module."""

import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sjifire.ops.geo import reverse_geocode


def _mock_response(data: dict, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("GET", "https://atlas.microsoft.com/test"),
    )


# ---------------------------------------------------------------------------
# Sample Azure Maps API responses
# ---------------------------------------------------------------------------

REVERSE_GEOCODE_RESPONSE = {
    "addresses": [
        {
            "address": {
                "streetName": "Spring Street",
                "municipality": "Friday Harbor",
                "countrySecondarySubdivision": "San Juan County",
                "countrySubdivision": "WA",
                "freeformAddress": "180 Spring Street, Friday Harbor, WA 98250",
            },
            "entityType": "Address",
        }
    ]
}

CROSS_STREET_RESPONSE = {
    "addresses": [
        {
            "address": {
                "streetName": "First Street",
            }
        },
        {
            "address": {
                "streetName": "Second Street",
            }
        },
    ]
}

EMPTY_RESPONSE = {"addresses": []}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReverseGeocode:
    async def test_happy_path(self):
        """Should return address, cross streets, and property type."""
        with (
            patch.dict(os.environ, {"AZURE_MAPS_KEY": "test-key"}),
            patch("sjifire.ops.geo.httpx.AsyncClient") as mock_cls,
        ):
            client = AsyncMock()

            async def mock_get(url, **kwargs):
                if "crossStreet" in url:
                    return _mock_response(CROSS_STREET_RESPONSE)
                return _mock_response(REVERSE_GEOCODE_RESPONSE)

            client.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await reverse_geocode(48.534, -123.013)

        assert result["road"] == "Spring Street"
        assert result["city"] == "Friday Harbor"
        assert result["county"] == "San Juan County"
        assert result["state"] == "WA"
        assert result["display_address"] == "180 Spring Street, Friday Harbor, WA 98250"
        assert result["property_type"] == "building"

        # Cross streets
        cross_names = [c["name"] for c in result["cross_streets"]]
        assert "First Street" in cross_names
        assert "Second Street" in cross_names
        # Main road should not appear in cross streets
        assert "Spring Street" not in cross_names

    async def test_empty_result(self):
        """Should return empty fields when no addresses found."""
        with (
            patch.dict(os.environ, {"AZURE_MAPS_KEY": "test-key"}),
            patch("sjifire.ops.geo.httpx.AsyncClient") as mock_cls,
        ):
            client = AsyncMock()
            client.get = AsyncMock(return_value=_mock_response(EMPTY_RESPONSE))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await reverse_geocode(0.0, 0.0)

        assert result["road"] == ""
        assert result["cross_streets"] == []
        assert result["city"] == ""
        assert result["display_address"] == ""

    async def test_cross_street_failure_graceful(self):
        """Cross street failure should not fail the whole call."""
        with (
            patch.dict(os.environ, {"AZURE_MAPS_KEY": "test-key"}),
            patch("sjifire.ops.geo.httpx.AsyncClient") as mock_cls,
        ):
            client = AsyncMock()

            async def mock_get(url, **kwargs):
                if "crossStreet" in url:
                    raise httpx.ConnectError("timeout")
                return _mock_response(REVERSE_GEOCODE_RESPONSE)

            client.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await reverse_geocode(48.534, -123.013)

        assert result["road"] == "Spring Street"
        assert result["cross_streets"] == []  # Gracefully degraded
        assert result["city"] == "Friday Harbor"

    async def test_missing_api_key_raises(self):
        """Should raise ValueError when AZURE_MAPS_KEY is not set."""
        with (
            patch.dict(os.environ, {"AZURE_MAPS_KEY": ""}, clear=False),
            pytest.raises(ValueError, match="AZURE_MAPS_KEY"),
        ):
            await reverse_geocode(48.534, -123.013)


class TestLookupLocationFallback:
    """Test that _lookup_location uses Azure Maps when key is set, OSM otherwise."""

    async def test_uses_azure_maps_when_key_set(self):
        with (
            patch.dict(os.environ, {"AZURE_MAPS_KEY": "test-key"}),
            patch("sjifire.ops.geo.reverse_geocode", new_callable=AsyncMock) as mock_geo,
        ):
            mock_geo.return_value = {"road": "Azure Rd", "cross_streets": []}
            from sjifire.ops.chat.tools import _lookup_location

            result = await _lookup_location(48.5, -123.0)
            mock_geo.assert_called_once_with(48.5, -123.0)
            assert result["road"] == "Azure Rd"

    async def test_uses_osm_without_key(self):
        with (
            patch.dict(os.environ, {"AZURE_MAPS_KEY": ""}, clear=False),
            patch(
                "sjifire.ops.chat.tools._lookup_location_osm", new_callable=AsyncMock
            ) as mock_osm,
        ):
            mock_osm.return_value = {"road": "OSM Rd", "cross_streets": []}
            from sjifire.ops.chat.tools import _lookup_location

            result = await _lookup_location(48.5, -123.0)
            mock_osm.assert_called_once_with(48.5, -123.0)
            assert result["road"] == "OSM Rd"
