#!/usr/bin/env python3
"""Manage Aladtec calendar subscriptions in M365.

This script helps sync Aladtec iCal calendar subscriptions to M365 users.
Since Microsoft Graph API doesn't support adding iCal subscriptions programmatically,
this script:
1. Imports iCal URLs from a CSV file (email,ical_url)
2. Validates users exist in M365
3. Generates subscription instructions or checks existing subscriptions

Note: Aladtec iCal URLs contain a private token (uid) that can only be obtained
by each user from their "My Schedule" > "Share My Schedule" > "Links" tab.
Use --generate-template to create a CSV template for users to fill in.

Usage:
    uv run calendar-subscriptions --generate-template > ical_urls.csv
    uv run calendar-subscriptions --import ical_urls.csv --dry-run
    uv run calendar-subscriptions --check-existing --email user@sjifire.org
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

from sjifire.aladtec.member_scraper import AladtecMemberScraper
from sjifire.calendar.subscriptions import (
    CalendarSubscription,
    CalendarSubscriptionManager,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress verbose logging unless in verbose mode
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def generate_csv_template() -> int:
    """Generate a CSV template with member emails for iCal URL collection."""
    logger.info("Fetching members from Aladtec to generate template...")

    with AladtecMemberScraper() as scraper:
        if not scraper.login():
            logger.error("Failed to log in to Aladtec")
            return 1

        members = scraper.get_members()

    # Print CSV header and rows
    print("email,ical_url,notes")
    for member in sorted(members, key=lambda m: m.email or ""):
        if member.email:
            # Leave ical_url blank for users to fill in
            print(f'{member.email},,"Get from Aladtec: My Schedule > Share My Schedule > Links"')

    logger.info(f"Generated template for {len([m for m in members if m.email])} members")
    return 0


def import_ical_urls(csv_path: Path) -> list[CalendarSubscription]:
    """Import iCal URLs from a CSV file.

    Expected format: email,ical_url[,notes]

    Args:
        csv_path: Path to the CSV file

    Returns:
        List of CalendarSubscription objects
    """
    subscriptions = []

    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row.get("email", "").strip()
            ical_url = row.get("ical_url", "").strip()

            if email and ical_url:
                subscriptions.append(
                    CalendarSubscription(
                        user_email=email,
                        subscription_url=ical_url,
                        calendar_name="Aladtec Schedule",
                    )
                )

    return subscriptions


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Manage Aladtec calendar subscriptions in M365")
    parser.add_argument(
        "--generate-template",
        action="store_true",
        help="Generate a CSV template with member emails for iCal URL collection",
    )
    parser.add_argument(
        "--import",
        dest="import_csv",
        type=str,
        metavar="CSV_FILE",
        help="Import iCal URLs from a CSV file (email,ical_url columns)",
    )
    parser.add_argument(
        "--check-existing",
        action="store_true",
        help="Check if users already have Aladtec calendar subscriptions in M365",
    )
    parser.add_argument(
        "--email",
        type=str,
        help="Process single user by email address",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
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
        logging.getLogger("azure").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.DEBUG)

    # Validate arguments
    if not args.generate_template and not args.import_csv and not args.check_existing:
        parser.error("One of --generate-template, --import, or --check-existing is required")

    if args.dry_run:
        logger.info("DRY RUN - no changes will be made")

    # Handle --generate-template
    if args.generate_template:
        return generate_csv_template()

    # Handle --check-existing
    if args.check_existing:
        manager = CalendarSubscriptionManager()

        if args.email:
            emails = [args.email]
        else:
            # Get all member emails from Aladtec
            logger.info("Fetching members from Aladtec...")
            with AladtecMemberScraper() as scraper:
                if not scraper.login():
                    logger.error("Failed to log in to Aladtec")
                    return 1
                members = scraper.get_members()
                emails = [m.email for m in members if m.email]

        logger.info(f"Checking {len(emails)} users for existing Aladtec calendar...")

        import asyncio

        async def check_all():
            results = []
            for email in emails:
                has_calendar = await manager.check_existing_subscription(email, "Aladtec")
                results.append((email, has_calendar))
            return results

        results = asyncio.run(check_all())

        has_count = sum(1 for _, has in results if has)
        missing_count = sum(1 for _, has in results if not has)

        logger.info(f"Results: {has_count} have Aladtec calendar, {missing_count} missing")

        for email, has_calendar in results:
            status = "✓ Has" if has_calendar else "✗ Missing"
            print(f"  {status}: {email}")

        return 0

    # Handle --import
    if args.import_csv:
        csv_path = Path(args.import_csv)
        if not csv_path.exists():
            logger.error(f"CSV file not found: {csv_path}")
            return 1

        subscriptions = import_ical_urls(csv_path)
        logger.info(f"Loaded {len(subscriptions)} subscriptions from {csv_path}")

        if not subscriptions:
            logger.warning("No valid subscriptions found in CSV")
            return 1

        # Filter by email if specified
        if args.email:
            subscriptions = [s for s in subscriptions if s.user_email.lower() == args.email.lower()]
            if not subscriptions:
                logger.error(f"No subscription found for email: {args.email}")
                return 1

        # Process subscriptions
        logger.info(f"Processing {len(subscriptions)} calendar subscriptions...")

        manager = CalendarSubscriptionManager()
        results = manager.sync_subscriptions(subscriptions, dry_run=args.dry_run)

        # Report results
        success_count = sum(1 for r in results if r.success)
        error_count = sum(1 for r in results if not r.success)

        logger.info(f"Results: {success_count} successful, {error_count} errors")

        for result in results:
            status = "✓" if result.success else "✗"
            logger.info(f"  {status} {result.user_email}: {result.message}")
            if result.subscription_url:
                logger.debug(f"    URL: {result.subscription_url}")

        return 0 if error_count == 0 else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
