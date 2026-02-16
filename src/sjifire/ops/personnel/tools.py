"""Tools for personnel lookup.

Exposes minimal personnel data (names + emails only) from Entra ID.
Group membership is never exposed -- used internally for access control only.
"""

import logging

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from sjifire.core.config import get_domain
from sjifire.core.msgraph_client import get_graph_client
from sjifire.ops.auth import get_current_user

logger = logging.getLogger(__name__)


async def _fetch_all_users(
    select: list[str],
) -> list:
    """Fetch all active users from Graph API with given select fields."""
    client = get_graph_client()
    query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
        select=select,
        filter="accountEnabled eq true",
        top=999,
    )
    config = RequestConfiguration(query_parameters=query_params)
    result = await client.users.get(request_configuration=config)

    users: list = []

    def _collect(page):
        if page and page.value:
            users.extend(page.value)

    _collect(result)
    while result and result.odata_next_link:
        result = await client.users.with_url(result.odata_next_link).get()
        _collect(result)

    return users


def _is_person(email: str, domain: str) -> bool:
    """Check if an email belongs to a real person (not a service/group account)."""
    if not email or not email.endswith(f"@{domain}"):
        return False
    local = email.split("@")[0]
    return not local.startswith(("svc-", "api", "noreply"))


async def get_personnel() -> list[dict[str, str]]:
    """Get a list of active SJI Fire personnel.

    Returns names and email addresses only. Use this to look up
    people for crew assignment on incidents. Only returns real people
    with @domain emails (no groups, shared mailboxes, or guests).

    Returns:
        List of {"name": "...", "email": "..."} for each active user
    """
    user = get_current_user()
    logger.info("Personnel lookup requested by %s", user.email)

    domain = get_domain()
    users = await _fetch_all_users(["displayName", "mail", "userPrincipalName"])

    personnel = []
    for u in users:
        email = (u.mail or u.user_principal_name or "").lower()
        if _is_person(email, domain):
            personnel.append({"name": u.display_name or "", "email": email})

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
    domain = get_domain()
    users = await _fetch_all_users(
        ["displayName", "mail", "userPrincipalName", "onPremisesExtensionAttributes"],
    )

    personnel = []
    for u in users:
        email = (u.mail or u.user_principal_name or "").lower()
        if not _is_person(email, domain):
            continue
        ext = u.on_premises_extension_attributes
        positions = ext.extension_attribute3 if ext else None
        if positions:  # Has scheduling positions = operational
            personnel.append({"name": u.display_name or "", "email": email})

    personnel.sort(key=lambda p: p["name"])
    logger.info("Retrieved %d operational personnel", len(personnel))
    return personnel
