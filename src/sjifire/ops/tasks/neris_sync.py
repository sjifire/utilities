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

logger = logging.getLogger(__name__)


def fetch_neris_summaries(*, last_modified: str | None = None) -> list[dict]:
    """Fetch all NERIS incidents and extract summary dicts.

    This is a **blocking** call (uses ``NerisClient`` which is sync).
    Run via ``asyncio.to_thread()`` in async contexts.

    Args:
        last_modified: Optional ISO timestamp to filter by last modification.
            When set, only incidents modified after this time are returned.

    Returns:
        List of summary dicts with keys: neris_id, incident_number,
        determinant_code, status, incident_type, call_create.
    """
    from sjifire.neris.client import NerisClient

    kwargs: dict = {}
    if last_modified:
        kwargs["last_modified"] = last_modified

    with NerisClient() as client:
        incidents = client.get_all_incidents(**kwargs)

    summaries: list[dict] = []
    for inc in incidents:
        dispatch = inc.get("dispatch", {})
        types = inc.get("incident_types", [])
        status_info = inc.get("incident_status", {})

        summaries.append(
            {
                "source": "neris",
                "neris_id": inc.get("neris_id", ""),
                "incident_number": dispatch.get("incident_number", ""),
                "determinant_code": str(dispatch.get("determinant_code") or ""),
                "status": status_info.get("status", ""),
                "incident_type": types[0].get("type", "") if types else "",
                "call_create": dispatch.get("call_create", ""),
            }
        )

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
    """Transition local incidents based on NERIS status changes.

    For each NERIS summary, check if a local incident exists with
    the same ``neris_incident_id``. If the local incident is in
    ``submitted`` status and NERIS shows ``APPROVED``, transition
    the local incident to ``approved``.

    Args:
        summaries: List of summary dicts from ``fetch_neris_summaries()``

    Returns:
        Number of local incidents transitioned
    """
    from sjifire.ops.incidents.models import EditEntry
    from sjifire.ops.incidents.store import IncidentStore

    transitioned = 0
    async with IncidentStore() as store:
        for summary in summaries:
            neris_id = summary.get("neris_id", "")
            neris_status = summary.get("status", "")
            if not neris_id or neris_status != "APPROVED":
                continue

            doc = await store.get_by_neris_id(neris_id)
            if doc is None:
                continue

            # Only transition submitted → approved
            if doc.status != "submitted":
                continue

            doc.status = "approved"
            doc.updated_at = datetime.now(UTC)
            doc.edit_history.append(
                EditEntry(
                    editor_email="system@sjifire.org",
                    editor_name="NERIS Sync",
                    fields_changed=["status:approved"],
                )
            )
            await store.update(doc)
            transitioned += 1
            logger.info(
                "NERIS sync transitioned incident %s → approved (NERIS: %s)",
                doc.id,
                neris_id,
            )

    if transitioned:
        logger.info("Transitioned %d local incidents to approved", transitioned)
    return transitioned


@register("neris-sync")
async def neris_sync() -> int:
    """Fetch from NERIS API and write to Cosmos DB.

    Uses a high-water mark checkpoint for incremental syncs.
    On first run (no checkpoint), fetches all records.

    Returns:
        Number of reports synced
    """
    from sjifire.ops.neris.store import NerisReportStore

    # Read checkpoint
    async with NerisReportStore() as store:
        checkpoint = await store.get_sync_checkpoint()

    if checkpoint:
        logger.info("Incremental NERIS sync (last_modified=%s)", checkpoint)
    else:
        logger.info("Full NERIS sync (no checkpoint)")

    summaries = await asyncio.to_thread(fetch_neris_summaries, last_modified=checkpoint)
    logger.info("Fetched %d NERIS summaries from API", len(summaries))

    count = await refresh_neris_report_cache(summaries)

    # Sync NERIS status changes to local incidents
    await _sync_neris_to_local(summaries)

    # Store checkpoint after successful sync
    new_checkpoint = datetime.now(UTC).isoformat()
    async with NerisReportStore() as store:
        await store.set_sync_checkpoint(new_checkpoint)

    return count
