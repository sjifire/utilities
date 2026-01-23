"""CLI script to sync Aladtec members to Entra ID."""

import argparse
import asyncio
import json
import logging
import sys

from sjifire.aladtec.entra_sync import EntraSync
from sjifire.aladtec.scraper import AladtecScraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def run_sync(dry_run: bool = False, output_json: bool = False) -> int:
    """Run the Aladtec to Entra sync.

    Args:
        dry_run: If True, don't make changes
        output_json: If True, output results as JSON

    Returns:
        Exit code
    """
    logger.info("=" * 50)
    logger.info("Aladtec to Entra ID Sync")
    logger.info("=" * 50)

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

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

    # Sync to Entra ID
    logger.info("")
    logger.info("Syncing to Entra ID...")

    try:
        sync = EntraSync()
        results = await sync.sync_members(members, dry_run=dry_run)

    except Exception as e:
        logger.error(f"Failed to sync to Entra ID: {e}")
        return 1

    # Output results
    logger.info("")
    logger.info("=" * 50)
    logger.info("Results")
    logger.info("=" * 50)

    if output_json:
        print(json.dumps(results, indent=2))
    else:
        logger.info(f"Created: {len(results['created'])}")
        logger.info(f"Updated: {len(results['updated'])}")
        logger.info(f"Skipped: {len(results['skipped'])}")
        logger.info(f"Errors:  {len(results['errors'])}")

        if results["errors"]:
            logger.info("")
            logger.info("Errors:")
            for error in results["errors"]:
                logger.info(f"  - {error['member']}: {error['error']}")

    return 0 if not results["errors"] else 1


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sync Aladtec members to Entra ID"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(run_sync(
        dry_run=args.dry_run,
        output_json=args.output_json,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
