#!/usr/bin/env python3
"""Sync Aladtec on-duty schedule to M365 shared calendar.

Usage:
    uv run calendar-sync --month "Jan 2026"   # Sync specific month
    uv run calendar-sync --months 4           # Sync next 4 months
    uv run calendar-sync --delete "Jan 2026"  # Delete all events for a month
"""

import argparse
import calendar
import logging
import sys
from datetime import date

from dateutil import parser as dateparser

from sjifire.aladtec.schedule import AladtecScheduleScraper
from sjifire.calendar import CalendarSync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_month(month_str: str) -> tuple[int, int]:
    """Parse a month string into (year, month).

    Uses python-dateutil for robust parsing. Accepts formats like:
    - "Jan 2026", "January 2026"
    - "2026-01", "2026-1"
    - "01/2026", "1/2026"

    Returns:
        Tuple of (year, month)

    Raises:
        ValueError: If the string cannot be parsed
    """
    month_str = month_str.strip()

    try:
        # dateutil.parser handles most date formats automatically
        parsed = dateparser.parse(month_str, dayfirst=False)
        if parsed:
            return parsed.year, parsed.month
    except (ValueError, TypeError):
        pass

    raise ValueError(
        f"Cannot parse month: '{month_str}'. Use formats like 'Jan 2026', '2026-01', or '01/2026'"
    )


def get_month_date_range(year: int, month: int) -> tuple[date, date]:
    """Get the first and last day of a month.

    Returns:
        Tuple of (first_day, last_day)
    """
    first_day = date(year, month, 1)
    last_day_num = calendar.monthrange(year, month)[1]
    last_day = date(year, month, last_day_num)
    return first_day, last_day


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync Aladtec on-duty schedule to M365 shared calendar"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--month",
        type=str,
        help="Sync a specific month (e.g., 'Jan 2026', '2026-01', '01/2026')",
    )
    parser.add_argument(
        "--months",
        type=int,
        help="Sync the next N months starting from today",
    )
    parser.add_argument(
        "--delete",
        type=str,
        metavar="MONTH",
        help="Delete all On Duty events for a month (e.g., 'Jan 2026')",
    )
    parser.add_argument(
        "--mailbox",
        default="svc-automations@sjifire.org",
        help="Shared mailbox email address (default: svc-automations@sjifire.org)",
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

    # Require exactly one of --month, --months, or --delete
    options_set = sum(bool(x) for x in [args.month, args.months, args.delete])
    if options_set == 0:
        parser.error("One of --month, --months, or --delete is required")
    if options_set > 1:
        parser.error("Cannot combine --month, --months, and --delete")

    if args.dry_run:
        logger.info("DRY RUN - no changes will be made")

    # Handle delete mode
    if args.delete:
        try:
            year, month = parse_month(args.delete)
            start_date, end_date = get_month_date_range(year, month)
        except ValueError as e:
            logger.error(str(e))
            return 1

        logger.info(f"Deleting On Duty events for {start_date} to {end_date}")

        calendar_sync = CalendarSync(mailbox=args.mailbox)
        result = calendar_sync.delete_date_range(start_date, end_date, dry_run=args.dry_run)

        logger.info(f"Delete complete: {result}")

        if result.errors:
            for error in result.errors:
                logger.error(f"  Error: {error}")
            return 1

        return 0

    # Calculate date range
    if args.month:
        # Sync specific month
        try:
            year, month = parse_month(args.month)
            start_date, end_date = get_month_date_range(year, month)
        except ValueError as e:
            logger.error(str(e))
            return 1
    else:
        # Sync next N months from today
        today = date.today()
        start_date = date(today.year, today.month, 1)

        # End date: N months forward (last day of that month)
        end_month = today.month + args.months - 1  # -1 because current month counts
        end_year = today.year
        while end_month > 12:
            end_month -= 12
            end_year += 1
        # Get last day of end month
        last_day_num = calendar.monthrange(end_year, end_month)[1]
        end_date = date(end_year, end_month, last_day_num)

    logger.info(f"Date range: {start_date} to {end_date}")

    # Step 1: Fetch schedules from Aladtec
    logger.info("Fetching schedule from Aladtec...")

    with AladtecScheduleScraper() as scraper:
        if not scraper.login():
            logger.error("Failed to log in to Aladtec")
            return 1

        schedules = scraper.get_schedule_range(start_date, end_date)

    if not schedules:
        logger.warning("No schedule data retrieved")
        # Still continue to sync what we have
        schedules = []

    logger.info(f"Retrieved {len(schedules)} days of schedule data")

    # Step 2: Sync to calendar
    logger.info(f"Syncing to calendar: {args.mailbox}")

    calendar_sync = CalendarSync(mailbox=args.mailbox)
    result = calendar_sync.sync(schedules, dry_run=args.dry_run)

    # Report results
    logger.info(f"Sync complete: {result}")

    if result.errors:
        for error in result.errors:
            logger.error(f"  Error: {error}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
