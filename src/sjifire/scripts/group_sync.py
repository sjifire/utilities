"""CLI script to sync Aladtec-based M365 groups."""

import argparse
import asyncio
import logging
import sys

from sjifire.aladtec.scraper import AladtecScraper
from sjifire.entra.group_sync import (
    FullSyncResult,
    GroupSyncManager,
    PositionGroupStrategy,
    StationGroupStrategy,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Silence verbose HTTP request logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

# Available sync strategies
STRATEGIES = {
    "stations": StationGroupStrategy,
    "positions": PositionGroupStrategy,
}


def print_result(result: FullSyncResult, dry_run: bool = False) -> None:
    """Print sync results in a readable format."""
    logger.info("")
    logger.info("=" * 50)
    logger.info(f"Results: {result.group_type}")
    logger.info("=" * 50)

    for group_result in result.groups:
        logger.info(f"\n{group_result.group_name}:")

        if group_result.created:
            action = "Would create" if dry_run else "Created"
            logger.info(f"  Group: {action}")
        else:
            logger.info(f"  Group: Exists (ID: {group_result.group_id})")

        if group_result.members_added:
            action = "Would add" if dry_run else "Added"
            logger.info(f"  {action}: {', '.join(group_result.members_added)}")

        if group_result.members_removed:
            action = "Would remove" if dry_run else "Removed"
            logger.info(f"  {action}: {', '.join(group_result.members_removed)}")

        if group_result.errors:
            for error in group_result.errors:
                logger.error(f"  Error: {error}")

        if not group_result.has_changes and not group_result.errors:
            logger.info("  No changes needed")

    logger.info("")
    logger.info("-" * 50)
    logger.info("Summary:")
    logger.info(f"  Groups processed: {len(result.groups)}")
    logger.info(f"  Groups created: {result.total_created}")
    logger.info(f"  Members added: {result.total_added}")
    logger.info(f"  Members removed: {result.total_removed}")
    if result.total_errors:
        logger.info(f"  Errors: {result.total_errors}")


async def run_sync(
    strategies: list[str],
    dry_run: bool = False,
) -> int:
    """Run group sync for specified strategies.

    Args:
        strategies: List of strategy names to run
        dry_run: If True, don't make changes

    Returns:
        Exit code
    """
    logger.info("=" * 50)
    logger.info("Group Sync")
    logger.info("=" * 50)

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

    logger.info(f"Strategies: {', '.join(strategies)}")

    # Scrape members from Aladtec
    logger.info("")
    logger.info("Fetching members from Aladtec...")

    try:
        with AladtecScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to log in to Aladtec")
                return 1

            members = scraper.get_members()

        if not members:
            logger.error("No members found in Aladtec")
            return 1

        logger.info(f"Found {len(members)} members")

    except Exception as e:
        logger.error(f"Failed to fetch Aladtec members: {e}")
        return 1

    # Run sync for each strategy
    manager = GroupSyncManager()
    total_errors = 0

    # Backup all groups once before making any changes
    if not dry_run:
        logger.info("")
        logger.info("Creating backup of all M365 groups...")
        backup_path = await manager.backup_all_groups()
        if backup_path:
            logger.info(f"Backup created: {backup_path}")
        else:
            logger.warning("Backup failed, continuing anyway...")

    for strategy_name in strategies:
        logger.info("")
        logger.info(f"Running {strategy_name} sync...")

        strategy_class = STRATEGIES[strategy_name]
        strategy = strategy_class()

        try:
            result = await manager.sync(
                strategy=strategy,
                members=members,
                dry_run=dry_run,
            )
            print_result(result, dry_run=dry_run)
            total_errors += result.total_errors

        except Exception as e:
            logger.error(f"Failed to sync {strategy_name}: {e}")
            total_errors += 1

    return 0 if total_errors == 0 else 1


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sync M365 groups from Aladtec data",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes (skips backup)",
    )
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGIES.keys()),
        action="append",
        dest="strategies",
        help="Sync strategy to run (can be specified multiple times). "
        f"Available: {', '.join(STRATEGIES.keys())}",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all available sync strategies",
    )

    args = parser.parse_args()

    # Determine which strategies to run
    if args.all:
        strategies = list(STRATEGIES.keys())
    elif args.strategies:
        strategies = args.strategies
    else:
        # Default to stations only
        strategies = ["stations"]

    exit_code = asyncio.run(
        run_sync(
            strategies=strategies,
            dry_run=args.dry_run,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
