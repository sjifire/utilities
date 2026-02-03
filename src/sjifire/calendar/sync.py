"""Calendar sync logic for M365 shared calendar."""

import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
from msgraph.generated.models.event import Event
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
    CalendarViewRequestBuilder,
)
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from sjifire.aladtec.schedule import DaySchedule, ScheduleEntry
from sjifire.calendar.models import AllDayDutyEvent, CrewMember, OnDutyEvent, SyncResult
from sjifire.core.config import get_graph_credentials

logger = logging.getLogger(__name__)

# Standard shift times
SHIFT_START_HOUR = 18  # 6 PM
SHIFT_END_HOUR = 18  # 6 PM next day

# Timezone for all operations
TIMEZONE = ZoneInfo("America/Los_Angeles")
TIMEZONE_NAME = "America/Los_Angeles"

# Sections to exclude entirely from calendar events
EXCLUDED_SECTIONS = [
    "Administration",
    "Operations",
    "Prevention",
    "Training",
    "Trades",
    "State Mobe",
    "Time Off",
]


def should_exclude_section(section: str) -> bool:
    """Check if section should be excluded."""
    return section in EXCLUDED_SECTIONS


def is_unfilled_position(entry: ScheduleEntry) -> bool:
    """Check if this is an unfilled position placeholder.

    Unfilled positions have names like "Section / Position".
    Real person names don't contain " / ".
    """
    return " / " in entry.name


def is_filled_entry(entry: ScheduleEntry) -> bool:
    """Check if this entry represents a real person."""
    if not entry.name:
        return False
    if is_unfilled_position(entry):
        return False
    return True


class CalendarSync:
    """Sync on-duty schedule to M365 shared calendar."""

    def __init__(self, mailbox: str = "svc-automations@sjifire.org") -> None:
        """Initialize with Graph API credentials.

        Args:
            mailbox: Email address of the shared mailbox calendar
        """
        self.mailbox = mailbox
        tenant_id, client_id, client_secret = get_graph_credentials()

        self.credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        self.client = GraphServiceClient(credentials=self.credential)
        self._user_cache: dict[str, dict] | None = None

    async def _load_user_contacts(self) -> dict[str, dict]:
        """Load all user contact info from Entra ID.

        Returns:
            Dict mapping display name to {email, phone}
        """
        if self._user_cache is not None:
            return self._user_cache

        logger.info("Loading user contacts from Entra ID...")

        query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
            select=["displayName", "mail", "mobilePhone"],
            top=999,
        )
        config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration(
            query_parameters=query_params,
        )

        try:
            result = await self.client.users.get(request_configuration=config)
            self._user_cache = {}

            if result and result.value:
                for user in result.value:
                    if user.display_name:
                        # Store by display name (e.g., "Capt John Smith")
                        self._user_cache[user.display_name] = {
                            "email": user.mail,
                            "phone": user.mobile_phone,
                        }
                        # Also store by just first+last (e.g., "John Smith")
                        # Extract name without rank prefix
                        name_parts = user.display_name.split()
                        if len(name_parts) >= 2:
                            # Try without first word (might be rank)
                            plain_name = " ".join(name_parts[1:])
                            if plain_name not in self._user_cache:
                                self._user_cache[plain_name] = {
                                    "email": user.mail,
                                    "phone": user.mobile_phone,
                                }

            logger.info(f"Loaded {len(self._user_cache)} user contacts")
        except Exception as e:
            logger.error(f"Failed to load user contacts: {e}")
            self._user_cache = {}

        return self._user_cache

    def _lookup_contact(
        self, name: str, user_cache: dict[str, dict]
    ) -> tuple[str | None, str | None]:
        """Look up contact info for a name.

        Args:
            name: Person's name from schedule
            user_cache: Dict of user contacts

        Returns:
            Tuple of (email, phone) or (None, None)
        """
        # Try exact match first
        if name in user_cache:
            info = user_cache[name]
            return info.get("email"), info.get("phone")

        # Try case-insensitive match
        name_lower = name.lower()
        for display_name, info in user_cache.items():
            if display_name.lower() == name_lower:
                return info.get("email"), info.get("phone")
            # Try if name is contained (e.g., "John Smith" in "Capt John Smith")
            if name_lower in display_name.lower():
                return info.get("email"), info.get("phone")

        return None, None

    def convert_schedules_to_events(
        self,
        schedules: list[DaySchedule],
        user_cache: dict[str, dict],
    ) -> list[AllDayDutyEvent]:
        """Convert Aladtec schedules to all-day calendar events.

        For each calendar date, creates an all-day event showing:
        - Until 6 PM: Crew from previous day's shift
        - From 6 PM: Crew starting that day's shift

        Args:
            schedules: List of daily schedules from Aladtec
            user_cache: Dict of user contacts from Entra ID

        Returns:
            List of AllDayDutyEvent objects ready for calendar sync
        """
        # Build a lookup by date for quick access
        schedules_by_date: dict[date, DaySchedule] = {
            ds.date: ds for ds in schedules
        }

        # Determine the full date range we need to cover
        if not schedules:
            return []

        # Get all unique dates from schedules
        all_dates = sorted(schedules_by_date.keys())

        # For each date, we show crew "until 1800" (from previous day's shift)
        # and "from 1800" (from this day's shift)
        # The first date in range won't have "until 1800" data from previous day
        # unless that previous day is also in our data

        events: list[AllDayDutyEvent] = []

        for event_date in all_dates:
            # Previous day's schedule provides "until 1800" crew
            prev_date = event_date - timedelta(days=1)
            prev_schedule = schedules_by_date.get(prev_date)

            # This day's schedule provides "from 1800" crew
            today_schedule = schedules_by_date.get(event_date)

            # Build until 1800 crew (from previous day's shift)
            until_1800_crew: dict[str, list[CrewMember]] = {}
            until_1800_platoon = ""
            if prev_schedule:
                until_1800_platoon = prev_schedule.platoon
                filled = self._get_filled_entries(prev_schedule)
                until_1800_crew = self._entries_to_crew(filled, user_cache)

            # Build from 1800 crew (from today's shift)
            from_1800_crew: dict[str, list[CrewMember]] = {}
            from_1800_platoon = ""
            if today_schedule:
                from_1800_platoon = today_schedule.platoon
                filled = self._get_filled_entries(today_schedule)
                from_1800_crew = self._entries_to_crew(filled, user_cache)

            # Only create event if we have at least some crew data
            if until_1800_crew or from_1800_crew:
                events.append(AllDayDutyEvent(
                    event_date=event_date,
                    until_1800_platoon=until_1800_platoon,
                    until_1800_crew=until_1800_crew,
                    from_1800_platoon=from_1800_platoon,
                    from_1800_crew=from_1800_crew,
                ))

        # Sort by date
        events.sort(key=lambda e: e.event_date)

        logger.info(f"Converted {len(schedules)} days to {len(events)} all-day events")
        return events

    def _get_filled_entries(self, day_schedule: DaySchedule) -> list[ScheduleEntry]:
        """Get filled entries from a day schedule, filtering excluded sections."""
        filled = []
        for entry in day_schedule.entries:
            # Skip excluded sections
            if should_exclude_section(entry.section):
                continue
            # Skip unfilled positions
            if not is_filled_entry(entry):
                continue
            filled.append(entry)
        return filled

    def _entries_to_crew(
        self,
        entries: list[ScheduleEntry],
        user_cache: dict[str, dict],
    ) -> dict[str, list[CrewMember]]:
        """Convert schedule entries to crew dict with contact info.

        Deduplicates entries by (section, position, name) to avoid
        showing the same person multiple times.

        Args:
            entries: List of schedule entries
            user_cache: Dict of user contacts

        Returns:
            Dict mapping section to list of CrewMember objects
        """
        crew: dict[str, list[CrewMember]] = {}
        seen: set[tuple[str, str, str]] = set()  # (section, position, name)

        for entry in entries:
            # Deduplicate by section, position, name
            key = (entry.section, entry.position, entry.name)
            if key in seen:
                continue
            seen.add(key)

            if entry.section not in crew:
                crew[entry.section] = []

            email, phone = self._lookup_contact(entry.name, user_cache)

            crew[entry.section].append(CrewMember(
                name=entry.name,
                position=entry.position,
                email=email,
                phone=phone,
            ))

        return crew

    async def get_existing_events(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[date, str]:
        """Fetch existing On Duty events from the calendar.

        Returns:
            Dict mapping event date to event ID
        """
        # Extend end date to capture full range
        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=TIMEZONE)
        end_dt = datetime.combine(
            end_date + timedelta(days=1), datetime.min.time(), tzinfo=TIMEZONE
        )

        query_params = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
            start_date_time=start_dt.isoformat(),
            end_date_time=end_dt.isoformat(),
            filter="startswith(subject, 'On Duty')",
            top=500,
            select=["id", "subject", "start", "end", "isAllDay"],
        )
        config = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetRequestConfiguration(
            query_parameters=query_params,
        )

        try:
            result = await self.client.users.by_user_id(self.mailbox).calendar_view.get(
                request_configuration=config
            )
        except Exception as e:
            logger.error(f"Failed to fetch existing events: {e}")
            return {}

        events_by_date: dict[date, str] = {}

        if result and result.value:
            for item in result.value:
                # Parse start date
                event_date = self._parse_graph_date(item.start)
                if event_date and item.id:
                    events_by_date[event_date] = item.id

        logger.debug(f"Found {len(events_by_date)} existing On Duty events")
        return events_by_date

    def _parse_graph_date(self, dt: DateTimeTimeZone | None) -> date | None:
        """Parse Graph API datetime to Python date."""
        if not dt or not dt.date_time:
            return None

        try:
            # Graph returns ISO format, we just need the date part
            date_str = dt.date_time.split("T")[0]
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            return None

    async def create_event(self, event: AllDayDutyEvent) -> str | None:
        """Create an all-day calendar event with HTML body."""
        # For all-day events, use date only (no time component)
        # End date should be the next day for a single all-day event
        start_date = event.event_date.strftime("%Y-%m-%d")
        end_date = (event.event_date + timedelta(days=1)).strftime("%Y-%m-%d")

        graph_event = Event(
            subject=event.subject,
            body=ItemBody(
                content_type=BodyType.Html,
                content=event.body_html,
            ),
            start=DateTimeTimeZone(
                date_time=start_date,
                time_zone=TIMEZONE_NAME,
            ),
            end=DateTimeTimeZone(
                date_time=end_date,
                time_zone=TIMEZONE_NAME,
            ),
            is_all_day=True,
        )

        try:
            result = await self.client.users.by_user_id(self.mailbox).events.post(graph_event)
            return result.id if result else None
        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            return None

    async def update_event(self, event: AllDayDutyEvent) -> bool:
        """Update an existing all-day calendar event."""
        if not event.event_id:
            logger.error("Cannot update event without event_id")
            return False

        # For all-day events, use date only (no time component)
        start_date = event.event_date.strftime("%Y-%m-%d")
        end_date = (event.event_date + timedelta(days=1)).strftime("%Y-%m-%d")

        graph_event = Event(
            subject=event.subject,
            body=ItemBody(
                content_type=BodyType.Html,
                content=event.body_html,
            ),
            start=DateTimeTimeZone(
                date_time=start_date,
                time_zone=TIMEZONE_NAME,
            ),
            end=DateTimeTimeZone(
                date_time=end_date,
                time_zone=TIMEZONE_NAME,
            ),
            is_all_day=True,
        )

        try:
            await self.client.users.by_user_id(self.mailbox).events.by_event_id(
                event.event_id
            ).patch(graph_event)
            return True
        except Exception as e:
            logger.error(f"Failed to update event {event.event_id}: {e}")
            return False

    async def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event."""
        try:
            await self.client.users.by_user_id(self.mailbox).events.by_event_id(
                event_id
            ).delete()
            return True
        except Exception as e:
            logger.error(f"Failed to delete event {event_id}: {e}")
            return False

    async def sync_events(
        self,
        new_events: list[AllDayDutyEvent],
        start_date: date,
        end_date: date,
        dry_run: bool = False,
    ) -> SyncResult:
        """Sync all-day events to calendar, updating/creating/deleting as needed."""
        result = SyncResult()

        # Get existing events (date -> event_id mapping)
        existing_by_date = await self.get_existing_events(start_date, end_date)

        # Track which existing events we've matched
        matched_dates: set[date] = set()

        for new_event in new_events:
            event_date = new_event.event_date
            existing_id = existing_by_date.get(event_date)

            if existing_id:
                matched_dates.add(event_date)
                # Always update to refresh content with contact info
                logger.info(f"Updating event: {new_event.subject} on {event_date}")
                if not dry_run:
                    new_event.event_id = existing_id
                    if await self.update_event(new_event):
                        result.events_updated += 1
                    else:
                        result.errors.append(f"Failed to update {event_date}")
                else:
                    result.events_updated += 1
            else:
                # Create new event
                logger.info(f"Creating event: {new_event.subject} on {event_date}")
                if not dry_run:
                    event_id = await self.create_event(new_event)
                    if event_id:
                        result.events_created += 1
                    else:
                        result.errors.append(f"Failed to create {event_date}")
                else:
                    result.events_created += 1

        # Note: We intentionally do NOT delete orphaned events.
        # If Aladtec returns incomplete data, we don't want to lose valid events.

        return result

    def sync(
        self,
        schedules: list[DaySchedule],
        dry_run: bool = False,
    ) -> SyncResult:
        """Synchronous wrapper for sync_events."""
        if not schedules:
            logger.warning("No schedules to sync")
            return SyncResult()

        async def _async_sync() -> SyncResult:
            # Load user contacts first
            user_cache = await self._load_user_contacts()

            # Convert schedules to all-day events with contact info
            events = self.convert_schedules_to_events(schedules, user_cache)

            if not events:
                logger.warning("No events generated from schedules")
                return SyncResult()

            # Determine date range from all-day events
            start_date = min(e.event_date for e in events)
            end_date = max(e.event_date for e in events)

            return await self.sync_events(events, start_date, end_date, dry_run)

        return asyncio.run(_async_sync())
