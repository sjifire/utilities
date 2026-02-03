#!/usr/bin/env python3
"""Sync Aladtec on-duty schedule to M365 shared calendar.

Usage:
    uv run calendar-sync --month "Jan 2026"   # Sync specific month
    uv run calendar-sync --months 4           # Sync next 4 months
"""

import argparse
import calendar
import logging
import re
import sys
from datetime import date, timedelta

from sjifire.aladtec.schedule import AladtecScheduleScraper
from sjifire.calendar import CalendarSync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_month(month_str: str) -> tuple[int, int]:
    """Parse a month string into (year, month).

    Accepts formats like:
    - "Jan 2026", "January 2026"
    - "2026-01", "2026-1"
    - "01/2026", "1/2026"

    Returns:
        Tuple of (year, month)

    Raises:
        ValueError: If the string cannot be parsed
    """
    month_str = month_str.strip()

    # Try "YYYY-MM" or "YYYY-M" format
    match = re.match(r"^(\d{4})-(\d{1,2})$", month_str)
    if match:
        return int(match.group(1)), int(match.group(2))

    # Try "MM/YYYY" or "M/YYYY" format
    match = re.match(r"^(\d{1,2})/(\d{4})$", month_str)
    if match:
        return int(match.group(2)), int(match.group(1))

    # Try "Month YYYY" or "Mon YYYY" format
    match = re.match(r"^([A-Za-z]+)\s+(\d{4})$", month_str)
    if match:
        month_name = match.group(1).lower()
        year = int(match.group(2))

        # Map month names to numbers
        month_names = {
            "jan": 1, "january": 1,
            "feb": 2, "february": 2,
            "mar": 3, "march": 3,
            "apr": 4, "april": 4,
            "may": 5,
            "jun": 6, "june": 6,
            "jul": 7, "july": 7,
            "aug": 8, "august": 8,
            "sep": 9, "sept": 9, "september": 9,
            "oct": 10, "october": 10,
            "nov": 11, "november": 11,
            "dec": 12, "december": 12,
        }

        if month_name in month_names:
            return year, month_names[month_name]

    raise ValueError(
        f"Cannot parse month: '{month_str}'. "
        "Use formats like 'Jan 2026', '2026-01', or '01/2026'"
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
        "--mailbox",
        default="svc-automations@sjifire.org",
        help="Shared mailbox email address (default: svc-automations@sjifire.org)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Require either --month or --months
    if not args.month and not args.months:
        parser.error("Either --month or --months is required")

    if args.month and args.months:
        parser.error("Cannot use both --month and --months")

    if args.dry_run:
        logger.info("DRY RUN - no changes will be made")

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
