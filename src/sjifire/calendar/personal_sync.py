"""Personal calendar sync - Aladtec schedule to each user's M365 calendar."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.calendar import Calendar
from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
from msgraph.generated.models.event import Event
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.users.item.calendars.calendars_request_builder import (
    CalendarsRequestBuilder,
)
from msgraph.generated.users.item.calendars.item.events.events_request_builder import (
    EventsRequestBuilder,
)

from sjifire.aladtec.schedule_scraper import ScheduleEntry
from sjifire.core.config import get_graph_credentials

logger = logging.getLogger(__name__)

# Calendar name in each user's mailbox
CALENDAR_NAME = "Aladtec Schedule"

# Timezone for all operations
TIMEZONE_NAME = "America/Los_Angeles"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

# Concurrency limit for parallel API calls
MAX_CONCURRENT_REQUESTS = 5


@dataclass
class PersonalSyncResult:
    """Result of syncing personal calendar."""

    user: str
    events_created: int = 0
    events_updated: int = 0
    events_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # noqa: D105
        parts = []
        if self.events_created:
            parts.append(f"{self.events_created} created")
        if self.events_updated:
            parts.append(f"{self.events_updated} updated")
        if self.events_deleted:
            parts.append(f"{self.events_deleted} deleted")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return f"{self.user}: " + (", ".join(parts) if parts else "no changes")


def make_event_subject(entry: ScheduleEntry) -> str:
    """Create event subject from schedule entry."""
    return f"{entry.position} - {entry.section}"


def make_event_body(entry: ScheduleEntry) -> str:
    """Create event body from schedule entry."""
    return f"""Position: {entry.position}
Section: {entry.section}
Platoon: {entry.platoon}

This event is managed by automated sync from Aladtec.
Changes made here will be overwritten."""


def make_event_key(entry: ScheduleEntry) -> str:
    """Create unique key for an entry (for matching existing events)."""
    return f"{entry.date}|{entry.section}|{entry.position}|{entry.start_time}|{entry.end_time}"


class PersonalCalendarSync:
    """Sync individual Aladtec schedules to users' personal M365 calendars."""

    def __init__(self) -> None:
        """Initialize with Graph API credentials."""
        tenant_id, client_id, client_secret = get_graph_credentials()
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        self.client = GraphServiceClient(credentials=credential)
        self._calendar_cache: dict[str, str] = {}  # user_email -> calendar_id

    async def get_or_create_calendar(self, user_email: str) -> str | None:
        """Get or create the Aladtec Schedule calendar for a user.

        Returns:
            Calendar ID or None if failed
        """
        # Check cache first
        if user_email in self._calendar_cache:
            return self._calendar_cache[user_email]

        try:
            # Search for existing calendar
            query_params = CalendarsRequestBuilder.CalendarsRequestBuilderGetQueryParameters(
                filter=f"name eq '{CALENDAR_NAME}'",
            )
            config = CalendarsRequestBuilder.CalendarsRequestBuilderGetRequestConfiguration(
                query_parameters=query_params,
            )

            result = await self.client.users.by_user_id(user_email).calendars.get(
                request_configuration=config
            )

            if result and result.value:
                for cal in result.value:
                    if cal.name == CALENDAR_NAME and cal.id:
                        self._calendar_cache[user_email] = cal.id
                        logger.debug(f"Found existing calendar for {user_email}")
                        return cal.id

            # Create new calendar
            new_calendar = Calendar(name=CALENDAR_NAME)
            created = await self.client.users.by_user_id(user_email).calendars.post(new_calendar)

            if created and created.id:
                self._calendar_cache[user_email] = created.id
                logger.info(f"Created Aladtec Schedule calendar for {user_email}")
                return created.id

        except Exception as e:
            logger.error(f"Failed to get/create calendar for {user_email}: {e}")

        return None

    async def get_existing_events(
        self,
        user_email: str,
        calendar_id: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, str]:
        """Get existing Aladtec events in date range.

        Returns:
            Dict mapping event_key to event_id
        """
        # Note: We filter by date after fetching, not by calendarView endpoint
        # because we need to match events by key for sync logic
        _ = start_date, end_date  # Used for filtering below

        try:
            # Get all events from this calendar (it's dedicated to Aladtec)
            query_params = EventsRequestBuilder.EventsRequestBuilderGetQueryParameters(
                top=500,
                select=["id", "subject", "start", "end"],
            )
            config = EventsRequestBuilder.EventsRequestBuilderGetRequestConfiguration(
                query_parameters=query_params,
            )

            result = await (
                self.client.users.by_user_id(user_email)
                .calendars.by_calendar_id(calendar_id)
                .events.get(request_configuration=config)
            )

            events_by_key: dict[str, str] = {}

            if result and result.value:
                for event in result.value:
                    if not event.start or not event.start.date_time or not event.id:
                        continue

                    # Parse event datetime and convert to local timezone
                    try:
                        dt_str = event.start.date_time
                        event_tz = event.start.time_zone

                        # Remove microseconds if present (e.g., ".0000000")
                        if "." in dt_str:
                            dt_str = dt_str.split(".")[0]

                        # Parse base datetime
                        event_dt = datetime.fromisoformat(dt_str)

                        # Apply timezone based on Graph's time_zone field
                        if event_tz and event_tz.upper() == "UTC":
                            from zoneinfo import ZoneInfo

                            event_dt = event_dt.replace(tzinfo=ZoneInfo("UTC"))
                            event_dt = event_dt.astimezone(TIMEZONE)
                        elif event_tz:
                            # Try to use the specified timezone
                            try:
                                from zoneinfo import ZoneInfo

                                tz = ZoneInfo(event_tz)
                                event_dt = event_dt.replace(tzinfo=tz)
                                event_dt = event_dt.astimezone(TIMEZONE)
                            except KeyError:
                                # Unknown timezone, assume local
                                event_dt = event_dt.replace(tzinfo=TIMEZONE)
                        else:
                            # No timezone - assume local
                            event_dt = event_dt.replace(tzinfo=TIMEZONE)

                        event_date = event_dt.date()
                    except ValueError:
                        continue

                    # Only include events in our date range
                    if start_date <= event_date <= end_date:
                        # Create key from subject and start time (local time)
                        # Format: "date|subject|start_time"
                        start_time = event_dt.strftime("%H:%M")
                        key = f"{event_date}|{event.subject}|{start_time}"
                        events_by_key[key] = event.id

            return events_by_key

        except Exception as e:
            logger.error(f"Failed to get existing events for {user_email}: {e}")
            return {}

    async def create_event(
        self,
        user_email: str,
        calendar_id: str,
        entry: ScheduleEntry,
    ) -> bool:
        """Create a calendar event from a schedule entry."""
        start_dt = entry.start_datetime
        end_dt = entry.end_datetime

        event = Event(
            subject=make_event_subject(entry),
            body=ItemBody(
                content_type=BodyType.Text,
                content=make_event_body(entry),
            ),
            start=DateTimeTimeZone(
                date_time=start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=TIMEZONE_NAME,
            ),
            end=DateTimeTimeZone(
                date_time=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=TIMEZONE_NAME,
            ),
            is_reminder_on=False,
        )

        try:
            await (
                self.client.users.by_user_id(user_email)
                .calendars.by_calendar_id(calendar_id)
                .events.post(event)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to create event for {user_email}: {e}")
            return False

    async def delete_event(
        self,
        user_email: str,
        calendar_id: str,
        event_id: str,
    ) -> bool:
        """Delete a calendar event."""
        try:
            await (
                self.client.users.by_user_id(user_email)
                .calendars.by_calendar_id(calendar_id)
                .events.by_event_id(event_id)
                .delete()
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete event {event_id}: {e}")
            return False

    async def sync_user(
        self,
        user_email: str,
        entries: list[ScheduleEntry],
        start_date: date,
        end_date: date,
        dry_run: bool = False,
    ) -> PersonalSyncResult:
        """Sync schedule entries to a user's personal calendar.

        Args:
            user_email: User's email address
            entries: Schedule entries for this user
            start_date: Start of sync range
            end_date: End of sync range
            dry_run: If True, preview without making changes

        Returns:
            PersonalSyncResult with counts and errors
        """
        result = PersonalSyncResult(user=user_email)

        # Get or create calendar
        calendar_id = await self.get_or_create_calendar(user_email)
        if not calendar_id:
            result.errors.append("Failed to get/create calendar")
            return result

        # Get existing events
        existing = await self.get_existing_events(user_email, calendar_id, start_date, end_date)

        # Build set of new event keys
        new_event_keys: set[str] = set()
        entries_by_key: dict[str, ScheduleEntry] = {}

        for entry in entries:
            subject = make_event_subject(entry)
            start_time = entry.start_datetime.strftime("%H:%M")
            key = f"{entry.date}|{subject}|{start_time}"
            new_event_keys.add(key)
            entries_by_key[key] = entry

        # Determine what to create and delete
        existing_keys = set(existing.keys())
        to_create = new_event_keys - existing_keys
        to_delete = existing_keys - new_event_keys

        if dry_run:
            result.events_created = len(to_create)
            result.events_deleted = len(to_delete)
            for key in to_create:
                entry = entries_by_key[key]
                logger.info(f"Would create: {entry.date} - {entry.position}")
            for key in to_delete:
                logger.info(f"Would delete: {key}")
        else:
            # Create new events
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

            async def create_with_semaphore(entry: ScheduleEntry) -> bool:
                async with semaphore:
                    return await self.create_event(user_email, calendar_id, entry)

            if to_create:
                create_tasks = [create_with_semaphore(entries_by_key[key]) for key in to_create]
                create_results = await asyncio.gather(*create_tasks)
                result.events_created = sum(1 for r in create_results if r)
                result.errors.extend(["Failed to create event" for r in create_results if not r])

            # Delete old events
            async def delete_with_semaphore(event_id: str) -> bool:
                async with semaphore:
                    return await self.delete_event(user_email, calendar_id, event_id)

            if to_delete:
                delete_tasks = [delete_with_semaphore(existing[key]) for key in to_delete]
                delete_results = await asyncio.gather(*delete_tasks)
                result.events_deleted = sum(1 for r in delete_results if r)
                result.errors.extend(["Failed to delete event" for r in delete_results if not r])

        return result

    def sync(
        self,
        user_email: str,
        entries: list[ScheduleEntry],
        start_date: date,
        end_date: date,
        dry_run: bool = False,
    ) -> PersonalSyncResult:
        """Synchronous wrapper for sync_user."""
        return asyncio.run(self.sync_user(user_email, entries, start_date, end_date, dry_run))
