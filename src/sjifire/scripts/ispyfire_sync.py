#!/usr/bin/env python3
"""Sync Entra ID users to iSpyFire.

This script compares operational users in Entra ID with people in iSpyFire
and can add, update, or deactivate users to keep them in sync.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from email_validator import EmailNotValidError, validate_email

from sjifire.core.config import get_project_root
from sjifire.entra.users import EntraUserManager
from sjifire.ispyfire.client import ISpyFireClient
from sjifire.ispyfire.sync import (
    compare_entra_to_ispyfire,
    entra_user_to_ispyfire_person,
    fields_need_update,
    get_responder_types,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("msal").setLevel(logging.WARNING)


def backup_ispyfire_people(people: list, backup_dir: Path) -> Path:
    """Backup current iSpyFire people to JSON file.

    Args:
        people: List of ISpyFirePerson objects
        backup_dir: Directory to save backup

    Returns:
        Path to backup file
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"ispyfire_people_{timestamp}.json"

    # Convert to serializable format
    data = [
        {
            "id": person.id,
            "firstName": person.first_name,
            "lastName": person.last_name,
            "email": person.email,
            "cellPhone": person.cell_phone,
            "title": person.title,
            "isActive": person.is_active,
            "isLoginActive": person.is_login_active,
            "groupSetACLs": person.group_set_acls,
        }
        for person in people
    ]

    with backup_file.open("w") as f:
        json.dump(data, f, indent=2)

    logger.info(f"Backed up {len(people)} people to {backup_file}")
    return backup_file


def print_comparison_report(comparison) -> None:
    """Print a detailed comparison report."""
    print("\n" + "=" * 70)
    print("ISPYFIRE SYNC COMPARISON REPORT")
    print("=" * 70)

    # Summary
    print("\nSUMMARY:")
    print(f"  Entra operational users: {len(comparison.entra_operational)}")
    print(f"  iSpyFire people:         {len(comparison.ispyfire_people)}")
    print(f"  Already matched:         {len(comparison.matched)}")
    print(f"  To add to iSpyFire:      {len(comparison.to_add)}")
    print(f"  To update in iSpyFire:   {len(comparison.to_update)}")
    print(f"  To remove from iSpyFire: {len(comparison.to_remove)}")
    if comparison.skipped_no_phone:
        print(f"  Skipped (no phone):      {len(comparison.skipped_no_phone)}")
    if comparison.skipped_no_operational:
        print(f"  Skipped (non-operational): {len(comparison.skipped_no_operational)}")

    # People currently in iSpyFire
    print(f"\n{'=' * 70}")
    print("CURRENT ISPYFIRE PEOPLE")
    print("=" * 70)
    for person in sorted(comparison.ispyfire_people, key=lambda p: p.last_name):
        status = "ACTIVE" if person.is_active else "INACTIVE"
        print(f"  [{status}] {person.display_name} <{person.email}> - {person.title or 'No title'}")

    # Matched (in sync)
    if comparison.matched:
        print(f"\n{'=' * 70}")
        print("MATCHED (no changes needed)")
        print("=" * 70)
        for _user, person in sorted(comparison.matched, key=lambda x: x[1].last_name):
            print(f"  ✓ {person.display_name} <{person.email}>")

    # To add
    if comparison.to_add:
        print(f"\n{'=' * 70}")
        print("TO ADD TO ISPYFIRE")
        print("=" * 70)
        for user in sorted(comparison.to_add, key=lambda u: u.last_name or ""):
            rank = user.extension_attribute1 or ""
            print(f"  + {user.display_name} <{user.email}>")
            print(f"      Phone: {user.mobile_phone or 'None'}")
            print(f"      Rank: {rank or 'None'}")

    # To update
    if comparison.to_update:
        print(f"\n{'=' * 70}")
        print("TO UPDATE IN ISPYFIRE")
        print("=" * 70)
        for user, person in sorted(comparison.to_update, key=lambda x: x[1].last_name):
            diff_fields = fields_need_update(user, person)
            print(f"  ~ {person.display_name} <{person.email}>")
            print(f"      Fields to update: {', '.join(diff_fields)}")
            for field_name in diff_fields:
                if field_name == "firstName":
                    print(f"        firstName: '{person.first_name}' -> '{user.first_name}'")
                elif field_name == "lastName":
                    print(f"        lastName: '{person.last_name}' -> '{user.last_name}'")
                elif field_name == "cellPhone":
                    print(f"        cellPhone: '{person.cell_phone}' -> '{user.mobile_phone}'")
                elif field_name == "title":
                    entra_rank = user.extension_attribute1 or ""
                    print(f"        title: '{person.title}' -> '{entra_rank}'")

    # To remove
    if comparison.to_remove:
        print(f"\n{'=' * 70}")
        print("TO REMOVE FROM ISPYFIRE (deactivate)")
        print("=" * 70)
        for person in sorted(comparison.to_remove, key=lambda p: p.last_name):
            print(f"  - {person.display_name} <{person.email}> - {person.title or 'No title'}")

    # Skipped - no cell phone
    if comparison.skipped_no_phone:
        print(f"\n{'=' * 70}")
        print("SKIPPED - NO CELL PHONE")
        print("=" * 70)
        for user in sorted(comparison.skipped_no_phone, key=lambda u: u.last_name or ""):
            print(f"  ! {user.display_name} <{user.email}>")

    # Skipped - no operational position
    if comparison.skipped_no_operational:
        print(f"\n{'=' * 70}")
        print("SKIPPED - NO OPERATIONAL POSITION")
        print("=" * 70)
        for user in sorted(comparison.skipped_no_operational, key=lambda u: u.last_name or ""):
            positions = user.extension_attribute3 or "None"
            print(f"  ! {user.display_name} <{user.email}> - Positions: {positions}")

    print(f"\n{'=' * 70}")


async def run_sync(dry_run: bool = True, single_email: str | None = None) -> int:
    """Run the iSpyFire sync.

    Args:
        dry_run: If True, only show what would change without making changes
        single_email: If provided, only sync this user

    Returns:
        Exit code (0 for success)
    """
    project_root = get_project_root()
    backup_dir = project_root / "backups"

    # Get Entra employees (users with employee IDs - excludes shared mailboxes, resources)
    logger.info("Fetching Entra ID employees...")
    user_manager = EntraUserManager()
    entra_users = await user_manager.get_employees()
    logger.info(f"Fetched {len(entra_users)} employees from Entra ID")

    # Filter to single user if specified
    if single_email:
        single_email_lower = single_email.lower()
        entra_users = [u for u in entra_users if u.email and u.email.lower() == single_email_lower]
        if not entra_users:
            print(f"Error: User not found in Entra ID: {single_email}")
            return 1
        logger.info(f"Filtering to single user: {single_email}")

    # Get iSpyFire people (include inactive and deleted to prevent duplicates)
    logger.info("Fetching iSpyFire people...")
    with ISpyFireClient() as ispy_client:
        ispyfire_people = ispy_client.get_people(include_inactive=True, include_deleted=True)

        # Backup current state (only for full sync)
        if ispyfire_people and not single_email:
            backup_ispyfire_people(ispyfire_people, backup_dir)

        # Compare
        comparison = compare_entra_to_ispyfire(entra_users, ispyfire_people)

        # For single-user sync, don't remove anyone
        if single_email:
            comparison.to_remove = []

        # Print report
        print_comparison_report(comparison)

        if dry_run:
            print("\n*** DRY RUN - No changes made ***\n")
            return 0

        # Apply changes
        print("\nApplying changes...")

        # Add new people (creates user, sets active flags, sends invite email)
        # Safeguard: check by email first to prevent duplicates
        for user in comparison.to_add:
            # Double-check no existing person with this email (prevents duplicates)
            existing = ispy_client.get_person_by_email(user.email) if user.email else None
            if existing:
                logger.info(
                    f"Found existing person for {user.email}, reactivating instead of creating"
                )
                if not existing.is_active:
                    if ispy_client.reactivate_person(existing.id, email=existing.email):
                        logger.info(f"  Reactivated: {existing.display_name}")
                    else:
                        logger.error(f"  Failed to reactivate: {existing.display_name}")
                else:
                    logger.info(f"  Already active: {existing.display_name}")
                continue

            person = entra_user_to_ispyfire_person(user)
            logger.info(f"Creating: {person.display_name}")
            result = ispy_client.create_and_invite(person)
            if result:
                logger.info(f"  Created with ID: {result.id}")
            else:
                logger.error("  Failed to create")

        # Update existing people
        for user, person in comparison.to_update:
            logger.info(f"Updating: {person.display_name}")
            # Update fields from Entra
            if user.first_name:
                person.first_name = user.first_name
            if user.last_name:
                person.last_name = user.last_name
            if user.mobile_phone:
                person.cell_phone = user.mobile_phone
            if user.extension_attribute1:
                person.title = user.extension_attribute1
            # Update responder types from positions
            person.responder_types = get_responder_types(user)

            result = ispy_client.update_person(person)
            if result:
                logger.info("  Updated successfully")
            else:
                logger.error("  Failed to update")

        # Reactivate any matched users who are inactive in iSpyFire
        # (handles manual deactivation in iSpyFire UI)
        to_reactivate = [
            person
            for _user, person in comparison.matched + comparison.to_update
            if not person.is_active
        ]
        for person in to_reactivate:
            logger.info(f"Reactivating: {person.display_name}")
            if ispy_client.reactivate_person(person.id, email=person.email):
                logger.info("  Reactivated successfully")
            else:
                logger.error("  Failed to reactivate")

        # Deactivate removed people
        for person in comparison.to_remove:
            logger.info(f"Deactivating: {person.display_name}")
            if ispy_client.deactivate_person(person.id, email=person.email):
                logger.info("  Deactivated successfully")
            else:
                logger.error("  Failed to deactivate")

        print("\nSync complete.")
        return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync Entra ID users to iSpyFire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without making changes",
    )
    parser.add_argument(
        "--email",
        type=str,
        help="Sync a single user by email address",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate email if provided
    single_email = None
    if args.email:
        try:
            result = validate_email(args.email, check_deliverability=False)
            single_email = result.normalized
        except EmailNotValidError as e:
            print(f"Error: Invalid email address: {e}")
            return 1

    import asyncio

    return asyncio.run(run_sync(dry_run=args.dry_run, single_email=single_email))


if __name__ == "__main__":
    sys.exit(main())
