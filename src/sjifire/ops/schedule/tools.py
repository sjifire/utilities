"""Tools for schedule lookup with Cosmos DB caching.

Schedule data flows:
    Aladtec → (calendar-sync GHA) → Outlook group calendar
        → (schedule-refresh task, every 30 min) → Cosmos DB cache
        → tool response

If the cache is stale (>4 hours for today/future), falls back to
reading the Outlook group calendar directly via Graph API.

Shift-change logic: fire department shifts typically run 24 hours
(e.g. 18:00 to 18:00). Before the shift change hour, the previous
day's crew is still on duty. The shift change hour is detected from
the data (full-shift entries where start_time == end_time).
"""

import json
import logging
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup, Tag

from sjifire.calendar.models import CREW_DATA_MARKER
from sjifire.core.config import local_now
from sjifire.core.schedule import (
    detect_shift_change_hour,
    resolve_duty_date,
    should_exclude_section,
)
from sjifire.ops.auth import get_current_user
from sjifire.ops.schedule.models import DayScheduleCache, ScheduleEntryCache
from sjifire.ops.schedule.store import ScheduleStore

logger = logging.getLogger(__name__)

# Maximum cache age before triggering an Outlook calendar fallback refresh.
# The schedule-refresh background task keeps the cache fresh every 30 min;
# this TTL is a safety net if that task fails.
# Today/future: 4 hours.  Past dates: 7 days.
CACHE_MAX_AGE_HOURS = 4.0
CACHE_MAX_AGE_HOURS_PAST = 168.0  # 7 days

# Matches "From 1800 (A Platoon)" or "Until 1800 (B Platoon)"
_SECTION_RE = re.compile(r"(Until|From)\s+(\d{4})\s*(?:\(([^)]+)\))?")

# Regex to extract the CREW_DATA JSON from an HTML comment.
# Uses the shared marker constant so writer and reader stay in sync.
_CREW_DATA_RE = re.compile(rf"<!--\s*{re.escape(CREW_DATA_MARKER)}(.*?)-->", re.DOTALL)


# ---------------------------------------------------------------------------
# Outlook calendar → DayScheduleCache pipeline
# ---------------------------------------------------------------------------


def _parse_crew_data_json(html: str) -> tuple[list[ScheduleEntryCache], str] | None:
    """Try to extract structured crew data from an embedded JSON comment.

    Returns (entries, platoon) if the CREW_DATA comment is present and valid,
    or None to signal the caller should fall back to HTML table parsing.
    """
    match = _CREW_DATA_RE.search(html)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, IndexError):
        logger.warning("CREW_DATA comment found but JSON is invalid, falling back to HTML parsing")
        return None

    shift_hour = data.get("shift_change_hour", 0)
    shift_time_str = f"{shift_hour:02d}:00"
    platoon = data.get("from_platoon", "")
    from_crew: dict = data.get("from_crew", {})

    entries: list[ScheduleEntryCache] = []
    for section, members in from_crew.items():
        entries.extend(
            ScheduleEntryCache(
                name=member["name"],
                position=member["position"],
                section=section,
                start_time=shift_time_str,
                end_time=shift_time_str,
                platoon=platoon,
            )
            for member in members
        )

    return entries, platoon


def parse_duty_event_html(html: str, event_date: date) -> tuple[list[ScheduleEntryCache], str]:
    """Parse an On Duty calendar event body into schedule entries.

    Extracts only the "From" section (that date's assigned crew).
    The "Until" section is the previous day's crew, already cached.

    Tries structured JSON (CREW_DATA comment) first for reliability,
    then falls back to HTML table parsing for pre-JSON legacy events.

    Args:
        html: HTML body from the Outlook calendar event
        event_date: The date this event represents

    Returns:
        Tuple of (entries, platoon). Entries have start_time == end_time
        set to the shift change hour so detect_shift_change_hour() works.
    """
    if not html or not html.strip():
        return [], ""

    # Fast path: extract structured JSON embedded by calendar-sync
    result = _parse_crew_data_json(html)
    if result is not None:
        return result

    # Legacy fallback: parse HTML tables (for events created before JSON embedding)
    return _parse_duty_event_html_tables(html)


def _parse_duty_event_html_tables(html: str) -> tuple[list[ScheduleEntryCache], str]:
    """Legacy HTML table parser for pre-JSON calendar events."""
    soup = BeautifulSoup(html, "html.parser")

    entries: list[ScheduleEntryCache] = []
    platoon = ""
    shift_time_str = ""

    for h3 in soup.find_all("h3"):
        text = h3.get_text(strip=True)
        match = _SECTION_RE.match(text)
        if not match:
            continue

        label, time_code, platoon_text = match.groups()

        # Only extract the "From" section
        if label != "From":
            continue

        shift_time_str = f"{time_code[:2]}:{time_code[2:]}"
        platoon = platoon_text.strip() if platoon_text else ""

        # Find the table following this h3
        table = h3.find_next("table")
        if not table or not isinstance(table, Tag):
            continue

        current_section = ""
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue

            # Section header: single td with colspan
            first_td = tds[0]
            if first_td.get("colspan"):
                current_section = first_td.get_text(strip=True)
                continue

            # Crew row: name | position | contacts
            if len(tds) >= 2:
                name = tds[0].get_text(strip=True)
                position = tds[1].get_text(strip=True)
                if name and position:
                    entries.append(
                        ScheduleEntryCache(
                            name=name,
                            position=position,
                            section=current_section,
                            start_time=shift_time_str,
                            end_time=shift_time_str,
                            platoon=platoon,
                        )
                    )

    return entries, platoon


async def _fetch_group_calendar_events(
    start: date,
    end: date,
) -> dict[date, str]:
    """Read On Duty events from the all-personnel group calendar.

    Uses app-only auth (ClientSecretCredential) with Calendars.Read
    application permission.

    Returns:
        Dict mapping event date to HTML body content.
    """
    from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder
    from msgraph.generated.groups.item.calendar_view.calendar_view_request_builder import (
        CalendarViewRequestBuilder as GroupCalendarViewRequestBuilder,
    )

    from sjifire.core.config import get_org_config, get_timezone
    from sjifire.core.msgraph_client import get_graph_client

    client = get_graph_client()
    org = get_org_config()
    tz = get_timezone()

    # Find the all-personnel group by mailNickname
    mail_nickname = "all-personnel"
    query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
        filter=f"mailNickname eq '{mail_nickname}'",
        select=["id", "displayName", "mailNickname"],
    )
    config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration(
        query_parameters=query_params,
    )
    result = await client.groups.get(request_configuration=config)
    if not result or not result.value:
        raise RuntimeError(f"Group '{mail_nickname}' not found in Entra ID")

    group_id = result.value[0].id
    logger.info("Found group %s (%s)", result.value[0].display_name, group_id)

    # Fetch calendar events for date range
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=tz)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=tz)

    subject = org.duty_event_subject  # "On Duty"
    cv_params = GroupCalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
        start_date_time=start_dt.isoformat(),
        end_date_time=end_dt.isoformat(),
        filter=f"startswith(subject, '{subject}')",
        top=100,
        select=["id", "subject", "start", "end", "isAllDay", "body"],
    )
    cv_config = GroupCalendarViewRequestBuilder.CalendarViewRequestBuilderGetRequestConfiguration(
        query_parameters=cv_params,
    )
    events_result = await client.groups.by_group_id(group_id).calendar_view.get(
        request_configuration=cv_config,
    )

    events_by_date: dict[date, str] = {}
    if events_result and events_result.value:
        for event in events_result.value:
            if not event.start or not event.start.date_time:
                continue
            # All-day events have date_time like "2026-02-17T00:00:00.0000000"
            event_date = datetime.fromisoformat(event.start.date_time).date()
            body_content = event.body.content if event.body and event.body.content else ""
            events_by_date[event_date] = body_content

    logger.info(
        "Fetched %d On Duty events from group calendar (%s to %s)",
        len(events_by_date),
        start,
        end,
    )
    return events_by_date


async def fetch_schedule_from_outlook(start: date, end: date) -> list[DayScheduleCache]:
    """Fetch schedule data from Outlook group calendar.

    Reads On Duty events, parses the HTML body, and returns
    DayScheduleCache documents ready for Cosmos DB upsert.

    Used by the schedule-refresh background task and as the
    inline fallback when the cache is stale.

    Args:
        start: First date to fetch (inclusive)
        end: Last date to fetch (inclusive)

    Returns:
        List of DayScheduleCache documents (may be empty).
    """
    events = await _fetch_group_calendar_events(start, end)

    results: list[DayScheduleCache] = []
    for event_date, html in sorted(events.items()):
        entries, platoon = parse_duty_event_html(html, event_date)
        if not entries:
            logger.warning("No crew entries parsed for %s", event_date)
            continue
        results.append(
            DayScheduleCache(
                id=event_date.isoformat(),
                date=event_date.isoformat(),
                platoon=platoon,
                entries=entries,
            )
        )

    logger.info("Fetched %d days from Outlook calendar (%s to %s)", len(results), start, end)
    return results


# ---------------------------------------------------------------------------
# Cosmos DB cache management
# ---------------------------------------------------------------------------


def _detect_shift_change_hour_from_cache(cached: dict[str, DayScheduleCache]) -> int | None:
    """Detect shift change hour from cached schedule days.

    Flattens all entries across cached days and delegates to the
    shared ``detect_shift_change_hour`` utility.
    """
    all_entries = [e for day in cached.values() for e in day.entries]
    return detect_shift_change_hour(all_entries)


def _build_crew_list(
    day: DayScheduleCache,
    include_admin: bool,
) -> list[dict]:
    """Build the crew list from a day's schedule entries."""
    entries = day.entries
    if not include_admin:
        entries = [e for e in entries if not should_exclude_section(e.section)]
    return [
        {
            "name": e.name,
            "position": e.position,
            "section": e.section,
            "start_time": e.start_time,
            "end_time": e.end_time,
        }
        for e in entries
    ]


async def _ensure_cache(
    store: ScheduleStore,
    needed_dates: list[str],
) -> dict[str, DayScheduleCache]:
    """Ensure cache has fresh data for the needed dates.

    Checks Cosmos DB for each date. If any are missing or stale,
    fetches from the Outlook group calendar and updates the cache.

    Args:
        store: Connected ScheduleStore
        needed_dates: List of YYYY-MM-DD strings to ensure

    Returns:
        Dict mapping date string to DayScheduleCache for all needed dates
    """
    cached = await store.get_range(needed_dates)

    # Find dates that are missing or stale.
    # Past dates use a longer TTL (7 days) since they rarely change.
    today_str = date.today().isoformat()
    stale_dates = []
    for date_str in needed_dates:
        day = cached.get(date_str)
        max_age = CACHE_MAX_AGE_HOURS if date_str >= today_str else CACHE_MAX_AGE_HOURS_PAST
        if day is None or day.is_stale(max_age):
            stale_dates.append(date_str)

    if not stale_dates:
        logger.info("Schedule cache hit for all %d dates", len(needed_dates))
        return cached

    # Fallback: refresh stale dates from Outlook group calendar
    logger.info(
        "Fallback: refreshing %d stale/missing schedule dates from Outlook",
        len(stale_dates),
    )
    start = datetime.strptime(min(stale_dates), "%Y-%m-%d").date()
    end = datetime.strptime(max(stale_dates), "%Y-%m-%d").date()

    fresh = await fetch_schedule_from_outlook(start, end)

    # Write fresh data to cache
    for day_cache in fresh:
        await store.upsert(day_cache)
        cached[day_cache.date] = day_cache

    return cached


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------


async def get_on_duty_crew(
    target_date: str | None = None,
    include_admin: bool = False,
    target_hour: int | None = None,
) -> dict:
    """Get the crew on duty for a specific date or right now.

    When ``target_date`` is omitted the function is **time-aware**:
    it detects the shift-change hour from the data (full-shift entries
    where ``start_time == end_time``) and picks the correct day's crew
    based on the current local time.  Before the shift change, the
    previous day's crew is still on duty.  The response also includes
    an ``upcoming`` block with the next shift's crew.

    When ``target_date`` is provided with ``target_hour``, the function
    applies the same shift-change logic using the given hour instead of
    the current time. This is useful for historical lookups (e.g., an
    incident at 16:48 before an 18:00 shift change).

    When ``target_date`` is provided without ``target_hour``, the
    function returns that date's assigned crew (no time-based filtering).

    By default, administration staff and Time Off entries are excluded
    — pass ``include_admin=True`` to see everyone.

    Data is cached in Cosmos DB (refreshed from Outlook every 30 min
    by the schedule-refresh background task).

    Args:
        target_date: Date in YYYY-MM-DD format. Defaults to today (time-aware).
        include_admin: Include administration staff (default: False).
        target_hour: Hour of day (0-23) for shift-change-aware historical lookups.

    Returns:
        Dict with date, platoon, crew list, shift_change_hour, and
        (when target_date is omitted) an upcoming block.
    """
    user = get_current_user()

    try:
        dt = (
            datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else local_now().date()
        )
    except ValueError:
        return {"error": f"Invalid date format: {target_date!r}. Expected YYYY-MM-DD."}
    logger.info("Schedule lookup for %s hour=%s (user: %s)", dt, target_hour, user.email)

    # Request target date +/- 1 day for shift-change coverage
    needed = [
        (dt - timedelta(days=1)).isoformat(),
        dt.isoformat(),
        (dt + timedelta(days=1)).isoformat(),
    ]

    async with ScheduleStore() as store:
        cached = await _ensure_cache(store, needed)

    # Detect shift change hour from full-shift entries in the cached data
    shift_change_hour = _detect_shift_change_hour_from_cache(cached)

    # Determine effective hour: current local time for "now", or explicit target_hour
    effective_hour: int | None = target_hour
    if target_date is None and shift_change_hour is not None:
        effective_hour = local_now().hour

    duty_date, upcoming_date = resolve_duty_date(dt, shift_change_hour, effective_hour)

    duty_str = duty_date.isoformat()
    day = cached.get(duty_str)

    if day is None:
        return {
            "date": duty_str,
            "crew": [],
            "count": 0,
            "shift_change_hour": shift_change_hour,
            "note": "No schedule data available for this date.",
        }

    crew = _build_crew_list(day, include_admin)

    result: dict = {
        "date": duty_str,
        "platoon": day.platoon,
        "crew": crew,
        "count": len(crew),
        "shift_change_hour": shift_change_hour,
        "cache_age_hours": round(
            (datetime.now(day.fetched_at.tzinfo) - day.fetched_at).total_seconds() / 3600,
            1,
        ),
    }

    # Include upcoming shift when showing current crew (no target_date)
    if upcoming_date is not None:
        upcoming_str = upcoming_date.isoformat()
        upcoming_day = cached.get(upcoming_str)
        if upcoming_day:
            result["upcoming"] = {
                "date": upcoming_str,
                "platoon": upcoming_day.platoon,
                "crew": _build_crew_list(upcoming_day, include_admin),
            }

    return result
