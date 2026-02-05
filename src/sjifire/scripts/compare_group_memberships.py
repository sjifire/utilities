"""Compare expected group memberships (from Aladtec) vs actual (in Entra/Exchange)."""

import argparse
import asyncio
import logging
from collections import defaultdict

from sjifire.aladtec.member_scraper import AladtecMemberScraper
from sjifire.core.group_strategies import (
    STRATEGY_CLASSES,
    GroupStrategy,
)
from sjifire.entra.groups import EntraGroupManager
from sjifire.entra.users import EntraUserManager
from sjifire.exchange.client import ExchangeOnlineClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# All available strategies (from core.group_strategies)
STRATEGIES: dict[str, type[GroupStrategy]] = STRATEGY_CLASSES


async def compare_memberships(
    strategy_names: list[str],
    domain: str = "sjifire.org",
    verbose: bool = False,
) -> None:
    """Compare expected vs actual group memberships using sync strategies."""
    logger.info("=" * 60)
    logger.info("Group Membership Comparison")
    logger.info("=" * 60)
    logger.info(f"Strategies: {', '.join(strategy_names)}")

    # Fetch Aladtec members
    logger.info("")
    logger.info("Fetching Aladtec members...")

    try:
        with AladtecMemberScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to log in to Aladtec")
                return
            members = scraper.get_members(include_inactive=False)
    except Exception as e:
        logger.error(f"Failed to fetch Aladtec members: {e}")
        return

    logger.info(f"Found {len(members)} active members")

    # Build expected memberships from strategies
    # expected_memberships[group_email] = set of member emails
    expected_memberships: dict[str, set[str]] = defaultdict(set)
    group_display_names: dict[str, str] = {}  # email -> display name

    for strategy_name in strategy_names:
        strategy = STRATEGIES[strategy_name]()
        groups_to_sync = strategy.get_members(members)

        for group_key, group_members in groups_to_sync.items():
            config = strategy.get_config(group_key)
            email = f"{config.mail_nickname}@{domain}"
            group_display_names[email] = config.display_name

            for member in group_members:
                if member.email:
                    expected_memberships[email].add(member.email.lower())

    # Fetch Entra users for display name lookup
    logger.info("")
    logger.info("Fetching Entra ID users...")

    user_manager = EntraUserManager()
    entra_users = await user_manager.get_users(include_disabled=False)

    # Build email -> display name mapping
    email_to_name: dict[str, str] = {}
    for user in entra_users:
        if user.email:
            email_to_name[user.email.lower()] = user.display_name or user.email
        if user.upn:
            email_to_name[user.upn.lower()] = user.display_name or user.upn

    # Also add Aladtec members to name map
    for member in members:
        if member.email:
            email_to_name[member.email.lower()] = member.display_name

    # Initialize clients for fetching actual memberships
    group_manager = EntraGroupManager()
    exchange_client = ExchangeOnlineClient()

    # Fetch actual memberships
    logger.info("")
    logger.info("Fetching actual group memberships...")

    actual_memberships: dict[str, set[str]] = defaultdict(set)
    group_types: dict[str, str] = {}  # email -> "M365" or "Exchange"

    for group_email in expected_memberships:
        mail_nickname = group_email.split("@")[0]

        # Try M365 first
        try:
            m365_group = await group_manager.get_group_by_mail_nickname(mail_nickname)
            if m365_group:
                group_types[group_email] = "M365"
                member_ids = await group_manager.get_group_members(m365_group.id)
                # Convert user IDs to emails
                for user in entra_users:
                    if user.id in member_ids:
                        if user.email:
                            actual_memberships[group_email].add(user.email.lower())
                        elif user.upn:
                            actual_memberships[group_email].add(user.upn.lower())
                continue
        except Exception as e:
            logger.debug(f"Error checking M365 for {group_email}: {e}")

        # Try Exchange
        try:
            exchange_group = await exchange_client.get_distribution_group(group_email)
            if exchange_group:
                group_types[group_email] = "Exchange"
                member_emails = await exchange_client.get_distribution_group_members(group_email)
                actual_memberships[group_email] = {e.lower() for e in member_emails}
                continue
        except Exception as e:
            logger.debug(f"Error checking Exchange for {group_email}: {e}")

        # Group doesn't exist
        group_types[group_email] = "NOT FOUND"

    await exchange_client.close()

    # Compare and report
    logger.info("")
    logger.info("=" * 60)
    logger.info("COMPARISON RESULTS")
    logger.info("=" * 60)

    total_missing = 0
    total_extra = 0
    groups_with_issues = 0

    for group_email in sorted(expected_memberships.keys()):
        display_name = group_display_names.get(group_email, group_email)
        group_type = group_types.get(group_email, "UNKNOWN")
        expected = expected_memberships.get(group_email, set())
        actual = actual_memberships.get(group_email, set())

        missing = expected - actual
        extra = actual - expected

        logger.info("")
        logger.info(f"{display_name} ({group_email}):")
        logger.info(f"  Type: {group_type}")
        logger.info(f"  Expected: {len(expected)} members")
        logger.info(f"  Actual:   {len(actual)} members")

        if missing:
            logger.info(f"  MISSING ({len(missing)}):")
            for email in sorted(missing):
                name = email_to_name.get(email, email)
                logger.info(f"    - {name} ({email})")
            total_missing += len(missing)

        if extra:
            logger.info(f"  EXTRA ({len(extra)}):")
            for email in sorted(extra):
                name = email_to_name.get(email, email)
                logger.info(f"    - {name} ({email})")
            total_extra += len(extra)

        if not missing and not extra and group_type != "NOT FOUND":
            logger.info("  OK - memberships match")

        if missing or extra or group_type == "NOT FOUND":
            groups_with_issues += 1

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("")
    logger.info(f"Groups analyzed: {len(expected_memberships)}")
    logger.info(f"Groups with discrepancies: {groups_with_issues}")
    logger.info(f"Total missing memberships: {total_missing}")
    logger.info(f"Total extra memberships: {total_extra}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Compare expected group memberships (from Aladtec strategies) "
        "vs actual (in Entra/Exchange)",
    )
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGIES.keys()),
        action="append",
        dest="strategies",
        help="Strategy to compare (can be specified multiple times). "
        f"Available: {', '.join(STRATEGIES.keys())}",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Compare all available strategies",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show additional details",
    )
    args = parser.parse_args()

    # Determine which strategies to run
    strategies: list[str] = []
    if args.all:
        strategies = list(STRATEGIES.keys())
    elif args.strategies:
        strategies = args.strategies
    else:
        parser.error("Specify --all or at least one --strategy")

    asyncio.run(compare_memberships(strategy_names=strategies, verbose=args.verbose))


if __name__ == "__main__":
    main()
