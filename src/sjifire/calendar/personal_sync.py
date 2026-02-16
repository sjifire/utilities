"""Personal calendar sync - Aladtec schedule to each user's M365 calendar."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
from msgraph.generated.models.event import Event
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.users.item.calendars.item.events.events_request_builder import (
    EventsRequestBuilder,
)

from sjifire.aladtec.schedule_scraper import ScheduleEntry
from sjifire.calendar.models import get_aladtec_url
from sjifire.core.config import (
    get_graph_credentials,
    get_org_config,
    get_timezone,
    get_timezone_name,
)

logger = logging.getLogger(__name__)

# Timezone loaded from organization.json via get_timezone() / get_timezone_name().

# Concurrency limit for parallel API calls
MAX_CONCURRENT_REQUESTS = 5


@dataclass
class ExistingEvent:
    """Info about an existing calendar event."""

    event_id: str
    body: str


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
    return f"{entry.section} - {entry.position}"


def make_event_body(entry: ScheduleEntry) -> str:
    """Create event body from schedule entry."""
    aladtec_url = get_aladtec_url()
    return f"""Position: {entry.position}
Section: {entry.section}

This event is automatically imported from Aladtec. Any changes will be overwritten.

Modify your schedule: {aladtec_url}"""


def normalize_body_for_comparison(body: str) -> str:
    """Normalize body text for comparison.

    Microsoft Exchange converts plain text to HTML, so we need to
    extract text content and normalize whitespace for comparison.
    """
    import re

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", body)
    # Normalize whitespace (collapse multiple spaces/newlines)
    text = " ".join(text.split())
    return text


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
        self._uses_primary_calendar: set[str] = set()  # users using primary calendar

    async def ensure_aladtec_category(self, user_email: str) -> bool:
        """Ensure the Aladtec category exists in user's master category list.

        Creates the category with orange color if it doesn't exist.
        This makes the category visible in Outlook's category picker.

        Returns:
            True if category exists or was created successfully
        """
        try:
            # Get existing categories
            result = await self.client.users.by_user_id(user_email).outlook.master_categories.get()

            # Check if Aladtec already exists
            if result and result.value:
                for cat in result.value:
                    if cat.display_name == get_org_config().calendar_category:
                        return True  # Already exists

            # Create the category with orange color
            from msgraph.generated.models.outlook_category import OutlookCategory

            new_cat = OutlookCategory(
                display_name=get_org_config().calendar_category,
                color="preset6",  # Orange
            )
            await self.client.users.by_user_id(user_email).outlook.master_categories.post(new_cat)
            logger.info(f"Created Aladtec category for {user_email}")
            return True
        except Exception as e:
            logger.warning(f"Could not create Aladtec category for {user_email}: {e}")
            return False

    async def _get_primary_calendar_id(self, user_email: str) -> str | None:
        """Get the user's primary (default) calendar ID."""
        try:
            # The /calendar endpoint returns the default calendar
            calendar = await self.client.users.by_user_id(user_email).calendar.get()
            if calendar and calendar.id:
                return calendar.id
        except Exception as e:
            logger.error(f"Failed to get primary calendar for {user_email}: {e}")
        return None

    async def get_or_create_calendar(self, user_email: str) -> str | None:
        """Get the user's primary calendar for Aladtec events.

        Returns:
            Calendar ID or None if failed
        """
        # Check cache first
        if user_email in self._calendar_cache:
            return self._calendar_cache[user_email]

        calendar_id = await self._get_primary_calendar_id(user_email)
        if calendar_id:
            self._calendar_cache[user_email] = calendar_id
            self._uses_primary_calendar.add(user_email.lower())
            return calendar_id

        return None

    async def get_existing_events(
        self,
        user_email: str,
        calendar_id: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, ExistingEvent]:
        """Get existing Aladtec events in date range.

        When using primary calendar, only returns events with the Aladtec category.
        When using dedicated Aladtec calendar, returns all events.

        Returns:
            Dict mapping event_key to ExistingEvent (id and body)
        """
        # Note: We filter by date after fetching, not by calendarView endpoint
        # because we need to match events by key for sync logic
        _ = start_date, end_date  # Used for filtering below

        # If using primary calendar, filter by Aladtec category
        uses_primary = user_email.lower() in self._uses_primary_calendar
        filter_query = None
        if uses_primary:
            filter_query = f"categories/any(c:c eq '{get_org_config().calendar_category}')"

        try:
            # Get events from this calendar
            query_params = EventsRequestBuilder.EventsRequestBuilderGetQueryParameters(
                top=500,
                select=["id", "subject", "start", "end", "body", "categories"],
                filter=filter_query,
            )
            config = EventsRequestBuilder.EventsRequestBuilderGetRequestConfiguration(
                query_parameters=query_params,
            )

            result = await (
                self.client.users.by_user_id(user_email)
                .calendars.by_calendar_id(calendar_id)
                .events.get(request_configuration=config)
            )

            events_by_key: dict[str, ExistingEvent] = {}

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
                            event_dt = event_dt.astimezone(get_timezone())
                        elif event_tz:
                            # Try to use the specified timezone
                            try:
                                from zoneinfo import ZoneInfo

                                tz = ZoneInfo(event_tz)
                                event_dt = event_dt.replace(tzinfo=tz)
                                event_dt = event_dt.astimezone(get_timezone())
                            except KeyError:
                                # Unknown timezone, assume local
                                event_dt = event_dt.replace(tzinfo=get_timezone())
                        else:
                            # No timezone - assume local
                            event_dt = event_dt.replace(tzinfo=get_timezone())

                        event_date = event_dt.date()
                    except ValueError:
                        continue

                    # Only include events in our date range
                    if start_date <= event_date <= end_date:
                        # Parse end time
                        end_time_str = "00:00"
                        if event.end and event.end.date_time:
                            end_dt_str = event.end.date_time
                            if "." in end_dt_str:
                                end_dt_str = end_dt_str.split(".")[0]
                            try:
                                end_dt = datetime.fromisoformat(end_dt_str)
                                # Apply same timezone logic
                                end_tz = event.end.time_zone
                                if end_tz and end_tz.upper() == "UTC":
                                    end_dt = end_dt.replace(tzinfo=ZoneInfo("UTC"))
                                    end_dt = end_dt.astimezone(get_timezone())
                                elif end_tz:
                                    try:
                                        tz = ZoneInfo(end_tz)
                                        end_dt = end_dt.replace(tzinfo=tz)
                                        end_dt = end_dt.astimezone(get_timezone())
                                    except KeyError:
                                        end_dt = end_dt.replace(tzinfo=get_timezone())
                                else:
                                    end_dt = end_dt.replace(tzinfo=get_timezone())
                                end_time_str = end_dt.strftime("%H:%M")
                            except ValueError:
                                pass

                        # Create key from subject, start time, and end time
                        # Format: "date|subject|start_time|end_time"
                        start_time = event_dt.strftime("%H:%M")
                        key = f"{event_date}|{event.subject}|{start_time}|{end_time_str}"
                        # Extract body content
                        body = ""
                        if event.body and event.body.content:
                            body = event.body.content
                        events_by_key[key] = ExistingEvent(event_id=event.id, body=body)

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

        # Add category if using primary calendar (to identify Aladtec events)
        categories = None
        if user_email.lower() in self._uses_primary_calendar:
            categories = [get_org_config().calendar_category]

        event = Event(
            subject=make_event_subject(entry),
            body=ItemBody(
                content_type=BodyType.Text,
                content=make_event_body(entry),
            ),
            start=DateTimeTimeZone(
                date_time=start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=get_timezone_name(),
            ),
            end=DateTimeTimeZone(
                date_time=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=get_timezone_name(),
            ),
            is_reminder_on=False,
            categories=categories,
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

    async def update_event(
        self,
        user_email: str,
        calendar_id: str,
        event_id: str,
        entry: ScheduleEntry,
    ) -> bool:
        """Update an existing calendar event."""
        start_dt = entry.start_datetime
        end_dt = entry.end_datetime

        # Add category if using primary calendar (to identify Aladtec events)
        categories = None
        if user_email.lower() in self._uses_primary_calendar:
            categories = [get_org_config().calendar_category]

        event = Event(
            subject=make_event_subject(entry),
            body=ItemBody(
                content_type=BodyType.Text,
                content=make_event_body(entry),
            ),
            start=DateTimeTimeZone(
                date_time=start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=get_timezone_name(),
            ),
            end=DateTimeTimeZone(
                date_time=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                time_zone=get_timezone_name(),
            ),
            is_reminder_on=False,
            categories=categories,
        )

        try:
            await (
                self.client.users.by_user_id(user_email)
                .calendars.by_calendar_id(calendar_id)
                .events.by_event_id(event_id)
                .patch(event)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update event {event_id}: {e}")
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

    async def get_aladtec_category_events(
        self,
        user_email: str,
        calendar_id: str,
    ) -> list[tuple[str, str, str]]:
        """Get all events with the Aladtec category.

        Returns:
            List of (event_id, subject, start_date) tuples
        """
        try:
            query_params = EventsRequestBuilder.EventsRequestBuilderGetQueryParameters(
                top=500,
                select=["id", "subject", "start"],
                filter=f"categories/any(c:c eq '{get_org_config().calendar_category}')",
            )
            config = EventsRequestBuilder.EventsRequestBuilderGetRequestConfiguration(
                query_parameters=query_params,
            )

            result = await (
                self.client.users.by_user_id(user_email)
                .calendars.by_calendar_id(calendar_id)
                .events.get(request_configuration=config)
            )

            events: list[tuple[str, str, str]] = []
            if result and result.value:
                for event in result.value:
                    if event.id:
                        start_str = ""
                        if event.start and event.start.date_time:
                            start_str = event.start.date_time[:10]  # Just the date
                        events.append((event.id, event.subject or "", start_str))

            return events

        except Exception as e:
            logger.error(f"Failed to get Aladtec events for {user_email}: {e}")
            return []

    async def purge_aladtec_events(
        self,
        user_email: str,
        dry_run: bool = False,
    ) -> tuple[int, int]:
        """Delete all events with Aladtec category from user's primary calendar.

        Args:
            user_email: User's email address
            dry_run: If True, preview without making changes

        Returns:
            Tuple of (deleted_count, error_count)
        """
        # Get primary calendar
        calendar_id = await self._get_primary_calendar_id(user_email)
        if not calendar_id:
            logger.error(f"Could not get primary calendar for {user_email}")
            return 0, 1

        # Mark as using primary calendar for category filtering
        self._uses_primary_calendar.add(user_email.lower())

        # Get all Aladtec events
        events = await self.get_aladtec_category_events(user_email, calendar_id)

        if not events:
            logger.info(f"No Aladtec events found for {user_email}")
            return 0, 0

        logger.info(f"Found {len(events)} Aladtec events for {user_email}")

        if dry_run:
            for _event_id, subject, start_date in events:
                logger.info(f"  Would delete: {start_date} - {subject}")
            return len(events), 0

        # Delete events
        deleted = 0
        errors = 0
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async def delete_with_semaphore(event_id: str) -> bool:
            async with semaphore:
                return await self.delete_event(user_email, calendar_id, event_id)

        delete_tasks = [delete_with_semaphore(eid) for eid, _, _ in events]
        results = await asyncio.gather(*delete_tasks)

        for (_event_id, subject, start_date), success in zip(events, results, strict=True):
            if success:
                deleted += 1
                logger.debug(f"  Deleted: {start_date} - {subject}")
            else:
                errors += 1

        return deleted, errors

    async def sync_user(
        self,
        user_email: str,
        entries: list[ScheduleEntry],
        start_date: date,
        end_date: date,
        dry_run: bool = False,
        force: bool = False,
    ) -> PersonalSyncResult:
        """Sync schedule entries to a user's personal calendar.

        Args:
            user_email: User's email address
            entries: Schedule entries for this user
            start_date: Start of sync range
            end_date: End of sync range
            dry_run: If True, preview without making changes
            force: If True, update all events even if body hasn't changed

        Returns:
            PersonalSyncResult with counts and errors
        """
        result = PersonalSyncResult(user=user_email)

        # Get or create calendar
        calendar_id = await self.get_or_create_calendar(user_email)
        if not calendar_id:
            result.errors.append("Failed to get/create calendar")
            return result

        # Ensure Aladtec category exists in user's category list
        await self.ensure_aladtec_category(user_email)

        # Get existing events
        existing = await self.get_existing_events(user_email, calendar_id, start_date, end_date)

        # Build set of new event keys
        new_event_keys: set[str] = set()
        entries_by_key: dict[str, ScheduleEntry] = {}

        for entry in entries:
            subject = make_event_subject(entry)
            start_time = entry.start_datetime.strftime("%H:%M")
            end_time = entry.end_datetime.strftime("%H:%M")
            key = f"{entry.date}|{subject}|{start_time}|{end_time}"
            new_event_keys.add(key)
            entries_by_key[key] = entry

        # Determine what to create, update, and delete
        existing_keys = set(existing.keys())
        to_create = new_event_keys - existing_keys
        to_delete = existing_keys - new_event_keys
        maybe_update = new_event_keys & existing_keys  # Keys in both sets

        # Check which existing events need body updates
        to_update: list[tuple[str, str]] = []  # (key, event_id)
        for key in maybe_update:
            if force:
                # Force update all matching events
                to_update.append((key, existing[key].event_id))
            else:
                # Only update if body has changed
                entry = entries_by_key[key]
                new_body = make_event_body(entry)
                existing_body = existing[key].body
                # Normalize both for comparison (Exchange converts plain text to HTML)
                if normalize_body_for_comparison(new_body) != normalize_body_for_comparison(
                    existing_body
                ):
                    logger.debug(f"Body mismatch for {key}")
                    to_update.append((key, existing[key].event_id))

        if dry_run:
            result.events_created = len(to_create)
            result.events_updated = len(to_update)
            result.events_deleted = len(to_delete)
            for key in to_create:
                entry = entries_by_key[key]
                logger.info(f"Would create: {entry.date} - {entry.position}")
            for key, _ in to_update:
                entry = entries_by_key[key]
                logger.info(f"Would update: {entry.date} - {entry.position}")
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

            # Update existing events with changed bodies
            async def update_with_semaphore(key: str, event_id: str) -> bool:
                async with semaphore:
                    return await self.update_event(
                        user_email, calendar_id, event_id, entries_by_key[key]
                    )

            if to_update:
                update_tasks = [update_with_semaphore(key, eid) for key, eid in to_update]
                update_results = await asyncio.gather(*update_tasks)
                result.events_updated = sum(1 for r in update_results if r)
                result.errors.extend(["Failed to update event" for r in update_results if not r])

            # Delete old events
            async def delete_with_semaphore(event_id: str) -> bool:
                async with semaphore:
                    return await self.delete_event(user_email, calendar_id, event_id)

            if to_delete:
                delete_tasks = [delete_with_semaphore(existing[key].event_id) for key in to_delete]
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
        force: bool = False,
    ) -> PersonalSyncResult:
        """Synchronous wrapper for sync_user."""
        return asyncio.run(
            self.sync_user(user_email, entries, start_date, end_date, dry_run, force)
        )
