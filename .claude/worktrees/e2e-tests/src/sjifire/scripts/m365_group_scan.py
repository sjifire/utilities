#!/usr/bin/env python3
"""Scan M365 groups for usage analysis and detailed content inspection."""

import asyncio
import csv
import logging
from dataclasses import dataclass
from datetime import datetime

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder

from sjifire.core.msgraph_client import get_graph_client

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Suppress verbose Azure SDK and httpx logging
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("msal").setLevel(logging.WARNING)


@dataclass
class GroupUsageInfo:
    """Group with usage/activity information."""

    id: str
    display_name: str
    mail: str | None
    member_count: int
    created_date: datetime | None
    drive_storage_bytes: int
    drive_file_count: int
    drive_file_count_exceeded: bool  # True if count hit the limit
    conversation_count: int
    last_email_activity: datetime | None

    @property
    def days_since_email(self) -> int | None:
        """Days since last email activity."""
        if not self.last_email_activity:
            return None
        return (datetime.now() - self.last_email_activity).days

    @property
    def days_since_created(self) -> int | None:
        """Days since group was created."""
        if not self.created_date:
            return None
        return (datetime.now() - self.created_date).days


# =============================================================================
# Summary Report Functions
# =============================================================================


async def get_group_member_count(client, group_id: str) -> int:
    """Get member count for a group."""
    try:
        result = await client.groups.by_group_id(group_id).members.get()
        if result and result.value:
            return len(result.value)
    except Exception:
        logger.debug("Error getting member count")
    return 0


async def get_group_drive_info(client, group_id: str) -> dict:
    """Get OneDrive/SharePoint drive info for a group."""
    try:
        drive = await client.groups.by_group_id(group_id).drive.get()
        if drive:
            quota_used = 0
            if drive.quota and drive.quota.used:
                quota_used = drive.quota.used

            return {
                "drive_id": drive.id,
                "quota_used": quota_used,
                "web_url": drive.web_url,
            }
    except Exception:
        # Group may not have a drive provisioned
        logger.debug("Error getting drive info (group may not have a drive)")
    return {}


async def count_drive_files_recursive(
    client, drive_id: str, item_id: str = "root", *, limit: int | None = None
) -> tuple[int, bool]:
    """Recursively count all files in a drive.

    Args:
        client: Graph client.
        drive_id: The drive ID (not group ID).
        item_id: The item ID to start from ("root" for root folder).
        limit: Stop counting if this limit is exceeded. None for no limit.

    Returns:
        Tuple of (count, limit_exceeded). If limit_exceeded is True, count is the limit value.
    """
    count = 0
    try:
        result = (
            await client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).children.get()
        )

        if result and result.value:
            for item in result.value:
                if item.folder:
                    # It's a folder - recurse into it
                    if item.folder.child_count and item.folder.child_count > 0:
                        sub_count, exceeded = await count_drive_files_recursive(
                            client, drive_id, item.id, limit=limit - count if limit else None
                        )
                        count += sub_count
                        if exceeded:
                            return (limit, True)
                else:
                    # It's a file
                    count += 1
                    if limit and count >= limit:
                        return (limit, True)
    except Exception:
        logger.debug("Error counting drive files")
    return (count, False)


async def get_group_conversations_info(client, group_id: str) -> dict:
    """Get conversation info including count and last activity date."""
    try:
        convos = await client.groups.by_group_id(group_id).conversations.get()
        if convos and convos.value:
            # Find the most recent lastDeliveredDateTime
            last_activity = None
            for convo in convos.value:
                if convo.last_delivered_date_time:
                    dt = convo.last_delivered_date_time.replace(tzinfo=None)
                    if last_activity is None or dt > last_activity:
                        last_activity = dt
            return {
                "count": len(convos.value),
                "last_activity": last_activity,
            }
    except Exception:
        logger.debug("Error getting conversations info")
    return {"count": 0, "last_activity": None}


async def get_all_m365_groups(
    client, *, count_files: bool = False, file_count_limit: int | None = 500
) -> list[dict]:
    """Fetch all M365 groups with basic info."""
    query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
        filter="groupTypes/any(c:c eq 'Unified')",  # M365 groups only
        select=[
            "id",
            "displayName",
            "mail",
            "createdDateTime",
            "renewedDateTime",
            "description",
        ],
        top=999,
    )
    config = RequestConfiguration(query_parameters=query_params)
    result = await client.groups.get(request_configuration=config)

    groups = []
    if result and result.value:
        groups.extend(result.value)

    # Handle pagination
    while result and result.odata_next_link:
        result = await client.groups.with_url(result.odata_next_link).get()
        if result and result.value:
            groups.extend(result.value)

    # Fetch member counts, drive info, and conversations for each group
    logger.debug("Fetching member counts, drive info, and conversations...")
    group_list = []
    for i, g in enumerate(groups):
        logger.debug(f"  [{i + 1}/{len(groups)}] {g.display_name}")
        member_count = await get_group_member_count(client, g.id)
        drive_info = await get_group_drive_info(client, g.id)
        convo_info = await get_group_conversations_info(client, g.id)

        # Optionally count files recursively (slow)
        file_count = 0
        file_count_exceeded = False
        if count_files and drive_info.get("drive_id"):
            file_count, file_count_exceeded = await count_drive_files_recursive(
                client, drive_info["drive_id"], limit=file_count_limit
            )

        group_list.append(
            {
                "id": g.id,
                "display_name": g.display_name,
                "mail": g.mail,
                "created_date": (
                    g.created_date_time.replace(tzinfo=None) if g.created_date_time else None
                ),
                "renewed_date": (
                    g.renewed_date_time.replace(tzinfo=None) if g.renewed_date_time else None
                ),
                "description": g.description,
                "member_count": member_count,
                "drive_info": drive_info,
                "file_count": file_count,
                "file_count_exceeded": file_count_exceeded,
                "conversation_count": convo_info["count"],
                "last_email_activity": convo_info["last_activity"],
            }
        )

    return group_list


async def build_usage_report(
    *, count_files: bool = False, file_count_limit: int | None = 500
) -> list[GroupUsageInfo]:
    """Build complete usage report for all M365 groups."""
    client = get_graph_client()

    groups = await get_all_m365_groups(
        client, count_files=count_files, file_count_limit=file_count_limit
    )

    report = []
    for g in groups:
        drive_info = g.get("drive_info", {})
        report.append(
            GroupUsageInfo(
                id=g["id"],
                display_name=g["display_name"],
                mail=g["mail"],
                member_count=g.get("member_count", 0),
                created_date=g["created_date"],
                drive_storage_bytes=drive_info.get("quota_used", 0),
                drive_file_count=g.get("file_count", 0),
                drive_file_count_exceeded=g.get("file_count_exceeded", False),
                conversation_count=g.get("conversation_count", 0),
                last_email_activity=g.get("last_email_activity"),
            )
        )

    return report


def format_size(bytes_val: int) -> str:
    """Format bytes as human readable."""
    if bytes_val == 0:
        return "0"
    if bytes_val < 1024:
        return f"{bytes_val}B"
    if bytes_val < 1024 * 1024:
        return f"{bytes_val // 1024}KB"
    if bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val // (1024 * 1024)}MB"
    return f"{bytes_val // (1024 * 1024 * 1024)}GB"


def print_summary_report(report: list[GroupUsageInfo], *, show_files: bool = False) -> None:
    """Print the summary usage report."""

    # Sort by last email activity (None/never first, then oldest)
    def sort_key(g: GroupUsageInfo):
        if g.last_email_activity is None:
            return (0, datetime.min)  # Never emailed first
        return (1, g.last_email_activity)

    sorted_by_email = sorted(report, key=sort_key)

    # Summary stats
    total = len(report)
    empty_groups = sum(1 for g in report if g.member_count == 0)
    single_member = sum(1 for g in report if g.member_count == 1)
    never_emailed = sum(1 for g in report if g.last_email_activity is None)
    no_email_90d = sum(1 for g in report if g.days_since_email and g.days_since_email > 90)
    no_email_180d = sum(1 for g in report if g.days_since_email and g.days_since_email > 180)
    no_email_365d = sum(1 for g in report if g.days_since_email and g.days_since_email > 365)
    no_sp_storage = sum(1 for g in report if g.drive_storage_bytes == 0)

    print()
    print("M365 GROUP USAGE REPORT")
    print()
    print(f"  Total groups:        {total}")
    print(f"  Empty (0 members):   {empty_groups}")
    print(f"  Single member:       {single_member}")
    print(f"  Never emailed:       {never_emailed}")
    print(f"  No email 90+ days:   {no_email_90d}")
    print(f"  No email 180+ days:  {no_email_180d}")
    print(f"  No email 365+ days:  {no_email_365d}")
    print(f"  No SP storage:       {no_sp_storage}")

    # Table formatting
    baseline_bytes = 1.5 * 1024 * 1024  # ~1.5MB is baseline (empty site)

    # Column widths
    col_name = 38
    col_created = 10
    col_members = 7
    col_email = 12
    col_storage = 8
    col_files = 5
    col_convos = 6

    # Build separator and header (conditionally include Files column)
    if show_files:
        sep = (
            f"+{'-' * (col_name + 2)}"
            f"+{'-' * (col_created + 2)}"
            f"+{'-' * (col_members + 2)}"
            f"+{'-' * (col_email + 2)}"
            f"+{'-' * (col_storage + 2)}"
            f"+{'-' * (col_files + 2)}"
            f"+{'-' * (col_convos + 2)}+"
        )
        header = (
            f"| {'Group Name':<{col_name}} "
            f"| {'Created':<{col_created}} "
            f"| {'Members':>{col_members}} "
            f"| {'Last Email':<{col_email}} "
            f"| {'Storage':>{col_storage}} "
            f"| {'Files':>{col_files}} "
            f"| {'Emails':>{col_convos}} |"
        )
    else:
        sep = (
            f"+{'-' * (col_name + 2)}"
            f"+{'-' * (col_created + 2)}"
            f"+{'-' * (col_members + 2)}"
            f"+{'-' * (col_email + 2)}"
            f"+{'-' * (col_storage + 2)}"
            f"+{'-' * (col_convos + 2)}+"
        )
        header = (
            f"| {'Group Name':<{col_name}} "
            f"| {'Created':<{col_created}} "
            f"| {'Members':>{col_members}} "
            f"| {'Last Email':<{col_email}} "
            f"| {'Storage':>{col_storage}} "
            f"| {'Emails':>{col_convos}} |"
        )

    print()
    print("ALL GROUPS (sorted by last email activity, oldest first)")
    print(sep)
    print(header)
    print(sep)

    for g in sorted_by_email:
        created = g.created_date.strftime("%Y-%m-%d") if g.created_date else "-"
        last_email = g.last_email_activity.strftime("%Y-%m-%d") if g.last_email_activity else "-"

        # Show storage, marking baseline as "-"
        if g.drive_storage_bytes <= baseline_bytes:
            storage = "-"
        else:
            storage = format_size(g.drive_storage_bytes)

        if g.drive_file_count > 0:
            if g.drive_file_count_exceeded:
                files = f"{g.drive_file_count}+"
            else:
                files = str(g.drive_file_count)
        else:
            files = "-"
        convos = str(g.conversation_count) if g.conversation_count > 0 else "-"

        if show_files:
            row = (
                f"| {g.display_name[:col_name]:<{col_name}} "
                f"| {created:<{col_created}} "
                f"| {g.member_count:>{col_members}} "
                f"| {last_email:<{col_email}} "
                f"| {storage:>{col_storage}} "
                f"| {files:>{col_files}} "
                f"| {convos:>{col_convos}} |"
            )
        else:
            row = (
                f"| {g.display_name[:col_name]:<{col_name}} "
                f"| {created:<{col_created}} "
                f"| {g.member_count:>{col_members}} "
                f"| {last_email:<{col_email}} "
                f"| {storage:>{col_storage}} "
                f"| {convos:>{col_convos}} |"
            )
        print(row)

    print(sep)

    # Cleanup candidates - no email activity and empty
    safe_to_delete = [
        g
        for g in sorted_by_email
        if g.member_count == 0 and g.drive_storage_bytes == 0 and g.last_email_activity is None
    ]

    if safe_to_delete:
        print()
        print(f"SAFE TO DELETE ({len(safe_to_delete)} groups with no members/storage/email)")
        for g in safe_to_delete:
            print(f"  - {g.display_name} ({g.mail})")

    # Review candidates - no email in over a year
    review_candidates = [
        g
        for g in sorted_by_email
        if (g.days_since_email and g.days_since_email > 365) and g not in safe_to_delete
    ]

    if review_candidates:
        print()
        print(f"REVIEW CANDIDATES ({len(review_candidates)} groups with no email in 365+ days)")
        for g in review_candidates:
            days = g.days_since_email
            storage = format_size(g.drive_storage_bytes)
            print(f"  - {g.display_name}: {days} days, {g.member_count} members, {storage}")


def export_csv(report: list[GroupUsageInfo], filename: str) -> None:
    """Export report to CSV file."""
    from pathlib import Path

    with Path(filename).open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Group Name",
                "Email",
                "Created",
                "Last Email",
                "Days Since Email",
                "Members",
                "Storage MB",
                "Files",
                "Conversations",
            ]
        )
        for g in report:
            writer.writerow(
                [
                    g.display_name,
                    g.mail,
                    g.created_date.strftime("%Y-%m-%d") if g.created_date else "",
                    g.last_email_activity.strftime("%Y-%m-%d") if g.last_email_activity else "",
                    g.days_since_email or "",
                    g.member_count,
                    f"{g.drive_storage_bytes / (1024 * 1024):.1f}",
                    g.drive_file_count,
                    g.conversation_count,
                ]
            )
    print(f"Exported to {filename}")


# =============================================================================
# Deep Scan Functions (from station_group_scan.py)
# =============================================================================


async def get_groups_by_names(client, names: list[str]) -> list[dict]:
    """Fetch specific groups by display name."""
    groups = []
    for name in names:
        query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
            filter=f"displayName eq '{name}'",
            select=["id", "displayName", "mail", "createdDateTime"],
        )
        config = RequestConfiguration(query_parameters=query_params)
        result = await client.groups.get(request_configuration=config)

        if result and result.value:
            g = result.value[0]
            groups.append(
                {
                    "id": g.id,
                    "display_name": g.display_name,
                    "mail": g.mail,
                    "created": g.created_date_time,
                }
            )
        else:
            print(f"Warning: Group not found: {name}")
    return groups


async def get_all_m365_groups_basic(client) -> list[dict]:
    """Fetch all M365 groups with basic info (for deep scan)."""
    query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
        filter="groupTypes/any(c:c eq 'Unified')",
        select=["id", "displayName", "mail", "createdDateTime"],
        top=999,
    )
    config = RequestConfiguration(query_parameters=query_params)
    result = await client.groups.get(request_configuration=config)

    groups = []
    if result and result.value:
        groups.extend(
            [
                {
                    "id": g.id,
                    "display_name": g.display_name,
                    "mail": g.mail,
                    "created": g.created_date_time,
                }
                for g in result.value
            ]
        )

    # Handle pagination
    while result and result.odata_next_link:
        result = await client.groups.with_url(result.odata_next_link).get()
        if result and result.value:
            groups.extend(
                [
                    {
                        "id": g.id,
                        "display_name": g.display_name,
                        "mail": g.mail,
                        "created": g.created_date_time,
                    }
                    for g in result.value
                ]
            )

    return sorted(groups, key=lambda x: x["display_name"])


async def get_drive_contents(client, drive_id: str, item_id: str = "root") -> list[dict]:
    """Recursively get all items in a drive.

    Args:
        client: Graph client.
        drive_id: The drive ID (not group ID).
        item_id: The item ID to start from ("root" for root folder).
    """
    items = []
    try:
        result = (
            await client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).children.get()
        )

        if result and result.value:
            for item in result.value:
                item_info = {
                    "name": item.name,
                    "type": "folder" if item.folder else "file",
                    "size": item.size or 0,
                    "created": item.created_date_time,
                    "modified": item.last_modified_date_time,
                    "web_url": item.web_url,
                }
                items.append(item_info)

                # Recurse into folders
                if item.folder and item.folder.child_count and item.folder.child_count > 0:
                    sub_items = await get_drive_contents(client, drive_id, item.id)
                    for sub in sub_items:
                        sub["name"] = f"{item.name}/{sub['name']}"
                        items.append(sub)
    except Exception as e:
        logger.debug(f"Error getting drive contents: {e}")
    return items


async def get_site_pages(client, group_id: str) -> list[dict]:
    """Get SharePoint pages for a group's site."""
    pages = []
    try:
        # Get the group's SharePoint site
        site = await client.groups.by_group_id(group_id).sites.get()
        if site and site.value:
            site_id = site.value[0].id
            # Try to get pages from the site
            # Pages are stored in the "Site Pages" library
            site_pages = await client.sites.by_site_id(site_id).pages.get()
            if site_pages and site_pages.value:
                pages.extend(
                    [
                        {
                            "name": page.name,
                            "title": getattr(page, "title", None),
                            "created": getattr(page, "created_date_time", None),
                            "modified": getattr(page, "last_modified_date_time", None),
                            "web_url": getattr(page, "web_url", None),
                        }
                        for page in site_pages.value
                    ]
                )
    except Exception as e:
        logger.debug(f"Error getting site pages: {e}")
    return pages


async def get_email_history(client, group_id: str) -> list[dict]:
    """Get conversation/email history for a group."""
    emails = []
    try:
        convos = await client.groups.by_group_id(group_id).conversations.get()
        if convos and convos.value:
            emails.extend(
                [
                    {
                        "topic": convo.topic,
                        "last_delivered": convo.last_delivered_date_time,
                        "has_attachments": convo.has_attachments,
                        "preview": convo.preview[:100] if convo.preview else None,
                    }
                    for convo in convos.value
                ]
            )
    except Exception as e:
        logger.debug(f"Error getting conversations: {e}")
    return emails


def print_deep_scan_group(
    group: dict, files: list[dict], pages: list[dict], emails: list[dict]
) -> None:
    """Print deep scan results for a single group."""
    print("=" * 80)
    print(f"  {group['display_name']}")
    print(f"  Email: {group['mail']}")
    print(f"  Created: {group['created'].strftime('%Y-%m-%d') if group['created'] else 'Unknown'}")
    print("=" * 80)

    # SharePoint files
    print("\n  SHAREPOINT FILES:")
    if not files:
        print("    (no files)")
    else:
        # Filter out common default items
        default_names = {"General", "Forms", "Shared Documents", "_catalogs", "_cts"}
        real_files = [f for f in files if f["name"].split("/")[0] not in default_names]
        default_files = [f for f in files if f["name"].split("/")[0] in default_names]

        if real_files:
            print(f"    User files ({len(real_files)}):")
            for f in sorted(real_files, key=lambda x: x["name"])[:20]:  # Show first 20
                if f["size"] < 1024 * 1024:
                    size_str = f"{f['size'] // 1024}KB"
                else:
                    size_str = f"{f['size'] // (1024 * 1024)}MB"
                mod_date = f["modified"].strftime("%Y-%m-%d") if f["modified"] else "?"
                ftype = f["type"][:1].upper()
                fname = f["name"][:50]
                print(f"      [{ftype}] {fname:<50} {size_str:>8}  {mod_date}")
            if len(real_files) > 20:
                print(f"      ... and {len(real_files) - 20} more files")
        else:
            print("    (no user files - only defaults)")

        if default_files:
            folder_names = {f["name"].split("/")[0] for f in default_files}
            print(f"    Default folders: {', '.join(folder_names)}")

    # SharePoint pages
    print("\n  SHAREPOINT PAGES:")
    if not pages:
        print("    (no custom pages)")
    else:
        for p in pages:
            print(f"    - {p['title'] or p['name']}")

    # Email history
    print("\n  EMAIL HISTORY:")
    if not emails:
        print("    (no emails)")
    else:
        # Sort by date, newest first
        emails_sorted = sorted(
            emails, key=lambda x: x["last_delivered"] or datetime.min, reverse=True
        )
        print(f"    Total conversations: {len(emails_sorted)}")
        print("    Recent emails:")
        for e in emails_sorted[:10]:  # Show last 10
            date_str = e["last_delivered"].strftime("%Y-%m-%d") if e["last_delivered"] else "?"
            topic = (e["topic"] or "(no subject)")[:60]
            attach = " [+attach]" if e["has_attachments"] else ""
            print(f"      {date_str}  {topic}{attach}")
        if len(emails_sorted) > 10:
            oldest = emails_sorted[-1]["last_delivered"]
            oldest_str = oldest.strftime("%Y-%m-%d") if oldest else "?"
            print(f"      ... and {len(emails_sorted) - 10} more (oldest: {oldest_str})")

    print()


async def run_deep_scan(group_names: list[str] | None, scan_all: bool) -> None:
    """Run deep scan on specified groups.

    Args:
        group_names: List of specific group names to scan.
        scan_all: If True, scan all M365 groups.
    """
    client = get_graph_client()

    if scan_all:
        groups = await get_all_m365_groups_basic(client)
    elif group_names:
        groups = await get_groups_by_names(client, group_names)
    else:
        # Should not happen due to CLI validation
        print("Error: No groups specified for deep scan")
        return

    print(f"Scanning {len(groups)} groups...\n")

    for i, group in enumerate(groups):
        logger.debug(f"[{i + 1}/{len(groups)}] Scanning {group['display_name']}...")

        # Get drive ID first
        drive_info = await get_group_drive_info(client, group["id"])
        drive_id = drive_info.get("drive_id")

        files = await get_drive_contents(client, drive_id) if drive_id else []
        pages = await get_site_pages(client, group["id"])
        emails = await get_email_history(client, group["id"])
        print_deep_scan_group(group, files, pages, emails)


# =============================================================================
# CLI Entry Point
# =============================================================================


async def async_main() -> None:
    """Async main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Scan M365 groups for usage analysis and detailed content inspection.",
        epilog="""
Examples:
  %(prog)s                          Summary report of all M365 groups
  %(prog)s --csv report.csv         Export summary to CSV
  %(prog)s --file-count             Include file counts (stops at 500)
  %(prog)s --full-file-count        Include exact file counts (slower)
  %(prog)s --deep "Station 31"      Deep scan specific group
  %(prog)s --deep --all             Deep scan all groups (slow)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--deep",
        nargs="*",
        metavar="GROUP",
        help="Deep scan specific group(s) by name. Shows files, pages, emails.",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Include all groups. With --deep: scan all groups deeply.",
    )
    parser.add_argument(
        "--csv",
        type=str,
        metavar="FILE",
        help="Export summary report to CSV file.",
    )
    parser.add_argument(
        "--file-count",
        action="store_true",
        help="Count files in SharePoint (stops at 500, shows 500+).",
    )
    parser.add_argument(
        "--full-file-count",
        action="store_true",
        help="Count all files in SharePoint with no limit (slow for large libraries).",
    )
    args = parser.parse_args()

    # Determine mode
    if args.deep is not None:
        # Deep scan mode
        if args.all:
            # Deep scan all groups
            await run_deep_scan(group_names=None, scan_all=True)
        elif args.deep:
            # Deep scan specific groups
            await run_deep_scan(group_names=args.deep, scan_all=False)
        else:
            # --deep with no arguments and no --all
            parser.error("--deep requires group names or --all flag")
    else:
        # Summary report mode (default)
        count_files = args.file_count or args.full_file_count
        file_count_limit = None if args.full_file_count else 500
        report = await build_usage_report(
            count_files=count_files, file_count_limit=file_count_limit
        )

        if args.csv:
            export_csv(report, args.csv)
        else:
            print_summary_report(report, show_files=count_files)


def main() -> None:
    """CLI entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
