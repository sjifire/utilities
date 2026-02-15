"""Background dispatch sync loop.

Periodically ingests new completed calls from iSpyFire and retries
enrichment for any stored records that are missing analysis data.

This separates the write path (ingestion + enrichment) from the read
path (``list_recent_with_open``), so MCP tool calls never trigger
writes as a side effect.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

SYNC_INTERVAL = 3600  # 1 hour


async def dispatch_sync_loop() -> None:
    """Background loop: sync new calls from iSpyFire, sweep un-enriched.

    Runs forever (until cancelled). Each iteration:

    1. **Ingest** — ``sync_recent(days=2)`` fetches the last 48 hours of
       calls from iSpyFire and stores only NEW completed calls (diff
       against existing Cosmos docs).
    2. **Sweep** — ``enrich_stored(limit=10)`` picks up any docs that
       failed enrichment previously and retries.

    All errors are caught so the loop never crashes the server.
    """
    # Short delay to let the server finish startup
    await asyncio.sleep(10)
    logger.info("Background dispatch sync started (interval=%ds)", SYNC_INTERVAL)

    while True:
        try:
            from sjifire.ops.dispatch.store import DispatchStore

            async with DispatchStore() as store:
                # 1. Ingest new completed calls
                new_count = await store.sync_recent(days=2)
                if new_count:
                    logger.info("Background sync: %d new calls", new_count)

                # 2. Sweep: re-enrich any docs missing analysis
                unenriched = await store.enrich_stored(limit=10)
                if unenriched:
                    enriched = sum(
                        1 for d in unenriched if d.analysis.incident_commander or d.analysis.summary
                    )
                    logger.info("Background sweep: enriched %d/%d", enriched, len(unenriched))
        except Exception:
            logger.exception("Background dispatch sync failed")

        await asyncio.sleep(SYNC_INTERVAL)
