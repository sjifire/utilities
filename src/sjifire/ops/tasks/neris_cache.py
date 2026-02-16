"""NERIS report cache refresh task.

Fetches incident summaries from the NERIS API and writes them to
the ``neris-reports`` Cosmos DB container.  All server instances
then read from Cosmos (never the API directly).

This is the single canonical version of the NERIS summary extraction
logic — dashboard.py and incidents/tools.py both delegate here.
"""

import asyncio
import logging

from sjifire.ops.tasks.registry import register

logger = logging.getLogger(__name__)


def fetch_neris_summaries() -> list[dict]:
    """Fetch all NERIS incidents and extract summary dicts.

    This is a **blocking** call (uses ``NerisClient`` which is sync).
    Run via ``asyncio.to_thread()`` in async contexts.

    Returns:
        List of summary dicts with keys: neris_id, incident_number,
        determinant_code, status, incident_type, call_create.
    """
    from sjifire.neris.client import NerisClient

    with NerisClient() as client:
        incidents = client.get_all_incidents()

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


@register("neris-cache")
async def neris_cache_refresh() -> int:
    """Fetch from NERIS API and write to Cosmos DB cache.

    Returns:
        Number of reports cached
    """
    summaries = await asyncio.to_thread(fetch_neris_summaries)
    logger.info("Fetched %d NERIS summaries from API", len(summaries))
    return await refresh_neris_report_cache(summaries)
