"""CLI script to import Aladtec members to Entra ID."""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict

from sjifire.aladtec.scraper import AladtecScraper
from sjifire.entra.aladtec_import import AladtecImporter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def run_import(
    dry_run: bool = False,
    disable_inactive: bool = False,
    output_json: bool = False,
) -> int:
    """Run the Aladtec to Entra import.

    Args:
        dry_run: If True, don't make changes
        disable_inactive: If True, disable accounts for inactive members
        output_json: If True, output results as JSON

    Returns:
        Exit code
    """
    logger.info("=" * 50)
    logger.info("Aladtec to Entra ID Import")
    logger.info("=" * 50)

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

    # Scrape members from Aladtec (including inactive if disable_inactive is set)
    logger.info("")
    logger.info("Fetching members from Aladtec...")

    try:
        with AladtecScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to log in to Aladtec")
                return 1

            members = scraper.get_members(include_inactive=disable_inactive)

        if not members:
            logger.error("No members found in Aladtec")
            return 1

        active_count = sum(1 for m in members if m.is_active)
        inactive_count = len(members) - active_count
        logger.info(
            f"Found {len(members)} members ({active_count} active, {inactive_count} inactive)"
        )

    except Exception as e:
        logger.error(f"Failed to fetch Aladtec members: {e}")
        return 1

    # Import to Entra ID
    logger.info("")
    logger.info("Importing to Entra ID...")

    try:
        importer = AladtecImporter()
        result = await importer.import_members(
            members,
            dry_run=dry_run,
            disable_inactive=disable_inactive,
        )

    except Exception as e:
        logger.error(f"Failed to import to Entra ID: {e}")
        return 1

    # Output results
    logger.info("")
    logger.info("=" * 50)
    logger.info("Results")
    logger.info("=" * 50)

    if output_json:
        print(json.dumps(asdict(result), indent=2))
    else:
        logger.info(f"Created:  {len(result.created)}")
        logger.info(f"Updated:  {len(result.updated)}")
        logger.info(f"Disabled: {len(result.disabled)}")
        logger.info(f"Skipped:  {len(result.skipped)}")
        logger.info(f"Errors:   {len(result.errors)}")

        if result.errors:
            logger.info("")
            logger.info("Errors:")
            for error in result.errors:
                logger.info(f"  - {error['member']}: {error['error']}")

    return 0 if not result.errors else 1


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Import Aladtec members to Entra ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--disable-inactive",
        action="store_true",
        help="Disable Entra accounts for inactive Aladtec members",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(
        run_import(
            dry_run=args.dry_run,
            disable_inactive=args.disable_inactive,
            output_json=args.output_json,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
