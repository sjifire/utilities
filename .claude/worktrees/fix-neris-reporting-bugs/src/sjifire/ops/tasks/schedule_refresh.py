"""Schedule cache refresh background task.

Thin wrapper around the Outlook calendar pipeline in
``sjifire.ops.schedule.tools``. Runs every 30 minutes via
Container Apps Job to keep the Cosmos DB schedule cache fresh.
"""

import logging
from datetime import date, timedelta

from sjifire.ops.tasks.registry import register

logger = logging.getLogger(__name__)


@register("schedule-refresh")
async def schedule_refresh() -> int:
    """Refresh schedule cache from Outlook group calendar.

    Fetches On Duty events for today-1 through today+7, parses the
    HTML body, and upserts crew data to Cosmos DB.

    Returns:
        Number of days refreshed.
    """
    from sjifire.ops.schedule.store import ScheduleStore
    from sjifire.ops.schedule.tools import fetch_schedule_from_outlook

    today = date.today()
    start = today - timedelta(days=1)
    end = today + timedelta(days=7)

    fresh = await fetch_schedule_from_outlook(start, end)

    if not fresh:
        logger.warning("No On Duty events found in group calendar")
        return 0

    count = 0
    async with ScheduleStore() as store:
        for doc in fresh:
            await store.upsert(doc)
            count += 1
            logger.info(
                "Refreshed schedule for %s: %d entries (%s)",
                doc.date,
                len(doc.entries),
                doc.platoon,
            )

    logger.info("Schedule refresh complete: %d days updated", count)
    return count
