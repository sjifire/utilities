"""MCP tools for personnel lookup.

Exposes minimal personnel data (names + emails only) from Entra ID.
Group membership is never exposed -- used internally for access control only.
"""

import logging

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from sjifire.core.msgraph_client import get_graph_client
from sjifire.mcp.auth import get_current_user

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
        top=200,
    )
    config = RequestConfiguration(query_parameters=query_params)
    result = await client.users.get(request_configuration=config)

    personnel = []
    if result and result.value:
        for user in result.value:
            email = user.mail or user.user_principal_name or ""
            if email and not email.startswith("svc-") and not email.startswith("api@"):
                personnel.append(
                    {
                        "name": user.display_name or "",
                        "email": email.lower(),
                    }
                )

    # Sort client-side since Graph API doesn't support orderby with filter
    personnel.sort(key=lambda p: p["name"])

    logger.info("Retrieved %d personnel", len(personnel))
    return personnel
