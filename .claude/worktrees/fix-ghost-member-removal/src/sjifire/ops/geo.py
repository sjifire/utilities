"""Azure Maps geocoding module.

Wraps Azure Maps Search REST API for reverse geocoding and cross-street
lookup.  Returns the same shape as the legacy OSM Nominatim + Overpass
approach so callers don't need to change.

Requires ``AZURE_MAPS_KEY`` environment variable.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://atlas.microsoft.com"

# Map Azure Maps entityType to a human-readable property type
_ENTITY_TYPE_MAP = {
    "Address": "building",
    "Street": "street",
    "CrossStreet": "intersection",
    "RoadBlock": "road",
    "CountrySubdivision": "region",
    "CountrySecondarySubdivision": "county",
    "Neighbourhood": "neighborhood",
    "PostalCodeArea": "postal",
    "Municipality": "city",
    "MunicipalitySubdivision": "district",
    "Country": "country",
    "CountryTertiarySubdivision": "township",
    "PointOfInterest": "poi",
}


def get_azure_maps_key() -> str | None:
    """Return the Azure Maps subscription key, or None if not set."""
    return os.getenv("AZURE_MAPS_KEY") or None


async def reverse_geocode(lat: float, lon: float) -> dict:
    """Reverse geocode coordinates using Azure Maps.

    Makes two calls:
    1. Reverse address lookup (road, city, county, state, display address)
    2. Reverse cross-street lookup (nearest cross streets)

    Cross-street failure degrades gracefully (returns empty list).

    Args:
        lat: Latitude
        lon: Longitude

    Returns:
        Dict matching the shape of the legacy ``_lookup_location()``::

            {
                "road": str,
                "cross_streets": [{"name": str, "type": str}, ...],
                "city": str,
                "county": str,
                "state": str,
                "display_address": str,
                "property_type": str,
            }

    Raises:
        ValueError: If AZURE_MAPS_KEY is not set
        httpx.HTTPStatusError: If the Azure Maps API returns an error
    """
    api_key = get_azure_maps_key()
    if not api_key:
        msg = "AZURE_MAPS_KEY environment variable not set"
        raise ValueError(msg)

    query = f"{lat},{lon}"
    common_params = {
        "api-version": "1.0",
        "subscription-key": api_key,
        "query": query,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. Reverse geocode — address from coordinates
        resp = await client.get(
            f"{_BASE_URL}/search/address/reverse/json",
            params=common_params,
        )
        resp.raise_for_status()
        data = resp.json()

        addresses = data.get("addresses", [])
        if not addresses:
            return {
                "road": "",
                "cross_streets": [],
                "city": "",
                "county": "",
                "state": "",
                "display_address": "",
                "property_type": "",
            }

        addr = addresses[0].get("address", {})
        entity_type = addresses[0].get("entityType", "")

        road = addr.get("streetName", "")
        city = addr.get("municipality", "")
        county = addr.get("countrySecondarySubdivision", "")
        state = addr.get("countrySubdivision", "")
        display_address = addr.get("freeformAddress", "")
        fallback = entity_type.lower() if entity_type else ""
        property_type = _ENTITY_TYPE_MAP.get(entity_type, fallback)

        # 2. Reverse cross-street lookup
        cross_streets: list[dict] = []
        try:
            resp2 = await client.get(
                f"{_BASE_URL}/search/address/reverse/crossStreet/json",
                params=common_params,
            )
            resp2.raise_for_status()
            cross_data = resp2.json()
            for cs_addr in cross_data.get("addresses", []):
                cs_info = cs_addr.get("address", {})
                street = cs_info.get("streetName", "")
                if street and street.lower() != road.lower():
                    cross_streets.append({"name": street, "type": "road"})
        except Exception:
            logger.warning("Azure Maps cross-street lookup failed for %.6f,%.6f", lat, lon)

    return {
        "road": road,
        "cross_streets": cross_streets,
        "city": city,
        "county": county,
        "state": state,
        "display_address": display_address,
        "property_type": property_type,
    }
