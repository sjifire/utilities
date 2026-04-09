"""Dispatch call sync + enrichment tasks.

Fetches recent completed calls from iSpyFire, stores them in Cosmos DB
with AI enrichment, then retries enrichment on any previously stored
calls that are missing analysis (e.g., due to transient AI failures).

Tasks:
    dispatch-sync     — Sync new calls from iSpyFire + enrich missing
    dispatch-enrich   — Enrich stored calls missing analysis
    dispatch-reenrich — Force re-enrich ALL stored calls (after code changes)

Requires: ISPYFIRE_*, COSMOS_*, ANTHROPIC_API_KEY env vars.
"""

import logging
from datetime import timedelta

from sjifire.ops.tasks.registry import register

logger = logging.getLogger(__name__)


async def _prewarm_schedule(docs) -> None:
    """Pre-warm schedule cache for a batch of dispatch docs.

    Collects all unique dates needed and fetches them in one batch
    instead of per-call fetches during enrichment.
    """
    from sjifire.ops.schedule.store import ScheduleStore
    from sjifire.ops.schedule.tools import _ensure_cache

    dates_needed: set[str] = set()
    for doc in docs:
        if doc.time_reported:
            dt = doc.time_reported
            for delta in (-1, 0):
                dates_needed.add((dt + timedelta(days=delta)).strftime("%Y-%m-%d"))

    if not dates_needed:
        return

    logger.info(
        "Pre-warming schedule cache for %d dates (%s to %s)",
        len(dates_needed),
        min(dates_needed),
        max(dates_needed),
    )
    async with ScheduleStore() as sstore:
        await _ensure_cache(sstore, sorted(dates_needed))


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


@register("dispatch-enrich")
async def dispatch_enrich() -> int:
    """Enrich stored dispatch calls that are missing analysis.

    Skips calls that already have analysis. Use dispatch-reenrich
    to force re-enrichment of all calls.

    Returns:
        Number of calls enriched
    """
    from sjifire.ops.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        docs = await store.list_recent(limit=9999)
        unenriched = [
            d for d in docs if not d.analysis.incident_commander and not d.analysis.summary
        ]
        logger.info(
            "Found %d stored calls, %d missing analysis",
            len(docs),
            len(unenriched),
        )

        if unenriched:
            await _prewarm_schedule(unenriched)
            results = await store.enrich_stored(force=False, limit=9999)
            count = sum(1 for d in results if d.analysis.incident_commander or d.analysis.summary)
            logger.info("Enriched %d calls", count)
            return count

    return 0


@register("dispatch-reenrich", auto=False)
async def dispatch_reenrich() -> int:
    """Force re-enrichment of ALL stored dispatch calls.

    Re-runs the full enrichment pipeline (LLM analysis + unit timing
    extraction) on every stored call, regardless of existing analysis.
    Use after changing enrichment logic (e.g., new status mappings,
    updated prompts).

    Excluded from automatic runs (``auto=False``) because it calls the
    LLM for every stored call, which can take many minutes.
    Run explicitly: ``uv run ops-tasks dispatch-reenrich``

    Returns:
        Number of calls re-enriched
    """
    from sjifire.ops.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        docs = await store.list_recent(limit=9999)
        logger.info("Found %d stored dispatch calls to re-enrich", len(docs))

        if docs:
            await _prewarm_schedule(docs)

        results = await store.enrich_stored(force=True, limit=9999)
        count = sum(1 for d in results if d.analysis.incident_commander or d.analysis.summary)
        logger.info("Force re-enriched %d of %d stored calls", count, len(results))

    return count
