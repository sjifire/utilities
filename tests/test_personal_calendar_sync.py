"""Tests for sjifire.calendar.personal_sync module."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.aladtec.schedule_scraper import ScheduleEntry
from sjifire.calendar.personal_sync import (
    ExistingEvent,
    PersonalCalendarSync,
    make_event_body,
    make_event_subject,
    normalize_body_for_comparison,
)
from sjifire.core.config import get_org_config

ALADTEC_CATEGORY = get_org_config().calendar_category

# =============================================================================
# Helper Function Tests
# =============================================================================


class TestMakeEventSubject:
    """Tests for make_event_subject function."""

    def test_formats_section_and_position(self):
        """Subject includes section and position."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Captain",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        assert make_event_subject(entry) == "S31 - Captain"

    def test_backup_duty_position(self):
        """Backup Duty positions format correctly."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="Backup Duty",
            position="Backup Duty Officer",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        assert make_event_subject(entry) == "Backup Duty - Backup Duty Officer"


class TestMakeEventBody:
    """Tests for make_event_body function."""

    def test_includes_position(self, mock_env_vars):
        """Body includes position."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Captain",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        body = make_event_body(entry)
        assert "Position: Captain" in body

    def test_includes_section(self, mock_env_vars):
        """Body includes section."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Captain",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        body = make_event_body(entry)
        assert "Section: S31" in body

    def test_includes_auto_import_notice(self, mock_env_vars):
        """Body includes auto-import notice."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Captain",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        body = make_event_body(entry)
        assert "automatically imported from Aladtec" in body


class TestNormalizeBodyForComparison:
    """Tests for normalize_body_for_comparison function."""

    def test_removes_html_tags(self):
        """HTML tags are removed."""
        html = "<p>Hello <strong>World</strong></p>"
        assert normalize_body_for_comparison(html) == "Hello World"

    def test_normalizes_whitespace(self):
        """Multiple spaces and newlines are collapsed."""
        text = "Hello    World\n\nTest"
        assert normalize_body_for_comparison(text) == "Hello World Test"

    def test_plain_text_unchanged(self):
        """Plain text content is preserved."""
        text = "Simple text"
        assert normalize_body_for_comparison(text) == "Simple text"


# =============================================================================
# Primary Calendar Feature Tests
# =============================================================================


class TestEnsureAladtecCategory:
    """Tests for ensure_aladtec_category method."""

    @pytest.fixture
    def sync(self, mock_env_vars):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.mark.asyncio
    async def test_returns_true_when_category_exists(self, sync):
        """Returns True if Aladtec category already exists."""
        mock_category = MagicMock()
        mock_category.display_name = ALADTEC_CATEGORY

        mock_result = MagicMock()
        mock_result.value = [mock_category]

        sync.client.users.by_user_id.return_value.outlook.master_categories.get = AsyncMock(
            return_value=mock_result
        )

        result = await sync.ensure_aladtec_category("test@example.com")

        assert result is True
        # Should not call post since category exists
        sync.client.users.by_user_id.return_value.outlook.master_categories.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_category_when_missing(self, sync):
        """Creates Aladtec category if it doesn't exist."""
        mock_result = MagicMock()
        mock_result.value = []  # No categories

        sync.client.users.by_user_id.return_value.outlook.master_categories.get = AsyncMock(
            return_value=mock_result
        )
        sync.client.users.by_user_id.return_value.outlook.master_categories.post = AsyncMock()

        result = await sync.ensure_aladtec_category("test@example.com")

        assert result is True
        # Should call post to create category
        sync.client.users.by_user_id.return_value.outlook.master_categories.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self, sync):
        """Returns False if API call fails."""
        sync.client.users.by_user_id.return_value.outlook.master_categories.get = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await sync.ensure_aladtec_category("test@example.com")

        assert result is False


class TestGetPrimaryCalendarId:
    """Tests for _get_primary_calendar_id method."""

    @pytest.fixture
    def sync(self, mock_env_vars):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.mark.asyncio
    async def test_returns_calendar_id(self, sync):
        """Returns primary calendar ID on success."""
        mock_calendar = MagicMock()
        mock_calendar.id = "primary-calendar-id"

        sync.client.users.by_user_id.return_value.calendar.get = AsyncMock(
            return_value=mock_calendar
        )

        result = await sync._get_primary_calendar_id("test@example.com")
        assert result == "primary-calendar-id"

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, sync):
        """Returns None when API call fails."""
        sync.client.users.by_user_id.return_value.calendar.get = AsyncMock(
            side_effect=Exception("API Error")
        )

        result = await sync._get_primary_calendar_id("test@example.com")
        assert result is None


class TestGetOrCreateCalendarPrimaryMode:
    """Tests for get_or_create_calendar with primary calendar mode."""

    @pytest.fixture
    def sync(self, mock_env_vars):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.mark.asyncio
    async def test_all_users_get_primary_calendar(self, sync):
        """All users get primary calendar with Aladtec category."""
        mock_calendar = MagicMock()
        mock_calendar.id = "primary-calendar-id"

        sync.client.users.by_user_id.return_value.calendar.get = AsyncMock(
            return_value=mock_calendar
        )

        result = await sync.get_or_create_calendar("test@example.com")

        assert result == "primary-calendar-id"
        assert "test@example.com" in sync._uses_primary_calendar


class TestCreateEventWithCategory:
    """Tests for create_event adding category for primary calendar users."""

    @pytest.fixture
    def sync(self, mock_env_vars):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_entry(self):
        """Create sample schedule entry."""
        return ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Captain",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )

    @pytest.mark.asyncio
    async def test_adds_category_for_primary_calendar_user(self, sync, sample_entry):
        """Events for primary calendar users include Aladtec category."""
        sync._uses_primary_calendar.add("test@example.com")

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.post = AsyncMock()

        await sync.create_event("test@example.com", "calendar-id", sample_entry)

        # Check that post was called with event containing categories
        call_args = sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.post.call_args
        event = call_args[0][0]
        assert event.categories == [ALADTEC_CATEGORY]

    @pytest.mark.asyncio
    async def test_no_category_for_separate_calendar_user(self, sync, sample_entry):
        """Events for separate calendar users don't have categories."""
        # User NOT in _uses_primary_calendar

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.post = AsyncMock()

        await sync.create_event("other@example.com", "calendar-id", sample_entry)

        # Check that post was called with event without categories
        call_args = sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.post.call_args
        event = call_args[0][0]
        assert event.categories is None


class TestGetExistingEventsWithFilter:
    """Tests for get_existing_events filtering by category."""

    @pytest.fixture
    def sync(self, mock_env_vars):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.mark.asyncio
    async def test_filters_by_category_for_primary_calendar_user(self, sync):
        """Primary calendar users get events filtered by Aladtec category."""
        sync._uses_primary_calendar.add("test@example.com")

        mock_result = MagicMock()
        mock_result.value = []

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get = AsyncMock(
            return_value=mock_result
        )

        await sync.get_existing_events(
            "test@example.com", "calendar-id", date(2026, 2, 1), date(2026, 2, 28)
        )

        # Check that filter was applied
        call_args = sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get.call_args
        config = call_args[1]["request_configuration"]
        assert f"categories/any(c:c eq '{ALADTEC_CATEGORY}')" in str(config.query_parameters.filter)

    @pytest.mark.asyncio
    async def test_no_filter_for_separate_calendar_user(self, sync):
        """Separate calendar users get all events (no filter)."""
        # User NOT in _uses_primary_calendar

        mock_result = MagicMock()
        mock_result.value = []

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get = AsyncMock(
            return_value=mock_result
        )

        await sync.get_existing_events(
            "other@example.com", "calendar-id", date(2026, 2, 1), date(2026, 2, 28)
        )

        # Check that no filter was applied
        call_args = sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get.call_args
        config = call_args[1]["request_configuration"]
        assert config.query_parameters.filter is None


class TestUpdateEventWithCategory:
    """Tests for update_event adding category for primary calendar users."""

    @pytest.fixture
    def sync(self, mock_env_vars):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_entry(self):
        """Create sample schedule entry."""
        return ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Captain",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )

    @pytest.mark.asyncio
    async def test_adds_category_on_update_for_primary_user(self, sync, sample_entry):
        """Updated events for primary calendar users include Aladtec category."""
        sync._uses_primary_calendar.add("test@example.com")

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.by_event_id.return_value.patch = AsyncMock()

        await sync.update_event("test@example.com", "calendar-id", "event-id", sample_entry)

        # Check that patch was called with event containing categories
        call_args = sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.by_event_id.return_value.patch.call_args
        event = call_args[0][0]
        assert event.categories == [ALADTEC_CATEGORY]


# =============================================================================
# Purge Feature Tests
# =============================================================================


class TestGetAladtecCategoryEvents:
    """Tests for get_aladtec_category_events method."""

    @pytest.fixture
    def sync(self, mock_env_vars):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.mark.asyncio
    async def test_returns_events_with_aladtec_category(self, sync):
        """Returns list of events with Aladtec category."""
        mock_event1 = MagicMock()
        mock_event1.id = "event-1"
        mock_event1.subject = "S31 - Captain"
        mock_event1.start = MagicMock()
        mock_event1.start.date_time = "2026-02-15T18:00:00"

        mock_event2 = MagicMock()
        mock_event2.id = "event-2"
        mock_event2.subject = "Backup Duty - Officer"
        mock_event2.start = MagicMock()
        mock_event2.start.date_time = "2026-02-16T08:00:00"

        mock_result = MagicMock()
        mock_result.value = [mock_event1, mock_event2]

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get = AsyncMock(
            return_value=mock_result
        )

        events = await sync.get_aladtec_category_events("test@example.com", "calendar-id")

        assert len(events) == 2
        assert events[0] == ("event-1", "S31 - Captain", "2026-02-15")
        assert events[1] == ("event-2", "Backup Duty - Officer", "2026-02-16")

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_events(self, sync):
        """Returns empty list when no events found."""
        mock_result = MagicMock()
        mock_result.value = []

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get = AsyncMock(
            return_value=mock_result
        )

        events = await sync.get_aladtec_category_events("test@example.com", "calendar-id")

        assert events == []

    @pytest.mark.asyncio
    async def test_handles_api_error(self, sync):
        """Returns empty list on API error."""
        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get = AsyncMock(
            side_effect=Exception("API Error")
        )

        events = await sync.get_aladtec_category_events("test@example.com", "calendar-id")

        assert events == []


class TestPurgeAladtecEvents:
    """Tests for purge_aladtec_events method."""

    @pytest.fixture
    def sync(self, mock_env_vars):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.mark.asyncio
    async def test_dry_run_returns_count_without_deleting(self, sync):
        """Dry run returns count but doesn't delete events."""
        # Mock getting primary calendar
        mock_calendar = MagicMock()
        mock_calendar.id = "primary-calendar-id"
        sync.client.users.by_user_id.return_value.calendar.get = AsyncMock(
            return_value=mock_calendar
        )

        # Mock getting events
        mock_event = MagicMock()
        mock_event.id = "event-1"
        mock_event.subject = "S31 - Captain"
        mock_event.start = MagicMock()
        mock_event.start.date_time = "2026-02-15T18:00:00"

        mock_result = MagicMock()
        mock_result.value = [mock_event]

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get = AsyncMock(
            return_value=mock_result
        )

        # Mock delete (should NOT be called)
        delete_mock = AsyncMock()
        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.by_event_id.return_value.delete = delete_mock

        deleted, errors = await sync.purge_aladtec_events("test@example.com", dry_run=True)

        assert deleted == 1
        assert errors == 0
        delete_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_deletes_events_when_not_dry_run(self, sync):
        """Actually deletes events when not in dry run mode."""
        # Mock getting primary calendar
        mock_calendar = MagicMock()
        mock_calendar.id = "primary-calendar-id"
        sync.client.users.by_user_id.return_value.calendar.get = AsyncMock(
            return_value=mock_calendar
        )

        # Mock getting events
        mock_event1 = MagicMock()
        mock_event1.id = "event-1"
        mock_event1.subject = "S31 - Captain"
        mock_event1.start = MagicMock()
        mock_event1.start.date_time = "2026-02-15T18:00:00"

        mock_event2 = MagicMock()
        mock_event2.id = "event-2"
        mock_event2.subject = "Backup Duty"
        mock_event2.start = MagicMock()
        mock_event2.start.date_time = "2026-02-16T08:00:00"

        mock_result = MagicMock()
        mock_result.value = [mock_event1, mock_event2]

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get = AsyncMock(
            return_value=mock_result
        )

        # Mock delete
        delete_mock = AsyncMock()
        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.by_event_id.return_value.delete = delete_mock

        deleted, errors = await sync.purge_aladtec_events("test@example.com", dry_run=False)

        assert deleted == 2
        assert errors == 0
        assert delete_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_events(self, sync):
        """Returns zero counts when no events to delete."""
        # Mock getting primary calendar
        mock_calendar = MagicMock()
        mock_calendar.id = "primary-calendar-id"
        sync.client.users.by_user_id.return_value.calendar.get = AsyncMock(
            return_value=mock_calendar
        )

        # Mock no events
        mock_result = MagicMock()
        mock_result.value = []

        sync.client.users.by_user_id.return_value.calendars.by_calendar_id.return_value.events.get = AsyncMock(
            return_value=mock_result
        )

        deleted, errors = await sync.purge_aladtec_events("test@example.com", dry_run=False)

        assert deleted == 0
        assert errors == 0

    @pytest.mark.asyncio
    async def test_returns_error_when_calendar_not_found(self, sync):
        """Returns error when primary calendar cannot be retrieved."""
        sync.client.users.by_user_id.return_value.calendar.get = AsyncMock(return_value=None)

        deleted, errors = await sync.purge_aladtec_events("test@example.com", dry_run=False)

        assert deleted == 0
        assert errors == 1


# =============================================================================
# sync_user Logic Tests
# =============================================================================


class TestSyncUserLogic:
    """Tests for sync_user create/update/delete logic."""

    @pytest.fixture
    def mock_env_vars(self):
        """Mock environment variables for Graph API credentials."""
        with patch.dict(
            "os.environ",
            {
                "MS_GRAPH_TENANT_ID": "test-tenant",
                "MS_GRAPH_CLIENT_ID": "test-client",
                "MS_GRAPH_CLIENT_SECRET": "test-secret",
            },
        ):
            yield

    @pytest.fixture
    def mock_aladtec_url(self):
        """Mock get_aladtec_url for make_event_body calls."""
        with patch(
            "sjifire.calendar.personal_sync.get_aladtec_url",
            return_value="https://aladtec.example.com",
        ):
            yield

    @pytest.fixture
    def sync(self, mock_env_vars, mock_aladtec_url):
        """Create PersonalCalendarSync with mocked client."""
        with (
            patch("sjifire.calendar.personal_sync.ClientSecretCredential"),
            patch("sjifire.calendar.personal_sync.GraphServiceClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            sync = PersonalCalendarSync()
            sync.client = mock_client
            return sync

    @pytest.fixture
    def sample_entry(self):
        """Sample schedule entry for testing."""
        return ScheduleEntry(
            date=date(2026, 2, 15),
            section="S31",
            position="Captain",
            name="John Doe",
            start_time="08:00",
            end_time="18:00",
        )

    def _setup_sync_mocks(self, sync, existing_events: dict[str, ExistingEvent] | None = None):
        """Set up common mocks for sync_user tests."""
        # Mock calendar retrieval
        mock_calendar = MagicMock()
        mock_calendar.id = "calendar-123"
        sync.client.users.by_user_id.return_value.calendar.get = AsyncMock(
            return_value=mock_calendar
        )

        # Mark as using primary calendar
        sync._uses_primary_calendar.add("test@example.com")

        # Mock ensure_aladtec_category
        sync.client.users.by_user_id.return_value.outlook.master_categories.get = AsyncMock(
            return_value=MagicMock(value=[MagicMock(display_name=ALADTEC_CATEGORY)])
        )

        # Mock get_existing_events by patching the method
        if existing_events is None:
            existing_events = {}
        sync.get_existing_events = AsyncMock(return_value=existing_events)

        # Mock create/update/delete
        sync.create_event = AsyncMock(return_value=True)
        sync.update_event = AsyncMock(return_value=True)
        sync.delete_event = AsyncMock(return_value=True)

    @pytest.mark.asyncio
    async def test_skips_existing_event_with_matching_key(self, sync, sample_entry):
        """Event already in calendar with same key and body is not recreated (idempotent)."""
        # Create existing event with matching key and body
        subject = make_event_subject(sample_entry)
        body = make_event_body(sample_entry)
        key = f"{sample_entry.date}|{subject}|08:00|18:00"
        existing = {key: ExistingEvent(event_id="existing-event-id", body=body)}

        self._setup_sync_mocks(sync, existing)

        result = await sync.sync_user(
            "test@example.com",
            [sample_entry],
            date(2026, 2, 1),
            date(2026, 2, 28),
            dry_run=False,
        )

        # Should not create, update, or delete anything
        assert result.events_created == 0
        assert result.events_updated == 0
        assert result.events_deleted == 0
        sync.create_event.assert_not_called()
        sync.update_event.assert_not_called()
        sync.delete_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_deletes_event_no_longer_in_schedule(self, sync):
        """Event in calendar but removed from schedule gets deleted (trade scenario)."""
        # Existing event for Feb 15 shift that was traded away
        key = "2026-02-15|S31 - Captain|08:00|18:00"
        existing = {key: ExistingEvent(event_id="old-event-id", body="Old shift body")}

        self._setup_sync_mocks(sync, existing)

        # Sync with empty schedule (user traded their shift)
        result = await sync.sync_user(
            "test@example.com",
            [],  # No entries - shift was traded away
            date(2026, 2, 1),
            date(2026, 2, 28),
            dry_run=False,
        )

        # Should delete the old event
        assert result.events_deleted == 1
        assert result.events_created == 0
        sync.delete_event.assert_called_once_with(
            "test@example.com", "calendar-123", "old-event-id"
        )

    @pytest.mark.asyncio
    async def test_creates_event_not_in_calendar(self, sync, sample_entry):
        """New schedule entry creates calendar event."""
        # No existing events
        self._setup_sync_mocks(sync, existing_events={})

        result = await sync.sync_user(
            "test@example.com",
            [sample_entry],
            date(2026, 2, 1),
            date(2026, 2, 28),
            dry_run=False,
        )

        # Should create the new event
        assert result.events_created == 1
        assert result.events_deleted == 0
        sync.create_event.assert_called_once()
        call_args = sync.create_event.call_args
        assert call_args[0][0] == "test@example.com"
        assert call_args[0][1] == "calendar-123"
        assert call_args[0][2] == sample_entry

    @pytest.mark.asyncio
    async def test_updates_event_when_body_changes(self, sync, sample_entry):
        """Existing event with changed body gets updated."""
        # Create existing event with matching key but different body
        subject = make_event_subject(sample_entry)
        key = f"{sample_entry.date}|{subject}|08:00|18:00"
        existing = {key: ExistingEvent(event_id="existing-event-id", body="Old body content")}

        self._setup_sync_mocks(sync, existing)

        result = await sync.sync_user(
            "test@example.com",
            [sample_entry],
            date(2026, 2, 1),
            date(2026, 2, 28),
            dry_run=False,
        )

        # Should update the event (body changed)
        assert result.events_updated == 1
        assert result.events_created == 0
        assert result.events_deleted == 0
        sync.update_event.assert_called_once()
        call_args = sync.update_event.call_args
        assert call_args[0][0] == "test@example.com"
        assert call_args[0][1] == "calendar-123"
        assert call_args[0][2] == "existing-event-id"
        assert call_args[0][3] == sample_entry

    @pytest.mark.asyncio
    async def test_trade_scenario_delete_old_create_new(self, sync):
        """Trade scenario: old shift deleted, new shift on different date created."""
        # Old shift on Feb 15 (traded away)
        old_key = "2026-02-15|S31 - Captain|08:00|18:00"
        existing = {old_key: ExistingEvent(event_id="old-event-id", body="Old shift")}

        self._setup_sync_mocks(sync, existing)

        # New shift on Feb 20 (received from trade)
        new_entry = ScheduleEntry(
            date=date(2026, 2, 20),
            section="S32",
            position="Firefighter",
            name="John Doe",
            start_time="08:00",
            end_time="18:00",
        )

        result = await sync.sync_user(
            "test@example.com",
            [new_entry],  # Only the new shift, old one is gone
            date(2026, 2, 1),
            date(2026, 2, 28),
            dry_run=False,
        )

        # Should delete old and create new
        assert result.events_deleted == 1
        assert result.events_created == 1
        sync.delete_event.assert_called_once_with(
            "test@example.com", "calendar-123", "old-event-id"
        )
        sync.create_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_updates_even_when_body_matches(self, sync, sample_entry):
        """Force flag updates events even when body hasn't changed."""
        # Create existing event with matching key and body
        subject = make_event_subject(sample_entry)
        body = make_event_body(sample_entry)
        key = f"{sample_entry.date}|{subject}|08:00|18:00"
        existing = {key: ExistingEvent(event_id="existing-event-id", body=body)}

        self._setup_sync_mocks(sync, existing)

        result = await sync.sync_user(
            "test@example.com",
            [sample_entry],
            date(2026, 2, 1),
            date(2026, 2, 28),
            dry_run=False,
            force=True,  # Force update
        )

        # Should update even though body matches
        assert result.events_updated == 1
        assert result.events_created == 0
        sync.update_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_dry_run_reports_changes_without_api_calls(self, sync, sample_entry):
        """Dry run reports what would happen without making API calls."""
        # Existing event to delete
        old_key = "2026-02-10|S31 - Lieutenant|08:00|18:00"
        existing = {old_key: ExistingEvent(event_id="old-event-id", body="Old shift")}

        self._setup_sync_mocks(sync, existing)

        result = await sync.sync_user(
            "test@example.com",
            [sample_entry],  # New entry to create
            date(2026, 2, 1),
            date(2026, 2, 28),
            dry_run=True,
        )

        # Should report changes
        assert result.events_created == 1
        assert result.events_deleted == 1

        # But no API calls should be made
        sync.create_event.assert_not_called()
        sync.delete_event.assert_not_called()
        sync.update_event.assert_not_called()
