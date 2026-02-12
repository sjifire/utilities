"""NERIS API client wrapper."""

import logging
import os
from typing import Self

from dotenv import load_dotenv
from neris_api_client import Config, GrantType, NerisApiClient
from neris_api_client.models import TypeIncidentStatusPayloadValue

logger = logging.getLogger(__name__)

BASE_URL = "https://api.neris.fsri.org/v1"
ENTITY_ID = "FD53055879"


def get_neris_credentials() -> tuple[str, str]:
    """Get NERIS API credentials from environment.

    Returns:
        Tuple of (client_id, client_secret)

    Raises:
        ValueError: If any required credential is not set
    """
    load_dotenv()

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

    def __init__(self, entity_id: str = ENTITY_ID) -> None:
        """Initialize with entity ID."""
        self.entity_id = entity_id
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
        """
        neris_id = neris_id or self.entity_id
        logger.info(f"Listing incidents for {neris_id}")
        return self.api.list_incidents(
            neris_id_entity=neris_id,
            page_size=page_size,
            cursor=cursor,
            **kwargs,
        )

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
        neris_id_incident: str,
        *,
        neris_id: str | None = None,
    ) -> dict | None:
        """Fetch a single incident by its NERIS ID.

        Args:
            neris_id_incident: Full incident NERIS ID (e.g. FD53055879|26SJ0020|1770457554)
            neris_id: Entity ID (defaults to configured entity)

        Returns:
            Incident dict, or None if not found
        """
        neris_id = neris_id or self.entity_id
        # The API has no single-incident GET; filter the list by incident_number
        # The incident_number is the middle segment of the compound NERIS ID
        parts = neris_id_incident.split("|")
        if len(parts) != 3:
            logger.error(f"Invalid incident NERIS ID format: {neris_id_incident}")
            return None

        incident_number = parts[1]
        result = self.api.list_incidents(
            neris_id_entity=neris_id,
            incident_number=incident_number,
            page_size=1,
        )
        incidents = result.get("incidents", [])
        if not incidents:
            logger.warning(f"Incident not found: {neris_id_incident}")
            return None
        return incidents[0]

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
