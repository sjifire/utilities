"""NERIS API client wrapper."""

import logging
import os
from typing import Self

from neris_api_client import Config, GrantType, NerisApiClient
from neris_api_client.models import TypeIncidentStatusPayloadValue

from sjifire.core.config import get_org_config

logger = logging.getLogger(__name__)

BASE_URL = "https://api.neris.fsri.org/v1"


def get_neris_credentials() -> tuple[str, str]:
    """Get NERIS API credentials from environment.

    Returns:
        Tuple of (client_id, client_secret)

    Raises:
        ValueError: If any required credential is not set
    """
    client_id = os.getenv("NERIS_CLIENT_ID")
    client_secret = os.getenv("NERIS_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError(
            "NERIS credentials not set. Required: NERIS_CLIENT_ID, NERIS_CLIENT_SECRET"
        )

    return client_id, client_secret


class NerisClient:
    """Client for the NERIS API.

    Wraps the neris-api-client library with project-specific defaults
    (base URL, grant type, entity ID).

    Usage::

        with NerisClient() as client:
            entity = client.get_entity()
            incidents = client.list_incidents()
    """

    def __init__(self, entity_id: str | None = None) -> None:
        """Initialize with entity ID (defaults to organization.json)."""
        self.entity_id = entity_id or get_org_config().neris_entity_id
        self._client: NerisApiClient | None = None

    def __enter__(self) -> Self:
        """Authenticate and return client."""
        client_id, client_secret = get_neris_credentials()
        self._client = NerisApiClient(
            Config(
                base_url=BASE_URL,
                grant_type=GrantType.CLIENT_CREDENTIALS,
                client_id=client_id,
                client_secret=client_secret,
            )
        )
        logger.info("Connected to NERIS API")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close client."""
        self._client = None

    @property
    def api(self) -> NerisApiClient:
        """Access the underlying neris-api-client for direct API calls."""
        if self._client is None:
            raise RuntimeError("Client must be used as context manager")
        return self._client

    def health(self) -> str:
        """Check API health."""
        return self.api.health()

    def get_entity(self, neris_id: str | None = None) -> dict:
        """Get entity details.

        Args:
            neris_id: Entity ID (defaults to configured entity)
        """
        neris_id = neris_id or self.entity_id
        logger.info(f"Fetching entity {neris_id}")
        return self.api.get_entity(neris_id)

    def list_incidents(
        self,
        *,
        neris_id: str | None = None,
        page_size: int = 100,
        cursor: str | None = None,
        **kwargs,
    ) -> dict:
        """List incidents for the entity.

        Args:
            neris_id: Entity ID (defaults to configured entity)
            page_size: Results per page (max 100)
            cursor: Pagination cursor
            **kwargs: Additional filters passed to the API

        Returns:
            Dict with "incidents", "next_cursor", "prev_cursor" keys.

        Raises:
            RuntimeError: If the API returns an error response.
        """
        neris_id = neris_id or self.entity_id
        logger.info(f"Listing incidents for {neris_id}")
        result = self.api.list_incidents(
            neris_id_entity=neris_id,
            page_size=page_size,
            cursor=cursor,
            **kwargs,
        )
        # The upstream library returns the raw Response on HTTP errors
        # instead of raising. Detect and raise so callers get a clear error.
        if not isinstance(result, dict):
            status = getattr(result, "status_code", "unknown")
            raise RuntimeError(f"NERIS API error (HTTP {status})")
        return result

    def get_all_incidents(self, *, neris_id: str | None = None, **kwargs) -> list[dict]:
        """Fetch all incidents with automatic pagination.

        Args:
            neris_id: Entity ID (defaults to configured entity)
            **kwargs: Additional filters passed to the API

        Returns:
            List of all incident dicts
        """
        all_incidents: list[dict] = []
        cursor = None

        while True:
            result = self.list_incidents(neris_id=neris_id, page_size=100, cursor=cursor, **kwargs)
            incidents = result.get("incidents", [])
            all_incidents.extend(incidents)
            cursor = result.get("next_cursor")
            if not cursor or not incidents:
                break

        logger.info(f"Fetched {len(all_incidents)} total incidents")
        return all_incidents

    def get_pending_incidents(self, *, neris_id: str | None = None) -> list[dict]:
        """Fetch all incidents awaiting approval.

        Args:
            neris_id: Entity ID (defaults to configured entity)
        """
        return self.get_all_incidents(neris_id=neris_id, status=["PENDING_APPROVAL"])

    def get_incident(
        self,
        lookup: str,
        *,
        neris_id: str | None = None,
    ) -> dict | None:
        """Fetch a single incident by NERIS ID or CAD incident number.

        Uses targeted API filters to avoid fetching all incidents.
        Accepts either a compound NERIS ID (``FD53055879|26SJ0020|1770457554``)
        or a local CAD incident number (``26-002358``).

        Lookup strategy:
        1. If *lookup* contains ``|`` → treat as a compound NERIS ID and try
           ``incident_number`` filter with the middle segment, then match by
           full ``neris_id``.
        2. Otherwise → try ``incident_number`` filter (exact), then
           ``dispatch_incident_number`` filter.
        3. Fall back to a full scan as a last resort.

        Args:
            lookup: Compound NERIS ID **or** local CAD number.
            neris_id: Entity ID (defaults to configured entity).

        Returns:
            Incident dict, or None if not found.
        """
        neris_id = neris_id or self.entity_id

        if "|" in lookup:
            return self._get_by_neris_id(lookup, neris_id=neris_id)

        return self._get_by_incident_number(lookup, neris_id=neris_id)

    def _get_by_neris_id(
        self,
        neris_id_incident: str,
        *,
        neris_id: str,
    ) -> dict | None:
        """Look up by compound NERIS ID (e.g. FD…|26SJ0020|177…)."""
        parts = neris_id_incident.split("|")
        # Middle segment is the NERIS incident_number (no dashes)
        if len(parts) >= 2:
            incident_num = parts[1]
            incidents = self.get_all_incidents(
                neris_id=neris_id, incident_number=incident_num,
            )
            for inc in incidents:
                if inc.get("neris_id") == neris_id_incident:
                    return inc

        # Narrow search missed — fall back to full scan
        logger.debug("Targeted lookup missed for %s, falling back to full scan", neris_id_incident)
        for inc in self.get_all_incidents(neris_id=neris_id):
            if inc.get("neris_id") == neris_id_incident:
                return inc

        logger.warning("Incident not found: %s", neris_id_incident)
        return None

    def _get_by_incident_number(
        self,
        number: str,
        *,
        neris_id: str,
    ) -> dict | None:
        """Look up by local CAD / incident number (e.g. 26-002358)."""
        # Try incident_number filter (NERIS stores without dashes)
        stripped = number.replace("-", "")
        for variant in (number, stripped):
            incidents = self.get_all_incidents(
                neris_id=neris_id, incident_number=variant,
            )
            if incidents:
                if len(incidents) == 1:
                    return incidents[0]
                logger.info("incident_number=%s returned %d results", variant, len(incidents))
                return incidents[0]

        # Try dispatch_incident_number filter
        for variant in (number, stripped):
            incidents = self.get_all_incidents(
                neris_id=neris_id, dispatch_incident_number=variant,
            )
            if incidents:
                if len(incidents) == 1:
                    return incidents[0]
                logger.info(
                    "dispatch_incident_number=%s returned %d results",
                    variant, len(incidents),
                )
                return incidents[0]

        logger.warning("Incident not found for number: %s", number)
        return None

    def patch_incident(
        self,
        neris_id_incident: str,
        properties: dict,
        *,
        neris_id: str | None = None,
    ) -> dict:
        """Update specific fields on an incident.

        Uses the NERIS patch format::

            client.patch_incident("FD53055879|26SJ0020|1770457554", {
                "base": {
                    "outcome_narrative": {
                        "action": "set",
                        "value": "Updated narrative text"
                    }
                }
            })

        Args:
            neris_id_incident: Full incident NERIS ID
            properties: Patch properties dict (field -> action)
            neris_id: Entity ID (defaults to configured entity)

        Returns:
            Updated incident response
        """
        neris_id = neris_id or self.entity_id
        body = {
            "neris_id": neris_id_incident,
            "action": "patch",
            "properties": properties,
        }
        logger.info(f"Patching incident {neris_id_incident}")
        return self.api.patch_incident(neris_id, neris_id_incident, body)

    def approve_incident(
        self,
        neris_id_incident: str,
        *,
        neris_id: str | None = None,
    ) -> dict:
        """Approve an incident (transition from PENDING_APPROVAL to APPROVED).

        Args:
            neris_id_incident: Full incident NERIS ID
            neris_id: Entity ID (defaults to configured entity)

        Returns:
            Updated incident response
        """
        neris_id = neris_id or self.entity_id
        logger.info(f"Approving incident {neris_id_incident}")
        return self.api.update_incident_status(
            neris_id, neris_id_incident, TypeIncidentStatusPayloadValue.APPROVED
        )

    def reject_incident(
        self,
        neris_id_incident: str,
        *,
        neris_id: str | None = None,
    ) -> dict:
        """Reject an incident.

        Args:
            neris_id_incident: Full incident NERIS ID
            neris_id: Entity ID (defaults to configured entity)

        Returns:
            Updated incident response
        """
        neris_id = neris_id or self.entity_id
        logger.info(f"Rejecting incident {neris_id_incident}")
        return self.api.update_incident_status(
            neris_id, neris_id_incident, TypeIncidentStatusPayloadValue.REJECTED
        )
