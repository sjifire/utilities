"""Compare expected M365 group memberships (from Aladtec) vs actual (in Entra)."""

import argparse
import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path

from sjifire.aladtec.scraper import AladtecScraper
from sjifire.entra.groups import EntraGroupManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "group_mappings.json"


async def compare_memberships(config_path: Path, verbose: bool = False) -> None:
    """Compare expected vs actual M365 group memberships."""
    logger.info("=" * 60)
    logger.info("M365 Group Membership Comparison")
    logger.info("=" * 60)

    # Load config
    with config_path.open() as f:
        config = json.load(f)

    ms_365_group_ids = config.get("ms_365_group_ids", {})
    position_mappings = config.get("position_mappings", {})
    work_group_mappings = config.get("work_group_mappings", {})
    conditional_mappings = config.get("conditional_mappings", {})

    # Build reverse lookup: group_name -> group_id
    group_name_to_id = ms_365_group_ids

    # Fetch Aladtec members
    logger.info("")
    logger.info("Fetching Aladtec members...")

    try:
        with AladtecScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to log in to Aladtec")
                return
            members = scraper.get_members(include_inactive=False)
    except Exception as e:
        logger.error(f"Failed to fetch Aladtec members: {e}")
        return

    logger.info(f"Found {len(members)} active members")

    # Build expected memberships based on positions and work groups
    # expected_memberships[group_name] = set of member display names
    expected_memberships: dict[str, set[str]] = defaultdict(set)

    # Also track member email for Entra matching
    member_email_map: dict[str, str] = {}  # display_name -> email

    for member in members:
        if member.email:
            member_email_map[member.display_name] = member.email.lower()

        # Check position mappings
        if member.positions:
            for pos in member.positions:
                if pos in position_mappings:
                    for group_name in position_mappings[pos].get("ms_365_groups", []):
                        expected_memberships[group_name].add(member.display_name)
        elif member.position and member.position in position_mappings:
            for group_name in position_mappings[member.position].get("ms_365_groups", []):
                expected_memberships[group_name].add(member.display_name)

        # Check work group mappings
        if member.work_group and member.work_group in work_group_mappings:
            for group_name in work_group_mappings[member.work_group].get("ms_365_groups", []):
                expected_memberships[group_name].add(member.display_name)

        # Check conditional mappings
        positions = member.positions or []
        for group_name, rules in conditional_mappings.items():
            requires = rules.get("requires_positions", [])
            excludes = rules.get("excludes_positions", [])

            # Must have all required positions
            has_required = all(pos in positions for pos in requires)
            # Must not have any excluded positions
            has_excluded = any(pos in positions for pos in excludes)

            if has_required and not has_excluded:
                expected_memberships[group_name].add(member.display_name)

    # Fetch Entra group memberships
    logger.info("")
    logger.info("Fetching Entra ID group memberships...")

    group_manager = EntraGroupManager()

    # Get all users to build ID -> name mapping
    from sjifire.entra.users import EntraUserManager

    user_manager = EntraUserManager()
    entra_users = await user_manager.get_users(include_disabled=False)

    # Build user ID -> display name mapping
    user_id_to_name: dict[str, str] = {}
    user_email_to_name: dict[str, str] = {}
    for user in entra_users:
        if user.id:
            user_id_to_name[user.id] = user.display_name or ""
        if user.email:
            user_email_to_name[user.email.lower()] = user.display_name or ""
        if user.upn:
            user_email_to_name[user.upn.lower()] = user.display_name or ""

    # Fetch actual memberships for each M365 group
    actual_memberships: dict[str, set[str]] = defaultdict(set)

    for group_name, group_id in group_name_to_id.items():
        if group_id == "TODO":
            continue

        try:
            member_ids = await group_manager.get_group_members(group_id)
            for member_id in member_ids:
                if member_id in user_id_to_name:
                    actual_memberships[group_name].add(user_id_to_name[member_id])
        except Exception as e:
            logger.error(f"Failed to get members for {group_name}: {e}")

    # Compare and report
    logger.info("")
    logger.info("=" * 60)
    logger.info("COMPARISON RESULTS")
    logger.info("=" * 60)

    all_groups = set(expected_memberships.keys()) | set(actual_memberships.keys())

    for group_name in sorted(all_groups):
        expected = expected_memberships.get(group_name, set())
        actual = actual_memberships.get(group_name, set())

        # Match by name (case-insensitive)
        expected_lower = {name.lower(): name for name in expected}
        actual_lower = {name.lower(): name for name in actual}

        missing = set()  # In expected but not in actual
        extra = set()  # In actual but not in expected

        for name_lower, name in expected_lower.items():
            if name_lower not in actual_lower:
                missing.add(name)

        for name_lower, name in actual_lower.items():
            if name_lower not in expected_lower:
                extra.add(name)

        logger.info("")
        logger.info(f"{group_name}:")
        logger.info(f"  Expected: {len(expected)} members")
        logger.info(f"  Actual:   {len(actual)} members")

        if missing:
            logger.info(f"  MISSING ({len(missing)}):")
            for name in sorted(missing):
                logger.info(f"    - {name}")

        if extra:
            logger.info(f"  EXTRA ({len(extra)}):")
            for name in sorted(extra):
                logger.info(f"    - {name}")

        if not missing and not extra:
            logger.info("  OK - memberships match")

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    total_missing = 0
    total_extra = 0
    groups_with_issues = 0

    for group_name in all_groups:
        expected = expected_memberships.get(group_name, set())
        actual = actual_memberships.get(group_name, set())

        expected_lower = {name.lower() for name in expected}
        actual_lower = {name.lower() for name in actual}

        missing = len(expected_lower - actual_lower)
        extra = len(actual_lower - expected_lower)

        total_missing += missing
        total_extra += extra
        if missing or extra:
            groups_with_issues += 1

    logger.info("")
    logger.info(f"Groups analyzed: {len(all_groups)}")
    logger.info(f"Groups with discrepancies: {groups_with_issues}")
    logger.info(f"Total missing memberships: {total_missing}")
    logger.info(f"Total extra memberships: {total_extra}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Compare expected M365 group memberships (from Aladtec) vs actual (in Entra)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to group_mappings.json (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show additional details",
    )
    args = parser.parse_args()

    asyncio.run(compare_memberships(config_path=args.config, verbose=args.verbose))


if __name__ == "__main__":
    main()
