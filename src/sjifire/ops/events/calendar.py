"""Read events from Exchange shared mailbox calendars.

Fetches events from one or more Exchange calendars and returns them
as a merged, sorted list of dicts. Calendar sources are configured in
``config/organization.json`` under the ``event_calendars`` key.

Each calendar source is a dict with:
- ``mailbox``: Email address of the user/shared-mailbox
- ``label``: Display label (e.g. "Training")
- ``calendar_name`` (optional): Name of a specific calendar folder.
  When omitted, queries the user's default calendar.
"""

import asyncio
import logging
from datetime import date, datetime, time

from sjifire.core.config import get_timezone_name, load_org_config

logger = logging.getLogger(__name__)


def _get_calendar_sources() -> list[dict[str, str]]:
    """Load calendar sources from organization config."""
    org = load_org_config()
    return org.event_calendars


def _strip_html(html: str) -> str:
    """Extract plain text from HTML body."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    except Exception:
        return html


async def _resolve_calendar_id(client, mailbox: str, calendar_name: str) -> str | None:
    """Look up a named calendar folder on a mailbox, return its ID."""
    try:
        calendars = await client.users.by_user_id(mailbox).calendars.get()
        if calendars and calendars.value:
            for cal in calendars.value:
                if cal.name and cal.name.lower() == calendar_name.lower():
                    return cal.id
    except Exception:
        logger.warning("Failed to list calendars for %s", mailbox, exc_info=True)
    return None


async def _fetch_one_calendar(
    mailbox: str,
    label: str,
    start: date,
    end: date,
    calendar_name: str = "",
) -> list[dict]:
    """Fetch events from a single shared mailbox calendar.

    When *calendar_name* is given, queries that specific calendar folder
    instead of the user's default calendar.
    """
    try:
        from sjifire.core.msgraph_client import get_graph_client

        client = get_graph_client()
    except Exception:
        logger.warning(
            "Graph client unavailable — cannot fetch calendar %s",
            mailbox,
            exc_info=True,
        )
        return []

    tz = get_timezone_name()
    start_dt = datetime.combine(start, time.min).isoformat()
    end_dt = datetime.combine(end, time.max).isoformat()

    # Resolve named calendar if requested
    calendar_id: str | None = None
    if calendar_name:
        calendar_id = await _resolve_calendar_id(client, mailbox, calendar_name)
        if calendar_id is None:
            logger.warning(
                "Calendar '%s' not found on %s — skipping",
                calendar_name,
                mailbox,
            )
            return []

    try:
        from kiota_abstractions.base_request_configuration import RequestConfiguration

        if calendar_id:
            # Query a specific named calendar
            from msgraph.generated.users.item.calendars.item.calendar_view.calendar_view_request_builder import (  # noqa: E501
                CalendarViewRequestBuilder,
            )
        else:
            # Query the default calendar
            from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
                CalendarViewRequestBuilder,
            )

        query = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
            start_date_time=start_dt,
            end_date_time=end_dt,
            select=["id", "subject", "start", "end", "isAllDay", "body", "location"],
            top=200,
            orderby=["start/dateTime asc"],
        )
        config = RequestConfiguration(query_parameters=query)
        config.headers.add("Prefer", f'outlook.timezone="{tz}"')

        if calendar_id:
            result = await (
                client.users.by_user_id(mailbox)
                .calendars.by_calendar_id(calendar_id)
                .calendar_view.get(request_configuration=config)
            )
        else:
            result = await client.users.by_user_id(mailbox).calendar_view.get(
                request_configuration=config
            )
    except Exception:
        logger.warning("Failed to fetch calendar events from %s", mailbox, exc_info=True)
        return []

    # Station address for conference rooms (falls back to org config)
    org = load_org_config()
    station_address = org.station_address

    events: list[dict] = []
    if result and result.value:
        for ev in result.value:
            body_html = ev.body.content if ev.body else ""
            loc = ""
            loc_address = ""  # address to use for maps link
            if ev.location and ev.location.display_name:
                loc = ev.location.display_name
                addr = ev.location.address
                coords = ev.location.coordinates
                loc_type = str(ev.location.location_type or "").lower()
                if (addr and addr.street) or (coords and coords.latitude and coords.longitude):
                    loc_address = loc
                elif "conference" in loc_type:
                    # Conference rooms are at the main station
                    loc_address = station_address

            events.append(
                {
                    "event_id": ev.id,
                    "subject": ev.subject or "",
                    "start": ev.start.date_time if ev.start else "",
                    "end": ev.end.date_time if ev.end else "",
                    "is_all_day": ev.is_all_day or False,
                    "location": loc,
                    "location_address": loc_address,
                    "body_preview": _strip_html(body_html)[:500],
                    "calendar_source": label,
                }
            )

    logger.info(
        "EVENT_CAL: fetched %d events from %s (%s) range %s to %s",
        len(events),
        mailbox,
        label,
        start,
        end,
    )
    return events


_CACHE_TTL = 10800  # 3 hours


def _cache_key(label: str, start: date, end: date) -> str:
    """Simple per-calendar cache key: ``cal:<label>:<start>:<end>``."""
    return f"cal:{label}:{start}:{end}"


async def _fetch_cached(
    mailbox: str,
    label: str,
    start: date,
    end: date,
    calendar_name: str = "",
) -> list[dict]:
    """Fetch a single calendar with per-calendar caching (3 h TTL)."""
    from sjifire.ops.cache import cosmos_cache

    key = _cache_key(label, start, end)
    cached = await cosmos_cache.get(key)
    if cached is not None:
        logger.debug("EVENT_CAL: cache hit for %s (%s → %s)", label, start, end)
        return cached

    events = await _fetch_one_calendar(mailbox, label, start, end, calendar_name)
    await cosmos_cache.set(key, events, ttl=_CACHE_TTL)
    logger.info(
        "EVENT_CAL: cached %d events for %s (%s → %s, ttl=%ds)",
        len(events),
        label,
        start,
        end,
        _CACHE_TTL,
    )
    return events


async def fetch_events(start: date, end: date) -> list[dict]:
    """Fetch events from all configured calendars.

    Each calendar is cached independently in Cosmos DB for 3 hours
    so the Events tab loads quickly and reduces Graph API calls.
    """
    sources = _get_calendar_sources()
    tasks = [
        _fetch_cached(s["mailbox"], s["label"], start, end, s.get("calendar_name", ""))
        for s in sources
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_events: list[dict] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("Calendar fetch failed for %s: %s", sources[i]["mailbox"], result)
            continue
        all_events.extend(result)

    all_events.sort(key=lambda e: e.get("start", ""))
    return all_events
