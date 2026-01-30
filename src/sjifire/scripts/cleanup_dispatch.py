"""Manual cleanup script for dispatch email archive."""

import argparse
import asyncio
import logging
import sys

from sjifire.core.config import load_dispatch_config
from sjifire.core.graph_client import get_graph_client
from sjifire.dispatch.cleanup import cleanup_old_emails

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def run_cleanup(dry_run: bool = False) -> int:
    """Run the archive cleanup process.

    Args:
        dry_run: If True, only show what would be deleted

    Returns:
        Exit code (0 for success)
    """
    try:
        config = load_dispatch_config()
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return 1

    client = get_graph_client()

    logger.info("=" * 50)
    logger.info("Dispatch Archive Cleanup")
    logger.info("=" * 50)
    logger.info(f"Retention period: {config.retention_days} days")
    logger.info(f"Archive folder: {config.archive_folder}")

    if dry_run:
        logger.info("")
        logger.info("DRY RUN - no emails will be deleted")
        logger.info("(Remove --dry-run to actually delete)")
        return 0

    logger.info("")
    result = await cleanup_old_emails(client, config)

    logger.info("")
    logger.info(f"Cleanup complete: {result['deleted_count']} emails deleted")
    logger.info(f"Cutoff date: {result['cutoff_date']}")

    return 0


def main():
    """CLI entry point for dispatch-cleanup."""
    parser = argparse.ArgumentParser(description="Clean up old emails from dispatch archive")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting",
    )

    args = parser.parse_args()
    exit_code = asyncio.run(run_cleanup(dry_run=args.dry_run))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
