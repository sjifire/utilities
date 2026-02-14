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
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)


def _fmt_dt(dt: datetime | None) -> str:
    """Format a datetime for display, or 'N/A' if None."""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M")


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
                    f"{_fmt_dt(detail.time_reported):<22} "
                    f"{detail.nature:<25} "
                    f"{detail.address}{city:<30} "
                    f"{detail.responding_units}{status}"
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
        print(f"  Reported:     {_fmt_dt(call.time_reported)}")
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
                    f"{_fmt_dt(unit.time_of_status_change):<24} "
                    f"{unit.radio_log}"
                )

        # CAD Comments
        if call.cad_comments:
            print("\n  CAD Comments:")
            for line in call.cad_comments.split("\n"):
                print(f"    {line}")

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
                f"{_fmt_dt(call.time_reported):<22} "
                f"{call.nature:<25} "
                f"{call.address}{city:<30} "
                f"{call.responding_units}"
            )

        print(f"\nTotal: {len(calls)} open calls")
        return 0


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

        if new_summaries:
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

            if completed:
                if args.dry_run:
                    print(f"[DRY RUN] Would archive {len(completed)} completed calls to Cosmos DB")
                    for call in completed:
                        print(
                            f"  {call.long_term_call_id}  "
                            f"{_fmt_dt(call.time_reported)}  {call.nature}"
                        )
                    return 0

                stored = asyncio.run(_store_completed(completed))
                print(f"Archived {stored} completed calls to Cosmos DB")
            else:
                print("No new completed calls to archive.")
        else:
            print("All calls already archived.")

    # Enrich any archived docs that are missing structured analysis
    if not getattr(args, "dry_run", False):
        force = getattr(args, "force", False)
        enriched = asyncio.run(_enrich_stored(force=force))
        if enriched:
            _print_enrichment_results(enriched)
            count = sum(1 for d in enriched if d.analysis.incident_commander or d.analysis.summary)
            print(f"\nEnriched {count} calls with structured analysis")

    return 0


def _print_enrichment_results(docs) -> None:
    """Print per-call enrichment results."""
    label = "Re-analyzing" if len(docs) > 0 else "Enriching"
    print(f"\n{label} {len(docs)} calls...")

    for doc in docs:
        if doc.analysis.incident_commander or doc.analysis.summary:
            ic_name = doc.analysis.incident_commander_name
            ic = doc.analysis.incident_commander or "-"
            ic_display = f"{ic_name} ({ic})" if ic_name else ic
            alarm = doc.analysis.alarm_time[11:16] if doc.analysis.alarm_time else ""
            enrt = doc.analysis.first_enroute[11:16] if doc.analysis.first_enroute else ""
            times = f"  page:{alarm or '-':>5} enrt:{enrt or '-':>5}" if enrt else ""
            print(
                f"  {doc.long_term_call_id}  {doc.nature:<25} "
                f"IC: {ic_display:<20} {doc.analysis.outcome}{times}"
            )
        else:
            print(f"  {doc.long_term_call_id}  {doc.nature:<25} (no data extracted)")


# ---------------------------------------------------------------------------
# Async helpers — delegate to DispatchStore
# ---------------------------------------------------------------------------


async def _get_existing_ids(summary_ids: list[str]) -> set[str]:
    from sjifire.mcp.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        return await store.get_existing_ids(summary_ids)


async def _store_completed(calls: list) -> int:
    from sjifire.mcp.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        return await store.store_completed(calls)


async def _enrich_stored(*, force: bool = False) -> list:
    from sjifire.mcp.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        return await store.enrich_stored(force=force)


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
    detail_parser.set_defaults(func=cmd_detail)

    # open command
    open_parser = subparsers.add_parser("open", help="Show currently open calls")
    open_parser.set_defaults(func=cmd_open)

    # archive command
    archive_parser = subparsers.add_parser("archive", help="Archive completed calls to Cosmos DB")
    archive_parser.add_argument(
        "--days",
        type=int,
        default=7,
        choices=[7, 30],
        help="Days to look back (default: 7, use 30 for initial preload)",
    )
    archive_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be archived without writing"
    )
    archive_parser.add_argument(
        "--force", action="store_true", help="Re-analyze all calls, even those already enriched"
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
