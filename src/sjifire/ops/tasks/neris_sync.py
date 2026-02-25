"""NERIS report sync task.

Fetches incident summaries from the NERIS API and writes them to
the ``neris-reports`` Cosmos DB container.  All server instances
then read from Cosmos (never the API directly).

This is the single canonical version of the NERIS summary extraction
logic — dashboard.py and incidents/tools.py both delegate here.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sjifire.ops.tasks.registry import register

# The NERIS API's last_modified query *filter* is broken as of 2026-02
# (rejects all formats with "Invalid relative datetime expression").
# Workaround: sort by last_modified descending and stop paginating
# once we see records older than our checkpoint.

logger = logging.getLogger(__name__)


def _extract_summary(inc: dict) -> dict:
    """Extract a summary dict from a raw NERIS incident."""
    dispatch = inc.get("dispatch", {})
    types = inc.get("incident_types", [])
    status_info = inc.get("incident_status", {})

    return {
        "source": "neris",
        "neris_id": inc.get("neris_id", ""),
        "incident_number": dispatch.get("incident_number", ""),
        "determinant_code": str(dispatch.get("determinant_code") or ""),
        "status": status_info.get("status", ""),
        "incident_type": types[0].get("type", "") if types else "",
        "call_create": dispatch.get("call_create", ""),
    }


def fetch_neris_summaries(*, since: str | None = None) -> list[dict]:
    """Fetch NERIS incidents and extract summary dicts.

    This is a **blocking** call (uses ``NerisClient`` which is sync).
    Run via ``asyncio.to_thread()`` in async contexts.

    When ``since`` is provided, fetches pages sorted by last_modified
    descending and stops once all records on a page are older than
    the cutoff. This avoids fetching the full history on every sync.

    When ``since`` is None, fetches all incidents (first run).

    Args:
        since: ISO timestamp cutoff. Only incidents modified after
            this time are returned.

    Returns:
        List of summary dicts with keys: neris_id, incident_number,
        determinant_code, status, incident_type, call_create.
    """
    from sjifire.neris.client import NerisClient

    with NerisClient() as client:
        if since is None:
            # First run: fetch everything
            incidents = client.get_all_incidents()
            return [_extract_summary(inc) for inc in incidents]

        # Incremental: page through newest-first, stop when all
        # records on a page are older than the checkpoint.
        summaries: list[dict] = []
        cursor = None

        while True:
            result = client.list_incidents(
                page_size=100,
                cursor=cursor,
                sort_by="last_modified",
                sort_direction="DESCENDING",
            )
            incidents = result.get("incidents", [])
            if not incidents:
                break

            page_has_new = False
            for inc in incidents:
                modified = inc.get("last_modified", "")
                if modified > since:
                    summaries.append(_extract_summary(inc))
                    page_has_new = True

            # If no record on this page was newer than the checkpoint,
            # everything beyond is older — stop paginating.
            if not page_has_new:
                break

            cursor = result.get("next_cursor")
            if not cursor:
                break

        return summaries


async def refresh_neris_report_cache(summaries: list[dict]) -> int:
    """Write NERIS summaries to the Cosmos DB cache.

    Args:
        summaries: List of summary dicts from ``fetch_neris_summaries()``

    Returns:
        Number of documents written
    """
    from sjifire.ops.neris.store import NerisReportStore

    async with NerisReportStore() as store:
        count = await store.bulk_upsert(summaries)

    logger.info("Wrote %d NERIS reports to cache", count)
    return count


async def _sync_neris_to_local(summaries: list[dict]) -> int:
    """Log NERIS status changes for local incidents (informational only).

    The sync task never auto-transitions local incident status.
    Approval is a manual action performed by the chief upon review.

    Args:
        summaries: List of summary dicts from ``fetch_neris_summaries()``

    Returns:
        Always 0 (no transitions performed)
    """
    from sjifire.ops.incidents.store import IncidentStore

    async with IncidentStore() as store:
        for summary in summaries:
            neris_id = summary.get("neris_id", "")
            neris_status = summary.get("status", "")
            if not neris_id or neris_status != "APPROVED":
                continue

            doc = await store.get_by_neris_id(neris_id)
            if doc is None:
                continue

            if doc.status != "approved":
                logger.info(
                    "NERIS %s is APPROVED but local %s is '%s' — "
                    "awaiting manual chief review",
                    neris_id,
                    doc.incident_number,
                    doc.status,
                )

    return 0


@register("neris-sync")
async def neris_sync() -> int:
    """Fetch from NERIS API and write to Cosmos DB.

    Uses a checkpoint for incremental syncs: sorts by last_modified
    descending and stops when records are older than the checkpoint.
    First run (no checkpoint) fetches all records.

    Returns:
        Number of reports synced
    """
    from sjifire.ops.neris.store import NerisReportStore

    # Read checkpoint
    async with NerisReportStore() as store:
        checkpoint = await store.get_sync_checkpoint()

    if checkpoint:
        logger.info("Incremental NERIS sync (since=%s)", checkpoint)
    else:
        logger.info("Full NERIS sync (no checkpoint)")

    summaries = await asyncio.to_thread(fetch_neris_summaries, since=checkpoint)
    logger.info("Fetched %d NERIS summaries from API", len(summaries))

    count = await refresh_neris_report_cache(summaries)

    # Sync NERIS status changes to local incidents
    await _sync_neris_to_local(summaries)

    # Store checkpoint after successful sync
    new_checkpoint = datetime.now(UTC).isoformat()
    async with NerisReportStore() as store:
        await store.set_sync_checkpoint(new_checkpoint)

    return count
