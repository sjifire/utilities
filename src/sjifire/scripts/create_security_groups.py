"""CLI script to create/delete security groups from the mapping config."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sjifire.entra.groups import EntraGroupManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Default config path
DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "group_mappings.json"


async def run_create_groups(
    config_path: Path,
    dry_run: bool = False,
) -> int:
    """Create security groups from the mapping config.

    Args:
        config_path: Path to the group_mappings.json config file
        dry_run: If True, don't create groups, just show what would be done

    Returns:
        Exit code
    """
    logger.info("=" * 50)
    logger.info("Create Security Groups from Config")
    logger.info("=" * 50)

    if dry_run:
        logger.info("DRY RUN - no groups will be created")

    logger.info(f"Config file: {config_path}")
    logger.info("")

    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        return 1

    try:
        group_manager = EntraGroupManager()
        results = await group_manager.create_security_groups_from_config(
            config_path=config_path,
            dry_run=dry_run,
        )

    except Exception as e:
        logger.error(f"Failed to create security groups: {e}")
        return 1

    # Output results
    logger.info("")
    logger.info("=" * 50)
    logger.info("Results")
    logger.info("=" * 50)

    created_count = 0
    existing_count = 0
    failed_count = 0

    for group_name, group_id in results.items():
        if group_id and not dry_run:
            if group_id.startswith("TODO:"):
                failed_count += 1
            else:
                # Check if this was existing or newly created by looking at log output
                # For simplicity, just count as processed
                created_count += 1
        elif group_id:
            existing_count += 1
        else:
            if dry_run:
                logger.info(f"  Would create: {group_name}")
            else:
                failed_count += 1

    if not dry_run:
        logger.info(f"Processed: {len(results)} groups")
        logger.info(f"  - Already existed: {existing_count}")
        logger.info(f"  - Created: {created_count - existing_count}")
        if failed_count:
            logger.info(f"  - Failed: {failed_count}")

    return 0


async def run_delete_groups(
    config_path: Path,
    dry_run: bool = False,
) -> int:
    """Delete security groups defined in the mapping config.

    Args:
        config_path: Path to the group_mappings.json config file
        dry_run: If True, don't delete groups, just show what would be done

    Returns:
        Exit code
    """
    logger.info("=" * 50)
    logger.info("Delete Security Groups from Config")
    logger.info("=" * 50)

    if dry_run:
        logger.info("DRY RUN - no groups will be deleted")

    logger.info(f"Config file: {config_path}")
    logger.info("")

    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        return 1

    try:
        group_manager = EntraGroupManager()
        results = await group_manager.delete_security_groups_from_config(
            config_path=config_path,
            dry_run=dry_run,
        )

    except Exception as e:
        logger.error(f"Failed to delete security groups: {e}")
        return 1

    # Output results
    logger.info("")
    logger.info("=" * 50)
    logger.info("Results")
    logger.info("=" * 50)

    success_count = sum(1 for success in results.values() if success)
    failed_count = len(results) - success_count

    logger.info(f"Processed: {len(results)} groups")
    logger.info(f"  - Deleted/Not found: {success_count}")
    if failed_count:
        logger.info(f"  - Failed: {failed_count}")

    return 0 if failed_count == 0 else 1


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Create or delete security groups defined in the mapping config",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to group_mappings.json config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete security groups instead of creating them",
    )

    args = parser.parse_args()

    if args.delete:
        exit_code = asyncio.run(
            run_delete_groups(
                config_path=args.config,
                dry_run=args.dry_run,
            )
        )
    else:
        exit_code = asyncio.run(
            run_create_groups(
                config_path=args.config,
                dry_run=args.dry_run,
            )
        )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
