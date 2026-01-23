"""Analyze Aladtec positions and Entra groups for mapping suggestions."""

import argparse
import asyncio
import logging
import re
from collections import defaultdict

from sjifire.aladtec.scraper import AladtecScraper
from sjifire.entra.groups import EntraGroup, EntraGroupManager, GroupType

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def normalize_for_comparison(text: str) -> str:
    """Normalize text for fuzzy comparison."""
    # Lowercase, remove special chars, collapse whitespace
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def find_potential_matches(
    position: str,
    groups: list[EntraGroup],
) -> list[tuple[EntraGroup, str]]:
    """Find potential group matches for a position.

    Args:
        position: Aladtec position name
        groups: List of Entra groups

    Returns:
        List of (group, match_reason) tuples
    """
    matches = []
    pos_normalized = normalize_for_comparison(position)
    pos_words = set(pos_normalized.split())

    for group in groups:
        group_normalized = normalize_for_comparison(group.display_name)
        group_words = set(group_normalized.split())

        # Exact match (normalized)
        if pos_normalized == group_normalized:
            matches.append((group, "exact match"))
            continue

        # Position name contained in group name
        if pos_normalized in group_normalized:
            matches.append((group, "position in group name"))
            continue

        # Group name contained in position
        if group_normalized in pos_normalized:
            matches.append((group, "group in position name"))
            continue

        # Significant word overlap
        common_words = pos_words & group_words
        # Filter out common filler words
        filler = {"the", "and", "or", "of", "a", "an", "sji", "fire", "district"}
        meaningful_common = common_words - filler
        if meaningful_common:
            matches.append((group, f"common words: {', '.join(meaningful_common)}"))

    return matches


async def analyze_mappings(verbose: bool = False) -> None:
    """Analyze Aladtec positions and Entra groups for mapping suggestions."""
    logger.info("=" * 60)
    logger.info("Aladtec Position to Entra Group Mapping Analysis")
    logger.info("=" * 60)

    # Fetch Aladtec members and extract positions
    logger.info("")
    logger.info("Fetching Aladtec members...")

    try:
        with AladtecScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to log in to Aladtec")
                return
            members = scraper.get_members(include_inactive=True)
    except Exception as e:
        logger.error(f"Failed to fetch Aladtec members: {e}")
        return

    # Extract unique positions and count members per position
    position_members: dict[str, list[str]] = defaultdict(list)
    for member in members:
        if member.positions:
            for pos in member.positions:
                position_members[pos].append(member.display_name)
        elif member.position:
            position_members[member.position].append(member.display_name)

    positions = sorted(position_members.keys())
    logger.info(f"Found {len(positions)} unique positions across {len(members)} members")

    # Fetch Entra groups
    logger.info("")
    logger.info("Fetching Entra ID groups...")

    try:
        group_manager = EntraGroupManager()
        all_groups = await group_manager.get_groups()
    except Exception as e:
        logger.error(f"Failed to fetch Entra groups: {e}")
        return

    # Categorize groups
    security_groups = [g for g in all_groups if g.group_type == GroupType.SECURITY]
    m365_groups = [g for g in all_groups if g.group_type == GroupType.MICROSOFT_365]
    other_groups = [
        g for g in all_groups if g.group_type not in (GroupType.SECURITY, GroupType.MICROSOFT_365)
    ]

    logger.info(f"Found {len(all_groups)} total groups:")
    logger.info(f"  - Security groups: {len(security_groups)}")
    logger.info(f"  - Microsoft 365 groups: {len(m365_groups)}")
    logger.info(f"  - Other (distribution, etc.): {len(other_groups)}")

    # Print all groups
    logger.info("")
    logger.info("=" * 60)
    logger.info("ENTRA ID GROUPS")
    logger.info("=" * 60)

    logger.info("")
    logger.info("Security Groups:")
    logger.info("-" * 40)
    for group in sorted(security_groups, key=lambda g: g.display_name):
        desc = f" - {group.description[:50]}..." if group.description else ""
        logger.info(f"  {group.display_name}{desc}")

    logger.info("")
    logger.info("Microsoft 365 Groups:")
    logger.info("-" * 40)
    for group in sorted(m365_groups, key=lambda g: g.display_name):
        desc = f" - {group.description[:50]}..." if group.description else ""
        mail = f" ({group.mail})" if group.mail else ""
        logger.info(f"  {group.display_name}{mail}{desc}")

    if other_groups:
        logger.info("")
        logger.info("Other Groups (Distribution Lists, etc.):")
        logger.info("-" * 40)
        for group in sorted(other_groups, key=lambda g: g.display_name):
            logger.info(f"  {group.display_name} ({group.group_type.value})")

    # Print all positions
    logger.info("")
    logger.info("=" * 60)
    logger.info("ALADTEC POSITIONS")
    logger.info("=" * 60)
    logger.info("")
    for pos in positions:
        count = len(position_members[pos])
        logger.info(f"  {pos} ({count} member{'s' if count != 1 else ''})")
        if verbose:
            for name in sorted(position_members[pos]):
                logger.info(f"    - {name}")

    # Analyze mappings
    logger.info("")
    logger.info("=" * 60)
    logger.info("MAPPING ANALYSIS")
    logger.info("=" * 60)

    # Groups to consider for mapping (security + M365)
    mappable_groups = security_groups + m365_groups

    matched_positions = []
    unmatched_positions = []
    used_groups = set()

    for pos in positions:
        matches = find_potential_matches(pos, mappable_groups)
        if matches:
            matched_positions.append((pos, matches))
            for group, _ in matches:
                used_groups.add(group.id)
        else:
            unmatched_positions.append(pos)

    # Print suggested mappings
    logger.info("")
    logger.info("Suggested Mappings:")
    logger.info("-" * 40)
    for pos, matches in matched_positions:
        member_count = len(position_members[pos])
        logger.info("")
        logger.info(f"  {pos} ({member_count} members)")
        for group, reason in matches:
            gtype = "Security" if group.group_type == GroupType.SECURITY else "M365"
            logger.info(f"    -> {group.display_name} [{gtype}] ({reason})")

    # Print positions without matches (GAPS)
    logger.info("")
    logger.info("=" * 60)
    logger.info("GAPS - Positions Without Group Matches")
    logger.info("=" * 60)
    logger.info("")
    if unmatched_positions:
        for pos in unmatched_positions:
            member_count = len(position_members[pos])
            logger.info(f"  {pos} ({member_count} members) - NO MATCHING GROUP")
    else:
        logger.info("  All positions have potential group matches!")

    # Print groups not matched to any position
    logger.info("")
    logger.info("=" * 60)
    logger.info("GAPS - Groups Not Matched to Any Position")
    logger.info("=" * 60)
    logger.info("")
    unused_groups = [g for g in mappable_groups if g.id not in used_groups]
    if unused_groups:
        for group in sorted(unused_groups, key=lambda g: g.display_name):
            gtype = "Security" if group.group_type == GroupType.SECURITY else "M365"
            logger.info(f"  {group.display_name} [{gtype}]")
    else:
        logger.info("  All groups have potential position matches!")

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("")
    logger.info(f"Aladtec Positions: {len(positions)}")
    logger.info(f"  - With suggested mappings: {len(matched_positions)}")
    logger.info(f"  - Without matches (need new groups): {len(unmatched_positions)}")
    logger.info("")
    logger.info(f"Entra Groups (Security + M365): {len(mappable_groups)}")
    logger.info(f"  - Matched to positions: {len(used_groups)}")
    logger.info(f"  - Not matched (may be unrelated): {len(unused_groups)}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze Aladtec positions and Entra groups for mapping suggestions",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show member names for each position",
    )
    args = parser.parse_args()

    asyncio.run(analyze_mappings(verbose=args.verbose))


if __name__ == "__main__":
    main()
