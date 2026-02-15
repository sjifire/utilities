"""Tools for personnel lookup.

Exposes minimal personnel data (names + emails only) from Entra ID.
Group membership is never exposed -- used internally for access control only.
"""

import logging

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from sjifire.core.msgraph_client import get_graph_client
from sjifire.ops.auth import get_current_user

logger = logging.getLogger(__name__)


async def get_personnel() -> list[dict[str, str]]:
    """Get a list of active SJI Fire personnel.

    Returns names and email addresses only. Use this to look up
    people for crew assignment on incidents.

    Returns:
        List of {"name": "...", "email": "..."} for each active user
    """
    user = get_current_user()
    logger.info("Personnel lookup requested by %s", user.email)

    client = get_graph_client()
    query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
        select=["displayName", "mail", "userPrincipalName"],
        filter="accountEnabled eq true",
        top=999,
    )
    config = RequestConfiguration(query_parameters=query_params)
    result = await client.users.get(request_configuration=config)

    personnel = []

    def _collect(page):
        if not page or not page.value:
            return
        for u in page.value:
            email = u.mail or u.user_principal_name or ""
            if email and not email.startswith("svc-") and not email.startswith("api@"):
                personnel.append(
                    {
                        "name": u.display_name or "",
                        "email": email.lower(),
                    }
                )

    _collect(result)
    while result and result.odata_next_link:
        result = await client.users.with_url(result.odata_next_link).get()
        _collect(result)

    # Sort client-side since Graph API doesn't support orderby with filter
    personnel.sort(key=lambda p: p["name"])

    logger.info("Retrieved %d personnel", len(personnel))
    return personnel


async def get_operational_personnel() -> list[dict[str, str]]:
    """Get personnel in operational roles (officers + field positions).

    Filters to users who have scheduling positions in extensionAttribute3,
    meaning they're in the Aladtec scheduling system and respond to calls.
    Lighter than get_personnel() for pre-loading into system prompts.

    Returns:
        List of {"name": "...", "email": "..."} for each operational user
    """
    client = get_graph_client()
    query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
        select=["displayName", "mail", "userPrincipalName", "onPremisesExtensionAttributes"],
        filter="accountEnabled eq true",
        top=999,
    )
    config = RequestConfiguration(query_parameters=query_params)
    result = await client.users.get(request_configuration=config)

    personnel = []

    def _collect(page):
        if not page or not page.value:
            return
        for u in page.value:
            email = u.mail or u.user_principal_name or ""
            if email and not email.startswith("svc-") and not email.startswith("api@"):
                ext = u.on_premises_extension_attributes
                positions = ext.extension_attribute3 if ext else None
                if positions:  # Has scheduling positions = operational
                    personnel.append(
                        {
                            "name": u.display_name or "",
                            "email": email.lower(),
                        }
                    )

    _collect(result)
    while result and result.odata_next_link:
        result = await client.users.with_url(result.odata_next_link).get()
        _collect(result)

    personnel.sort(key=lambda p: p["name"])
    logger.info("Retrieved %d operational personnel", len(personnel))
    return personnel
