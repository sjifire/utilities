"""CLI script to audit Aladtec members and compare with Entra ID."""

import argparse
import asyncio
import logging
import sys

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from sjifire.aladtec.member_scraper import AladtecMemberScraper
from sjifire.aladtec.models import Member
from sjifire.core.msgraph_client import get_graph_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def check_missing_data(members: list[Member]) -> dict[str, list[Member]]:
    """Check for members with missing required data.

    Args:
        members: List of Aladtec members

    Returns:
        Dict mapping issue type to list of affected members
    """
    issues: dict[str, list[Member]] = {
        "no_positions": [],
        "no_sjifire_email": [],
        "no_employee_id": [],
        "inactive": [],
    }

    for member in members:
        # Check for missing positions
        if not member.positions:
            issues["no_positions"].append(member)

        # Check for missing sjifire.org email
        if not member.email or not member.email.endswith("@sjifire.org"):
            issues["no_sjifire_email"].append(member)

        # Check for missing employee ID
        if not member.employee_id:
            issues["no_employee_id"].append(member)

        # Check for inactive status
        if not member.is_active:
            issues["inactive"].append(member)

    return issues


async def get_entra_users() -> list[dict]:
    """Fetch all users from Entra ID.

    Returns:
        List of user dicts with relevant fields
    """
    client = get_graph_client()

    users = []
    # Get users with relevant properties
    query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
        select=[
            "id",
            "displayName",
            "givenName",
            "surname",
            "mail",
            "userPrincipalName",
            "employeeId",
        ]
    )
    config = RequestConfiguration(query_parameters=query_params)
    result = await client.users.get(request_configuration=config)

    def _user_to_dict(user):
        return {
            "id": user.id,
            "display_name": user.display_name,
            "first_name": user.given_name,
            "last_name": user.surname,
            "email": user.mail,
            "upn": user.user_principal_name,
            "employee_id": user.employee_id,
        }

    if result and result.value:
        users.extend(_user_to_dict(user) for user in result.value)

    # Handle pagination
    while result and result.odata_next_link:
        result = await client.users.with_url(result.odata_next_link).get()
        if result and result.value:
            users.extend(_user_to_dict(user) for user in result.value)

    return users


def is_shared_mailbox(user: dict) -> bool:
    """Check if an Entra user is likely a shared/functional mailbox.

    Shared mailboxes typically have no givenName and no surname set,
    while real user accounts always have both populated.

    Args:
        user: User dict from Entra ID

    Returns:
        True if likely a shared mailbox
    """
    # If both givenName and surname are missing, it's likely a shared mailbox
    return not user.get("first_name") and not user.get("last_name")


def compare_systems(
    aladtec_members: list[Member], entra_users: list[dict]
) -> tuple[list[Member], list[dict]]:
    """Compare Aladtec members with Entra ID users.

    Matches by:
    1. sjifire.org email address
    2. Employee ID (if available)
    3. First + Last name (fallback)

    Filters out shared mailboxes from Entra (accounts without givenName/surname).

    Args:
        aladtec_members: Members from Aladtec
        entra_users: Users from Entra ID

    Returns:
        Tuple of (members_not_in_entra, entra_users_not_in_aladtec)
    """
    # Build lookup sets for Entra users
    entra_emails = {u["email"].lower() for u in entra_users if u.get("email")}
    entra_upns = {u["upn"].lower() for u in entra_users if u.get("upn")}
    entra_employee_ids = {u["employee_id"] for u in entra_users if u.get("employee_id")}
    entra_names = {
        f"{u['first_name']} {u['last_name']}".lower()
        for u in entra_users
        if u.get("first_name") and u.get("last_name")
    }

    # Build lookup sets for Aladtec members
    aladtec_emails = {m.email.lower() for m in aladtec_members if m.email}
    aladtec_employee_ids = {m.employee_id for m in aladtec_members if m.employee_id}
    aladtec_names = {m.display_name.lower() for m in aladtec_members}

    # Find Aladtec members not in Entra
    members_not_in_entra = []
    for member in aladtec_members:
        found = False

        # Match by sjifire.org email
        if member.email and (
            member.email.lower() in entra_emails or member.email.lower() in entra_upns
        ):
            found = True

        # Match by employee ID
        if not found and member.employee_id and member.employee_id in entra_employee_ids:
            found = True

        # Match by name (fallback)
        if not found and member.display_name.lower() in entra_names:
            found = True

        if not found:
            members_not_in_entra.append(member)

    # Find Entra users not in Aladtec (only sjifire.org accounts, exclude shared mailboxes)
    entra_not_in_aladtec = []
    for user in entra_users:
        # Only check sjifire.org accounts
        upn = user.get("upn", "")
        if not upn or not upn.endswith("@sjifire.org"):
            continue

        # Skip shared mailboxes (no givenName/surname)
        if is_shared_mailbox(user):
            continue

        found = False

        # Match by email
        if user.get("email") and user["email"].lower() in aladtec_emails:
            found = True

        # Match by employee ID
        if not found and user.get("employee_id") and user["employee_id"] in aladtec_employee_ids:
            found = True

        # Match by name
        if not found:
            name = f"{user.get('first_name', '')} {user.get('last_name', '')}".lower().strip()
            if name and name in aladtec_names:
                found = True

        if not found:
            entra_not_in_aladtec.append(user)

    return members_not_in_entra, entra_not_in_aladtec


def print_section(title: str, items: list, format_func) -> None:
    """Print a section with items.

    Note: This CLI tool intentionally outputs member data for admin review.
    """
    print(f"\n{'=' * 60}")
    print(f"{title} ({len(items)})")
    print("=" * 60)
    if items:
        for item in items:
            print(f"  - {format_func(item)}")  # lgtm[py/clear-text-logging-sensitive-data]
    else:
        print("  (none)")


async def run_audit(skip_entra: bool = False) -> int:
    """Run the audit.

    Args:
        skip_entra: Skip Entra ID comparison

    Returns:
        Exit code
    """
    # Fetch Aladtec members (including inactive)
    logger.info("Fetching members from Aladtec...")
    try:
        with AladtecMemberScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to log in to Aladtec")
                return 1
            members = scraper.get_members(include_inactive=True)
    except Exception as e:
        logger.error(f"Failed to fetch Aladtec members: {e}")
        return 1

    if not members:
        logger.error("No members found in Aladtec")
        return 1

    logger.info(f"Found {len(members)} members in Aladtec")

    # Check for missing data
    print("\n" + "=" * 60)
    print("ALADTEC DATA QUALITY AUDIT")
    print("=" * 60)

    issues = check_missing_data(members)

    print_section(
        "Members without positions",
        issues["no_positions"],
        lambda m: f"{m.display_name}",
    )

    print_section(
        "Members without @sjifire.org email",
        issues["no_sjifire_email"],
        lambda m: f"{m.display_name} ({m.email or 'no email'})",
    )

    print_section(
        "Members without employee ID",
        issues["no_employee_id"],
        lambda m: f"{m.display_name}",
    )

    print_section(
        "Inactive members (should deactivate in Entra ID)",
        issues["inactive"],
        lambda m: f"{m.display_name} ({m.email or 'no email'}) - status: {m.status}",
    )

    # Entra ID comparison
    if not skip_entra:
        print("\n" + "=" * 60)
        print("ENTRA ID COMPARISON")
        print("=" * 60)

        logger.info("Fetching users from Entra ID...")
        try:
            entra_users = await get_entra_users()
            logger.info(f"Found {len(entra_users)} users in Entra ID")

            # Filter to just sjifire.org accounts for reporting
            sjifire_users = [u for u in entra_users if u.get("upn", "").endswith("@sjifire.org")]
            logger.info(f"Found {len(sjifire_users)} @sjifire.org accounts in Entra ID")

            members_not_in_entra, entra_not_in_aladtec = compare_systems(members, entra_users)

            print_section(
                "Aladtec members NOT in Entra ID",
                members_not_in_entra,
                lambda m: f"{m.display_name} ({m.email or 'no email'})",
            )

            print_section(
                "Entra ID users NOT in Aladtec (@sjifire.org only)",
                entra_not_in_aladtec,
                lambda u: f"{u['display_name']} ({u['upn']})",
            )

            # Find Entra users that match inactive Aladtec members (for future deactivation)
            # Match by email OR by name since inactive members may not have emails
            inactive_members = issues["inactive"]
            if inactive_members:
                inactive_emails = {m.email.lower() for m in inactive_members if m.email}
                inactive_names = {m.display_name.lower() for m in inactive_members}

                entra_to_deactivate = []
                for u in entra_users:
                    # Only check sjifire.org accounts
                    upn = u.get("upn", "")
                    if not upn or not upn.endswith("@sjifire.org"):
                        continue

                    # Match by email
                    if u.get("email") and u["email"].lower() in inactive_emails:
                        entra_to_deactivate.append(u)
                        continue

                    # Match by name
                    entra_name = f"{u.get('first_name', '')} {u.get('last_name', '')}".lower()
                    if entra_name.strip() and entra_name in inactive_names:
                        entra_to_deactivate.append(u)

                print_section(
                    "Entra ID users to DEACTIVATE (inactive in Aladtec)",
                    entra_to_deactivate,
                    lambda u: f"{u['display_name']} ({u['upn']})",
                )

        except Exception as e:
            logger.error(f"Failed to fetch Entra ID users: {e}")
            print("\n  (Entra ID comparison skipped due to error)")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total Aladtec members: {len(members)}")
    print(f"  Active members: {len([m for m in members if m.is_active])}")
    print(f"  Inactive members: {len(issues['inactive'])}")
    print(f"  Missing positions: {len(issues['no_positions'])}")
    print(f"  Missing @sjifire.org email: {len(issues['no_sjifire_email'])}")
    print(f"  Missing employee ID: {len(issues['no_employee_id'])}")

    return 0


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Audit Aladtec members and compare with Entra ID",
    )
    parser.add_argument(
        "--skip-entra",
        action="store_true",
        help="Skip Entra ID comparison (only run Aladtec data quality checks)",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(run_audit(skip_entra=args.skip_entra))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
