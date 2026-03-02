"""Event archive background task.

Archives calendar events before they fall out of Outlook's 180-day
rolling window.  Creates skeleton EventRecord documents in Cosmos DB
for events between 150-180 days ago that don't already have records.
"""

import logging
from datetime import date, datetime, timedelta

from sjifire.ops.tasks.registry import register

logger = logging.getLogger(__name__)


@register("event-archive")
async def event_archive() -> int:
    """Archive calendar events approaching Outlook's expiry window.

    Fetches events from 150-180 days ago and creates skeleton
    EventRecord documents for any that don't already have records.

    Returns:
        Number of new records created.
    """
    from sjifire.ops.events.calendar import fetch_events
    from sjifire.ops.events.models import EventRecord
    from sjifire.ops.events.store import EventStore

    today = date.today()
    start = today - timedelta(days=180)
    end = today - timedelta(days=150)

    events = await fetch_events(start, end)
    if not events:
        logger.info("No calendar events in archive window (%s to %s)", start, end)
        return 0

    count = 0
    async with EventStore() as store:
        for ev in events:
            event_id = ev.get("event_id")
            if not event_id:
                continue

            existing = await store.get_by_calendar_event_id(event_id)
            if existing:
                continue

            start_str = ev.get("start", "")
            if not start_str:
                continue

            event_date = datetime.fromisoformat(start_str)
            end_str = ev.get("end", "")
            end_date = datetime.fromisoformat(end_str) if end_str else None

            rec = EventRecord(
                calendar_event_id=event_id,
                calendar_source=ev.get("calendar_source", ""),
                subject=ev.get("subject", ""),
                event_date=event_date,
                end_date=end_date,
                location=ev.get("location", ""),
                description=ev.get("body_preview", ""),
                created_by="system",
            )
            await store.upsert(rec)
            count += 1
            logger.info("Archived event %s: %s (%s)", rec.id, rec.subject, rec.event_date.date())

    logger.info("Event archive complete: %d new records from %d events", count, len(events))
    return count
