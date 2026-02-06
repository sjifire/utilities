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
from msgraph.generated.users.item.calendars.item.events.events_request_builder import (
    EventsRequestBuilder,
)

from sjifire.aladtec.schedule_scraper import ScheduleEntry
from sjifire.calendar.models import get_aladtec_url
from sjifire.core.config import get_graph_credentials

logger = logging.getLogger(__name__)

# Calendar name in each user's mailbox
CALENDAR_NAME = "Aladtec Schedule"

# Entra extension attribute to store calendar ID (survives renames)
CALENDAR_ID_ATTRIBUTE = "extension_attribute5"

# Timezone for all operations
TIMEZONE_NAME = "America/Los_Angeles"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

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

This event is imported automatically from Aladtec. Any changes will be overwritten.

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

    async def _get_stored_calendar_id(self, user_email: str) -> str | None:
        """Get stored calendar ID from user's Entra extension attribute.

        Returns:
            Calendar ID or None if not stored
        """
        try:
            from msgraph.generated.users.item.user_item_request_builder import (
                UserItemRequestBuilder,
            )

            query_params = UserItemRequestBuilder.UserItemRequestBuilderGetQueryParameters(
                select=["id", "onPremisesExtensionAttributes"],
            )
            config = UserItemRequestBuilder.UserItemRequestBuilderGetRequestConfiguration(
                query_parameters=query_params,
            )
            user = await self.client.users.by_user_id(user_email).get(request_configuration=config)
            if user and user.on_premises_extension_attributes:
                cal_id = getattr(
                    user.on_premises_extension_attributes,
                    CALENDAR_ID_ATTRIBUTE,
                    None,
                )
                if cal_id and str(cal_id).strip():
                    return str(cal_id).strip()
        except Exception as e:
            logger.debug(f"Could not get stored calendar ID for {user_email}: {e}")
        return None

    async def _store_calendar_id(self, user_email: str, calendar_id: str) -> bool:
        """Store calendar ID in user's Entra extension attribute.

        Returns:
            True if successful
        """
        if not calendar_id or not calendar_id.strip():
            return False
        try:
            from msgraph.generated.models.on_premises_extension_attributes import (
                OnPremisesExtensionAttributes,
            )
            from msgraph.generated.models.user import User

            ext_attrs = OnPremisesExtensionAttributes()
            setattr(ext_attrs, CALENDAR_ID_ATTRIBUTE, calendar_id)

            update = User(on_premises_extension_attributes=ext_attrs)
            await self.client.users.by_user_id(user_email).patch(update)
            logger.debug(f"Stored calendar ID for {user_email}")
            return True
        except Exception as e:
            # Log at debug level - this is expected to fail for some users
            logger.debug(f"Could not store calendar ID for {user_email}: {e}")
            return False

    async def _calendar_exists(self, user_email: str, calendar_id: str) -> bool:
        """Check if a calendar exists.

        Returns:
            True if calendar exists
        """
        if not calendar_id or not calendar_id.strip():
            return False
        try:
            cal = await (
                self.client.users.by_user_id(user_email).calendars.by_calendar_id(calendar_id).get()
            )
            return cal is not None and cal.id is not None
        except Exception:
            return False

    async def get_or_create_calendar(self, user_email: str) -> str | None:
        """Get or create the Aladtec Schedule calendar for a user.

        Uses Entra extension attribute to store calendar ID, which survives
        renames. Falls back to name matching for backwards compatibility.

        Returns:
            Calendar ID or None if failed
        """
        # Check cache first
        if user_email in self._calendar_cache:
            return self._calendar_cache[user_email]

        try:
            # First, check for stored calendar ID in Entra
            stored_id = await self._get_stored_calendar_id(user_email)
            if stored_id and await self._calendar_exists(user_email, stored_id):
                self._calendar_cache[user_email] = stored_id
                logger.debug(f"Found calendar by stored ID for {user_email}")
                return stored_id

            # Fall back to name-based lookup
            result = await self.client.users.by_user_id(user_email).calendars.get()

            if result and result.value:
                for cal in result.value:
                    if cal.name == CALENDAR_NAME and cal.id:
                        self._calendar_cache[user_email] = cal.id
                        # Store ID for future lookups
                        await self._store_calendar_id(user_email, cal.id)
                        logger.debug(f"Found calendar by name for {user_email}")
                        return cal.id

            # Create new calendar
            new_calendar = Calendar(name=CALENDAR_NAME)
            created = await self.client.users.by_user_id(user_email).calendars.post(new_calendar)

            if created and created.id:
                self._calendar_cache[user_email] = created.id
                # Store ID for future lookups
                await self._store_calendar_id(user_email, created.id)
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
    ) -> dict[str, ExistingEvent]:
        """Get existing Aladtec events in date range.

        Returns:
            Dict mapping event_key to ExistingEvent (id and body)
        """
        # Note: We filter by date after fetching, not by calendarView endpoint
        # because we need to match events by key for sync logic
        _ = start_date, end_date  # Used for filtering below

        try:
            # Get all events from this calendar (it's dedicated to Aladtec)
            query_params = EventsRequestBuilder.EventsRequestBuilderGetQueryParameters(
                top=500,
                select=["id", "subject", "start", "end", "body"],
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
                                    end_dt = end_dt.astimezone(TIMEZONE)
                                elif end_tz:
                                    try:
                                        tz = ZoneInfo(end_tz)
                                        end_dt = end_dt.replace(tzinfo=tz)
                                        end_dt = end_dt.astimezone(TIMEZONE)
                                    except KeyError:
                                        end_dt = end_dt.replace(tzinfo=TIMEZONE)
                                else:
                                    end_dt = end_dt.replace(tzinfo=TIMEZONE)
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
