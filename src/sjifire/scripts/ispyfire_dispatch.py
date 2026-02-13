#!/usr/bin/env python3
"""iSpyFire dispatch CLI for viewing call/incident data.

Commands:
    list    - List recent calls with dispatch ID, time, nature, address
    detail  - Show full details for a specific call
    open    - Show currently active/open calls
    archive - Archive completed calls to Cosmos DB
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from sjifire.ispyfire.client import ISpyFireClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _format_timestamp(ts: str | None) -> str:
    """Format a unix timestamp string to a readable date."""
    if not ts:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return ts


def cmd_list(args) -> int:
    """List recent calls."""
    with ISpyFireClient() as client:
        summaries = client.get_calls(days=args.days)

        if not summaries:
            print("No calls found.")
            return 0

        # Fetch details for each summary to show useful info
        print(f"\n{'Dispatch ID':<15} {'Date/Time':<22} {'Nature':<25} {'Address':<30} {'Units'}")
        print("-" * 110)

        for summary in summaries:
            detail = client.get_call_details(summary.id)
            if detail:
                status = "" if detail.is_completed else " [OPEN]"
                city = f", {detail.city}" if detail.city else ""
                print(
                    f"{detail.long_term_call_id:<15} "
                    f"{detail.time_reported:<22} "
                    f"{detail.nature:<25} "
                    f"{detail.address}{city:<30} "
                    f"{detail.joined_responders}{status}"
                )
            else:
                ts = _format_timestamp(summary.ispy_timestamp)
                print(f"{'?':<15} {ts:<22} (details unavailable)")

        print(f"\nTotal: {len(summaries)} calls in last {args.days} days")
        return 0


def cmd_detail(args) -> int:
    """Show full details for a specific call."""
    with ISpyFireClient() as client:
        call = client.get_call_details(args.call_id)

        if not call:
            print(f"Call not found: {args.call_id}")
            return 1

        # Header
        status = "Completed" if call.is_completed else "ACTIVE"
        print(f"\n{'=' * 70}")
        print(f"  Dispatch ID:  {call.long_term_call_id}")
        print(f"  Nature:       {call.nature}")
        print(f"  Address:      {call.address}")
        if call.city:
            print(f"  City:         {call.city}, {call.state} {call.zip_code}")
        print(f"  Zone:         {call.zone_code}")
        call_type = {"f": "Fire", "m": "Medical", "e": "EMS"}.get(call.type, call.type)
        print(f"  Type:         {call_type}")
        print(f"  Reported:     {call.time_reported}")
        print(f"  Status:       {status}")
        print(f"  Agency:       {call.agency_code}")
        if call.geo_location:
            print(f"  Location:     {call.geo_location}")
        print(f"{'=' * 70}")

        # Units / Responder timeline
        if call.responder_details:
            print("\n  Unit Response Timeline:")
            print(f"  {'Unit':<8} {'Status':<8} {'Time':<24} {'Log'}")
            print(f"  {'-' * 65}")
            for unit in call.responder_details:
                print(
                    f"  {unit.unit_number:<8} "
                    f"{unit.status:<8} "
                    f"{unit.time_of_status_change:<24} "
                    f"{unit.radio_log}"
                )

        # iSpy mobile responders
        if call.ispy_responders:
            print("\n  iSpy Mobile Responders:")
            for resp in call.ispy_responders.values():
                name = f"{resp.get('first', '')} {resp.get('last', '')}"
                status_str = resp.get("status", "")
                print(f"    {name:<25} {status_str}")

        # Comments
        if call.comments:
            print("\n  Comments:")
            for line in call.comments.split("\n"):
                print(f"    {line}")

        # Audit log
        if args.log:
            log_entries = client.get_call_log(call.id)
            if log_entries:
                print(f"\n  Audit Log ({len(log_entries)} entries):")
                for entry in log_entries:
                    ts = entry.get("timestamp", 0)
                    dt = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
                    email = entry.get("email", "unknown")
                    print(f"    {dt}  {email}")

        print()
        return 0


def cmd_open(args) -> int:
    """Show currently active/open calls."""
    with ISpyFireClient() as client:
        calls = client.get_open_calls()

        if not calls:
            print("No open calls.")
            return 0

        print(f"\n{'Dispatch ID':<15} {'Time':<22} {'Nature':<25} {'Address':<30} {'Units'}")
        print("-" * 110)

        for call in calls:
            city = f", {call.city}" if call.city else ""
            print(
                f"{call.long_term_call_id:<15} "
                f"{call.time_reported:<22} "
                f"{call.nature:<25} "
                f"{call.address}{city:<30} "
                f"{call.joined_responders}"
            )

        print(f"\nTotal: {len(calls)} open calls")
        return 0


async def _get_existing_ids(summary_ids: list[str]) -> set[str]:
    """Check which call UUIDs already exist in Cosmos DB.

    Args:
        summary_ids: List of iSpyFire call UUIDs from the summary list

    Returns:
        Set of UUIDs already stored
    """
    from sjifire.mcp.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        return await store.get_existing_ids(summary_ids)


async def _archive_to_cosmos(
    calls: list,
    client: ISpyFireClient,
) -> int:
    """Store completed calls to Cosmos DB via DispatchStore.

    Uses asyncio.to_thread to wrap the blocking ISpyFireClient.get_call_log().

    Args:
        calls: List of completed DispatchCall objects
        client: Active ISpyFireClient session

    Returns:
        Number of calls archived
    """
    from sjifire.mcp.dispatch.store import DispatchStore

    async def fetch_log(call_id: str) -> list[dict]:
        return await asyncio.to_thread(client.get_call_log, call_id)

    async with DispatchStore() as store:
        return await store.store_completed(calls, fetch_log)


def cmd_archive(args) -> int:
    """Archive completed calls to Cosmos DB."""
    with ISpyFireClient() as client:
        summaries = client.get_calls(days=args.days)

        if not summaries:
            print("No calls found.")
            return 0

        # Check which calls are already archived
        summary_ids = [s.id for s in summaries]
        existing = asyncio.run(_get_existing_ids(summary_ids))
        new_summaries = [s for s in summaries if s.id not in existing]

        print(
            f"Found {len(summaries)} calls in last {args.days} days "
            f"({len(existing)} already archived, {len(new_summaries)} new)"
        )

        if not new_summaries:
            print("All calls already archived.")
            return 0

        # Only fetch details for new calls
        calls = []
        for summary in new_summaries:
            detail = client.get_call_details(summary.id)
            if detail:
                calls.append(detail)

        completed = [c for c in calls if c.is_completed]
        open_calls = len(calls) - len(completed)

        if open_calls:
            print(f"  {open_calls} still open (will archive when completed)")

        if not completed:
            print("No new completed calls to archive.")
            return 0

        if args.dry_run:
            print(f"[DRY RUN] Would archive {len(completed)} completed calls to Cosmos DB")
            for call in completed:
                print(f"  {call.long_term_call_id}  {call.time_reported}  {call.nature}")
            return 0

        stored = asyncio.run(_archive_to_cosmos(completed, client))
        print(f"Archived {stored} completed calls to Cosmos DB")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="iSpyFire dispatch CLI - view call/incident data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # list command
    list_parser = subparsers.add_parser("list", help="List recent calls")
    list_parser.add_argument(
        "--days", type=int, default=30, choices=[7, 30], help="Days to look back (default: 30)"
    )
    list_parser.set_defaults(func=cmd_list)

    # detail command
    detail_parser = subparsers.add_parser("detail", help="Show full call details")
    detail_parser.add_argument("call_id", help="Call ID (UUID) or dispatch ID (e.g. 26-001678)")
    detail_parser.add_argument("--log", action="store_true", help="Include audit log")
    detail_parser.set_defaults(func=cmd_detail)

    # open command
    open_parser = subparsers.add_parser("open", help="Show currently open calls")
    open_parser.set_defaults(func=cmd_open)

    # archive command
    archive_parser = subparsers.add_parser(
        "archive", help="Archive completed calls to Cosmos DB"
    )
    archive_parser.add_argument(
        "--days", type=int, default=7, choices=[7, 30],
        help="Days to look back (default: 7, use 30 for initial preload)",
    )
    archive_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be archived without writing"
    )
    archive_parser.set_defaults(func=cmd_archive)

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
