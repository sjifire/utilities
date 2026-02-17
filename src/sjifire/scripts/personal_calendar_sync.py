#!/usr/bin/env python3
"""Sync Aladtec schedule to users' personal M365 calendars.

Creates an "Aladtec Schedule" calendar in each user's mailbox with their
scheduled shifts from Aladtec. This is a one-way sync (Aladtec -> M365).

Usage:
    uv run personal-calendar-sync --user user@example.org --month "Feb 2026"
    uv run personal-calendar-sync --all --month "Feb 2026" --dry-run
    uv run personal-calendar-sync --inspect --user user@example.org --month "Feb 2026"
"""

import argparse
import asyncio
import calendar
import logging
import sys
from datetime import date

from dateutil import parser as dateparser

from sjifire.aladtec.member_scraper import AladtecMemberScraper
from sjifire.aladtec.schedule_scraper import (
    AladtecScheduleScraper,
    ScheduleEntry,
    load_schedules,
)
from sjifire.calendar.personal_sync import PersonalCalendarSync
from sjifire.core.schedule import is_filled_entry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress verbose logging
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_month(month_str: str) -> tuple[int, int]:
    """Parse a month string into (year, month)."""
    month_str = month_str.strip()
    try:
        parsed = dateparser.parse(month_str, dayfirst=False)
        if parsed:
            return parsed.year, parsed.month
    except (ValueError, TypeError):
        pass
    raise ValueError(f"Cannot parse month: '{month_str}'")


def get_month_date_range(year: int, month: int) -> tuple[date, date]:
    """Get the first and last day of a month."""
    first_day = date(year, month, 1)
    last_day_num = calendar.monthrange(year, month)[1]
    last_day = date(year, month, last_day_num)
    return first_day, last_day


def normalize_name(name: str) -> str:
    """Normalize a name for matching (lowercase, strip extra spaces)."""
    return " ".join(name.lower().split())


def match_schedule_name_to_email(
    schedule_name: str,
    members: dict[str, str],
) -> str | None:
    """Match a schedule name to a member email.

    Schedule names are "Last, First" format.
    Member dict maps "First Last" -> email.

    Args:
        schedule_name: Name from schedule (e.g., "Greene, Adam")
        members: Dict mapping display name to email

    Returns:
        Email address or None if no match
    """
    # Parse "Last, First" into "First Last"
    if ", " in schedule_name:
        parts = schedule_name.split(", ", 1)
        normalized_name = f"{parts[1]} {parts[0]}" if len(parts) == 2 else schedule_name
    else:
        normalized_name = schedule_name

    normalized_name = normalize_name(normalized_name)

    # Try exact match first
    for display_name, email in members.items():
        if normalize_name(display_name) == normalized_name:
            return email

    # Try partial match (schedule might have middle name)
    for display_name, email in members.items():
        display_normalized = normalize_name(display_name)
        # Check if all words in display_name are in normalized_name
        display_words = set(display_normalized.split())
        schedule_words = set(normalized_name.split())
        if display_words <= schedule_words or schedule_words <= display_words:
            return email

    return None


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync Aladtec schedule to users' personal M365 calendars"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--month",
        type=str,
        help="Month to sync (e.g., 'Feb 2026', '2026-02')",
    )
    parser.add_argument(
        "--months",
        type=int,
        help="Sync the next N months starting from today",
    )
    parser.add_argument(
        "--load-schedule",
        type=str,
        metavar="PATH",
        help="Load schedule from JSON file (from duty-calendar-sync --save-schedule)",
    )
    parser.add_argument(
        "--user",
        type=str,
        help="Sync only this user's calendar (email address)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sync all users with scheduled time",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force update all events even if body hasn't changed",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="View existing events instead of syncing",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete all Aladtec-categorized events from user's primary calendar",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle purge mode first (doesn't need --month)
    if args.purge:
        if not args.user:
            parser.error("--purge requires --user")

        if args.dry_run:
            logger.info("DRY RUN - no changes will be made")

        logger.info(f"Purging Aladtec events for {args.user}...")
        sync = PersonalCalendarSync()

        async def do_purge() -> int:
            deleted, errors = await sync.purge_aladtec_events(args.user, args.dry_run)
            if args.dry_run:
                logger.info(f"Would delete {deleted} events")
            else:
                logger.info(f"Deleted {deleted} events, {errors} errors")
            return 1 if errors else 0

        return asyncio.run(do_purge())

    if not args.user and not args.all:
        parser.error("Either --user or --all is required")

    if args.user and args.all:
        parser.error("Cannot use both --user and --all")

    if not args.month and not args.months:
        parser.error("Either --month or --months is required")

    if args.month and args.months:
        parser.error("Cannot use both --month and --months")

    # Calculate date range
    if args.month:
        try:
            year, month = parse_month(args.month)
            start_date, end_date = get_month_date_range(year, month)
        except ValueError as e:
            logger.error(str(e))
            return 1
    else:
        # --months: sync next N months from today
        today = date.today()
        start_date = date(today.year, today.month, 1)

        end_month = today.month + args.months - 1
        end_year = today.year
        while end_month > 12:
            end_month -= 12
            end_year += 1
        last_day_num = calendar.monthrange(end_year, end_month)[1]
        end_date = date(end_year, end_month, last_day_num)

    if args.dry_run:
        logger.info("DRY RUN - no changes will be made")

    # Handle inspect mode
    if args.inspect:
        if not args.user:
            parser.error("--inspect requires --user")

        logger.info(f"Inspecting {args.user} for {start_date} to {end_date}")
        sync = PersonalCalendarSync()

        async def do_inspect() -> int:
            # Get or create calendar to find its ID
            calendar_id = await sync.get_or_create_calendar(args.user)
            if not calendar_id:
                print(f"No Aladtec Schedule calendar found for {args.user}")
                return 1

            events = await sync.get_existing_events(args.user, calendar_id, start_date, end_date)

            if not events:
                print(f"\nNo events found in Aladtec Schedule calendar for {args.user}")
                return 0

            print(f"\nFound {len(events)} events in Aladtec Schedule calendar:\n")
            for key in sorted(events.keys()):
                # Key format: date|subject|start_time|end_time
                parts = key.split("|")
                if len(parts) >= 4:
                    event_date, subject = parts[0], parts[1]
                    start_time, end_time = parts[2], parts[3]
                    print(f"  {event_date} {start_time}-{end_time}: {subject}")
                else:
                    print(f"  {key}")

            return 0

        return asyncio.run(do_inspect())

    logger.info(f"Syncing {start_date} to {end_date}")

    # Step 1: Fetch member list from Aladtec to get name->email mapping
    logger.info("Fetching member list from Aladtec...")
    members: dict[str, str] = {}

    with AladtecMemberScraper() as scraper:
        if not scraper.login():
            logger.error("Failed to login to Aladtec")
            return 1

        member_list = scraper.get_members(enrich=False)  # Only need name/email mapping
        for member in member_list:
            if member.email:
                # Map both "First Last" and other variations
                display_name = f"{member.first_name} {member.last_name}"
                members[display_name] = member.email

    logger.info(f"Found {len(members)} members with emails")

    # Step 2: Get schedule data (from cache or Aladtec)
    if args.load_schedule:
        logger.info(f"Loading schedule from {args.load_schedule}...")
        try:
            schedules = load_schedules(args.load_schedule)
            # Filter to our date range (cache may have more data)
            schedules = [s for s in schedules if start_date <= s.date <= end_date]
        except FileNotFoundError:
            logger.error(f"Schedule file not found: {args.load_schedule}")
            return 1
        except Exception as e:
            logger.error(f"Failed to load schedule: {e}")
            return 1
    else:
        logger.info("Fetching schedule from Aladtec...")
        with AladtecScheduleScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to login to Aladtec")
                return 1
            schedules = scraper.get_schedule_range(start_date, end_date)

    logger.info(f"Got {len(schedules)} days with schedule data")

    # Step 3: Group entries by user
    entries_by_email: dict[str, list[ScheduleEntry]] = {}
    unmatched_names: set[str] = set()
    skipped_empty_position = 0

    for day in schedules:
        for entry in day.entries:
            # Skip unfilled entries (empty name or "Section / Position" placeholder)
            if not is_filled_entry(entry.name):
                skipped_empty_position += 1
                continue

            email = match_schedule_name_to_email(entry.name, members)
            if email:
                if email not in entries_by_email:
                    entries_by_email[email] = []
                entries_by_email[email].append(entry)
            else:
                unmatched_names.add(entry.name)

    if skipped_empty_position:
        logger.info(f"Skipped {skipped_empty_position} entries with empty position (e.g., Trades)")

    if unmatched_names:
        logger.warning(f"Could not match {len(unmatched_names)} names to emails")
        if args.verbose:
            for name in sorted(unmatched_names):
                logger.debug(f"  Unmatched: {name}")

    # Step 4: Filter to requested user(s)
    if args.user:
        user_email = args.user.lower()
        if user_email not in entries_by_email:
            logger.warning(f"No schedule entries found for {args.user}")
            entries_by_email = {}
        else:
            entries_by_email = {user_email: entries_by_email[user_email]}

    logger.info(f"Syncing calendars for {len(entries_by_email)} users")

    # Step 5: Sync each user
    sync = PersonalCalendarSync()

    async def sync_all() -> list:
        results = []
        for email, entries in entries_by_email.items():
            logger.info(f"Syncing {email} ({len(entries)} entries)...")
            result = await sync.sync_user(
                email, entries, start_date, end_date, args.dry_run, args.force
            )
            results.append(result)
            logger.info(f"  {result}")
        return results

    results = asyncio.run(sync_all())

    # Summary
    total_created = sum(r.events_created for r in results)
    total_updated = sum(r.events_updated for r in results)
    total_deleted = sum(r.events_deleted for r in results)
    total_errors = sum(len(r.errors) for r in results)

    logger.info(
        f"\nSync complete: {total_created} created, {total_updated} updated, "
        f"{total_deleted} deleted"
    )
    if total_errors:
        logger.error(f"{total_errors} errors occurred")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
