"""Dispatch call sync + enrichment task.

Fetches recent completed calls from iSpyFire, stores them in Cosmos DB
with AI enrichment, then retries enrichment on any previously stored
calls that are missing analysis (e.g., due to transient AI failures).

Requires: ISPYFIRE_*, COSMOS_*, ANTHROPIC_API_KEY env vars.
"""

import logging

from sjifire.ops.tasks.registry import register

logger = logging.getLogger(__name__)


@register("dispatch-sync")
async def dispatch_sync() -> int:
    """Sync recent dispatch calls and enrich any missing analysis.

    Returns:
        Total number of calls processed (new + re-enriched)
    """
    from sjifire.ops.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        # 1. Fetch recent completed calls from iSpyFire, store new ones
        #    (store_call already enriches each new call)
        new_count = await store.sync_recent(days=2)
        if new_count:
            logger.info("Synced %d new completed calls", new_count)

        # 2. Retry enrichment on stored calls missing analysis
        retried = await store.enrich_stored()
        retry_count = sum(1 for d in retried if d.analysis.incident_commander or d.analysis.summary)
        if retry_count:
            logger.info("Re-enriched %d previously un-analyzed calls", retry_count)

    total = new_count + retry_count
    logger.info("Dispatch sync complete: %d new, %d re-enriched", new_count, retry_count)
    return total
