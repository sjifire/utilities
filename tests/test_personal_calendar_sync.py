"""Tests for sjifire.calendar.personal_sync module."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.aladtec.schedule_scraper import ScheduleEntry
from sjifire.calendar.personal_sync import (
    ALADTEC_CATEGORY,
    PersonalCalendarSync,
    make_event_body,
    make_event_subject,
    normalize_body_for_comparison,
)

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
