#!/usr/bin/env python3
"""Scan M365 groups for usage analysis and detailed content inspection."""

import asyncio
import csv
import io
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
    group_type: str
    member_count: int
    created_date: datetime | None
    renewed_date: datetime | None
    last_activity_date: datetime | None
    exchange_emails_received: int
    exchange_mailbox_storage_mb: float
    sharepoint_active_files: int
    sharepoint_total_files: int
    sharepoint_storage_mb: float
    yammer_messages: int
    is_deleted: bool
    # Additional fields from direct queries
    drive_storage_bytes: int = 0
    drive_file_count: int = 0
    conversation_count: int = 0
    last_email_activity: datetime | None = None

    @property
    def days_since_email(self) -> int | None:
        """Days since last email activity."""
        if not self.last_email_activity:
            return None
        return (datetime.now() - self.last_email_activity).days

    @property
    def days_since_activity(self) -> int | None:
        """Days since last activity."""
        if not self.last_activity_date:
            return None
        return (datetime.now() - self.last_activity_date).days

    @property
    def days_since_created(self) -> int | None:
        """Days since group was created."""
        if not self.created_date:
            return None
        return (datetime.now() - self.created_date).days


# =============================================================================
# Summary Report Functions (from group_usage_report.py)
# =============================================================================


async def get_group_activity_report(client) -> dict[str, dict]:
    """Fetch Office 365 Groups activity detail report.

    Returns dict keyed by group ID with activity metrics.
    """
    # Get the last 180 days of activity (max period)
    try:
        # Use the reports endpoint directly
        report = await client.reports.get_office365_groups_activity_detail_with_period("D180").get()

        if not report:
            logger.warning("No activity report returned")
            return {}

        # The report is returned as CSV content
        content = report.decode("utf-8") if isinstance(report, bytes) else report
        reader = csv.DictReader(io.StringIO(content))

        activity_by_group: dict[str, dict] = {}
        for row in reader:
            group_id = row.get("Group Id", "")
            if group_id:
                activity_by_group[group_id] = {
                    "last_activity_date": parse_date(row.get("Last Activity Date")),
                    "exchange_emails_received": int(row.get("Exchange Emails Received", 0) or 0),
                    "exchange_mailbox_storage_mb": float(
                        row.get("Exchange Mailbox Storage Used (Byte)", 0) or 0
                    )
                    / (1024 * 1024),
                    "sharepoint_active_files": int(row.get("SharePoint Active File Count", 0) or 0),
                    "sharepoint_total_files": int(row.get("SharePoint Total File Count", 0) or 0),
                    "sharepoint_storage_mb": float(
                        row.get("SharePoint Site Storage Used (Byte)", 0) or 0
                    )
                    / (1024 * 1024),
                    "yammer_messages": int(row.get("Yammer Posted Message Count", 0) or 0)
                    + int(row.get("Yammer Read Message Count", 0) or 0)
                    + int(row.get("Yammer Liked Message Count", 0) or 0),
                    "is_deleted": row.get("Is Deleted", "").lower() == "true",
                    "member_count": int(row.get("Member Count", 0) or 0),
                }

        return activity_by_group

    except Exception as e:
        # Check for 403 permission error
        error_str = str(e)
        if "403" in error_str or "S2SUnauthorized" in error_str:
            logger.warning("Skipping activity report (requires Reports.Read.All permission)")
        else:
            logger.error(f"Failed to get activity report: {e}")
        return {}


def parse_date(date_str: str | None) -> datetime | None:
    """Parse a date string from the report."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


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

            # Get root folder to check childCount (items at top level)
            root_child_count = 0
            try:
                root = await client.groups.by_group_id(group_id).drive.root.get()
                if root and root.folder and root.folder.child_count:
                    root_child_count = root.folder.child_count
            except Exception:
                logger.debug("Error getting root folder info")

            return {
                "drive_id": drive.id,
                "quota_used": quota_used,
                "web_url": drive.web_url,
                "root_child_count": root_child_count,
            }
    except Exception:
        # Group may not have a drive provisioned
        logger.debug("Error getting drive info (group may not have a drive)")
    return {}


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


async def get_all_m365_groups(client) -> list[dict]:
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
    logger.info("Fetching member counts, drive info, and conversations...")
    group_list = []
    for i, g in enumerate(groups):
        logger.info(f"  [{i + 1}/{len(groups)}] {g.display_name}")
        member_count = await get_group_member_count(client, g.id)
        drive_info = await get_group_drive_info(client, g.id)
        convo_info = await get_group_conversations_info(client, g.id)
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
                "conversation_count": convo_info["count"],
                "last_email_activity": convo_info["last_activity"],
            }
        )

    return group_list


async def build_usage_report() -> list[GroupUsageInfo]:
    """Build complete usage report for all M365 groups."""
    client = get_graph_client()

    logger.info("Fetching M365 groups...")
    groups = await get_all_m365_groups(client)
    logger.info(f"Found {len(groups)} M365 groups")

    logger.info("Fetching activity report (last 180 days)...")
    activity = await get_group_activity_report(client)
    logger.info(f"Got activity data for {len(activity)} groups")

    report = []
    for g in groups:
        group_id = g["id"]
        act = activity.get(group_id, {})
        drive_info = g.get("drive_info", {})

        # Use member count from group fetch if not in activity report
        member_count = act.get("member_count") or g.get("member_count", 0)

        report.append(
            GroupUsageInfo(
                id=group_id,
                display_name=g["display_name"],
                mail=g["mail"],
                group_type="M365",
                member_count=member_count,
                created_date=g["created_date"],
                renewed_date=g["renewed_date"],
                last_activity_date=act.get("last_activity_date") or g["renewed_date"],
                exchange_emails_received=act.get("exchange_emails_received", 0),
                exchange_mailbox_storage_mb=act.get("exchange_mailbox_storage_mb", 0),
                sharepoint_active_files=act.get("sharepoint_active_files", 0),
                sharepoint_total_files=act.get("sharepoint_total_files", 0),
                sharepoint_storage_mb=act.get("sharepoint_storage_mb", 0),
                yammer_messages=act.get("yammer_messages", 0),
                is_deleted=act.get("is_deleted", False),
                drive_storage_bytes=drive_info.get("quota_used", 0),
                drive_file_count=drive_info.get("root_child_count", 0),
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


def print_summary_report(report: list[GroupUsageInfo]) -> None:
    """Print the summary usage report."""

    # Sort by last activity (None = never active, then oldest first)
    def sort_key(g: GroupUsageInfo):
        if g.last_activity_date is None:
            return (0, datetime.min)  # Never active first
        return (1, g.last_activity_date)

    sorted_report = sorted(report, key=sort_key)

    print("\n" + "=" * 120)
    print("M365 GROUP USAGE REPORT")
    print("=" * 120)

    # Summary stats
    total = len(report)
    empty_groups = sum(1 for g in report if g.member_count == 0)
    single_member = sum(1 for g in report if g.member_count == 1)
    never_emailed = sum(1 for g in report if g.last_email_activity is None)
    no_email_90d = sum(1 for g in report if g.days_since_email and g.days_since_email > 90)
    no_email_180d = sum(1 for g in report if g.days_since_email and g.days_since_email > 180)
    no_email_365d = sum(1 for g in report if g.days_since_email and g.days_since_email > 365)
    no_sp_storage = sum(1 for g in report if g.drive_storage_bytes == 0)

    print(f"\nTotal M365 Groups: {total}")
    print(f"Empty groups (0 members): {empty_groups}")
    print(f"Single member groups: {single_member}")
    print(f"Never received email: {never_emailed}")
    print(f"No email in 90+ days: {no_email_90d}")
    print(f"No email in 180+ days: {no_email_180d}")
    print(f"No email in 365+ days: {no_email_365d}")
    print(f"No SharePoint storage: {no_sp_storage}")

    # Detailed listing - sorted by last email activity (oldest first)
    print("\n" + "-" * 120)
    print("ALL GROUPS BY LAST EMAIL ACTIVITY (oldest first)")
    print("-" * 120)

    header = f"{'Group Name':<35} {'Members':<8} {'Last Email':<12} "
    header += f"{'SP Size':<10} {'SP Files':<9} {'Convos':<8}"
    print(header)
    print("-" * 120)

    # Sort by last email activity (None/never first, then oldest)
    def email_sort_key(g: GroupUsageInfo):
        if g.last_email_activity is None:
            return (0, datetime.min)  # Never emailed first
        return (1, g.last_email_activity)

    sorted_by_email = sorted(sorted_report, key=email_sort_key)

    # Calculate baseline (minimum storage seen)
    baseline_bytes = 1.5 * 1024 * 1024  # Assume ~1.5MB is baseline

    for g in sorted_by_email:
        if g.last_email_activity:
            last_email = g.last_email_activity.strftime("%Y-%m-%d")
        else:
            last_email = "Never"

        # Show storage, marking baseline as "~base"
        if g.drive_storage_bytes <= baseline_bytes:
            storage = "~base"
        else:
            storage = format_size(g.drive_storage_bytes)

        files = str(g.drive_file_count) if g.drive_file_count > 0 else "-"

        row = f"{g.display_name[:34]:<35} {g.member_count:<8} {last_email:<12} "
        row += f"{storage:<10} {files:<9} {g.conversation_count:<8}"
        print(row)

    # Cleanup candidates - no email activity and empty
    print("\n" + "=" * 120)
    print("SAFE TO DELETE - No members, no SharePoint data, never emailed")
    print("=" * 120)

    safe_to_delete = [
        g
        for g in sorted_by_email
        if g.member_count == 0 and g.drive_storage_bytes == 0 and g.last_email_activity is None
    ]

    if not safe_to_delete:
        print("No completely empty groups found!")
    else:
        for g in safe_to_delete:
            print(f"\n  {g.display_name}")
            print(f"    Email: {g.mail}")
            created = g.created_date.strftime("%Y-%m-%d") if g.created_date else "Unknown"
            print(f"    Created: {created}")

    # Review candidates - no email in over a year
    print("\n" + "=" * 120)
    print("REVIEW CANDIDATES - No email activity in 365+ days")
    print("=" * 120)

    review_candidates = [
        g
        for g in sorted_by_email
        if (g.days_since_email and g.days_since_email > 365) and g not in safe_to_delete
    ]

    if not review_candidates:
        print("No groups with 365+ days of email inactivity!")
    else:
        for g in review_candidates:
            if g.last_email_activity:
                last_email = g.last_email_activity.strftime("%Y-%m-%d")
            else:
                last_email = "Never"
            print(f"\n  {g.display_name}")
            print(f"    Email: {g.mail}")
            print(f"    Last email: {last_email} ({g.days_since_email} days ago)")
            storage = format_size(g.drive_storage_bytes)
            print(f"    Members: {g.member_count}, SP Storage: {storage}")


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
                "Last Activity",
                "Days Inactive",
                "Members",
                "Emails Received",
                "SP Files",
                "SP Storage MB",
                "Exchange Storage MB",
            ]
        )
        for g in report:
            writer.writerow(
                [
                    g.display_name,
                    g.mail,
                    g.created_date.strftime("%Y-%m-%d") if g.created_date else "",
                    g.last_activity_date.strftime("%Y-%m-%d") if g.last_activity_date else "",
                    g.days_since_activity or "",
                    g.member_count,
                    g.exchange_emails_received,
                    g.sharepoint_total_files,
                    f"{g.sharepoint_storage_mb:.1f}",
                    f"{g.exchange_mailbox_storage_mb:.1f}",
                ]
            )
    logger.info(f"Exported to {filename}")


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
            logger.warning(f"Group not found: {name}")
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


async def get_drive_contents(client, group_id: str, path: str = "root") -> list[dict]:
    """Recursively get all items in a drive."""
    items = []
    try:
        if path == "root":
            result = await client.groups.by_group_id(group_id).drive.root.children.get()
        else:
            drive_item = client.groups.by_group_id(group_id).drive.items.by_drive_item_id(path)
            result = await drive_item.children.get()

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
                    sub_items = await get_drive_contents(client, group_id, item.id)
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
        logger.info("Fetching all M365 groups for deep scan...")
        groups = await get_all_m365_groups_basic(client)
    elif group_names:
        logger.info(f"Fetching groups: {', '.join(group_names)}...")
        groups = await get_groups_by_names(client, group_names)
    else:
        # Should not happen due to CLI validation
        logger.error("No groups specified for deep scan")
        return

    logger.info(f"Found {len(groups)} groups\n")

    for i, group in enumerate(groups):
        logger.info(f"[{i + 1}/{len(groups)}] Scanning {group['display_name']}...")
        files = await get_drive_contents(client, group["id"])
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
  %(prog)s --deep "Station 31"      Deep scan specific group
  %(prog)s --deep "Station 31" "Firefighters"   Deep scan multiple groups
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
        report = await build_usage_report()

        if args.csv:
            export_csv(report, args.csv)
        else:
            print_summary_report(report)


def main() -> None:
    """CLI entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
