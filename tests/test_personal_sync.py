"""Tests for sjifire.calendar.personal_sync module."""

from datetime import date
from unittest.mock import patch

from sjifire.aladtec.schedule_scraper import ScheduleEntry
from sjifire.calendar.personal_sync import (
    CALENDAR_ID_ATTRIBUTE,
    CALENDAR_NAME,
    PersonalSyncResult,
    make_event_body,
    make_event_subject,
)


class TestMakeEventSubject:
    """Tests for make_event_subject function."""

    def test_creates_subject_from_section_and_position(self):
        """Subject format is 'Section - Position'."""
        entry = ScheduleEntry(
            date=date(2026, 2, 18),
            section="Backup Duty",
            position="Backup Duty Officer",
            name="Greene, Adam",
            start_time="18:00",
            end_time="18:00",
        )
        assert make_event_subject(entry) == "Backup Duty - Backup Duty Officer"

    def test_handles_station_section(self):
        """Station sections work correctly."""
        entry = ScheduleEntry(
            date=date(2026, 2, 18),
            section="S31",
            position="Firefighter",
            name="Smith, John",
            start_time="18:00",
            end_time="18:00",
        )
        assert make_event_subject(entry) == "S31 - Firefighter"


class TestMakeEventBody:
    """Tests for make_event_body function."""

    @patch("sjifire.calendar.personal_sync.get_aladtec_url")
    def test_includes_position_and_section(self, mock_url):
        """Body includes position and section."""
        mock_url.return_value = "https://aladtec.example.com"
        entry = ScheduleEntry(
            date=date(2026, 2, 18),
            section="Backup Duty",
            position="Backup Duty Officer",
            name="Greene, Adam",
            start_time="18:00",
            end_time="18:00",
        )
        body = make_event_body(entry)
        assert "Position: Backup Duty Officer" in body
        assert "Section: Backup Duty" in body

    @patch("sjifire.calendar.personal_sync.get_aladtec_url")
    def test_includes_aladtec_url(self, mock_url):
        """Body includes Aladtec URL."""
        mock_url.return_value = "https://secure17.aladtec.com/sjifire"
        entry = ScheduleEntry(
            date=date(2026, 2, 18),
            section="S31",
            position="Firefighter",
            name="Smith, John",
            start_time="18:00",
            end_time="18:00",
        )
        body = make_event_body(entry)
        assert "https://secure17.aladtec.com/sjifire" in body

    @patch("sjifire.calendar.personal_sync.get_aladtec_url")
    def test_includes_warning_message(self, mock_url):
        """Body includes warning about automatic sync."""
        mock_url.return_value = "https://aladtec.example.com"
        entry = ScheduleEntry(
            date=date(2026, 2, 18),
            section="S31",
            position="Firefighter",
            name="Smith, John",
            start_time="18:00",
            end_time="18:00",
        )
        body = make_event_body(entry)
        assert "imported automatically from Aladtec" in body
        assert "changes will be overwritten" in body


class TestPersonalSyncResult:
    """Tests for PersonalSyncResult dataclass."""

    def test_str_with_no_changes(self):
        """Shows 'no changes' when nothing happened."""
        result = PersonalSyncResult(user="test@example.com")
        assert str(result) == "test@example.com: no changes"

    def test_str_with_created(self):
        """Shows created count."""
        result = PersonalSyncResult(user="test@example.com", events_created=5)
        assert str(result) == "test@example.com: 5 created"

    def test_str_with_deleted(self):
        """Shows deleted count."""
        result = PersonalSyncResult(user="test@example.com", events_deleted=3)
        assert str(result) == "test@example.com: 3 deleted"

    def test_str_with_updated(self):
        """Shows updated count."""
        result = PersonalSyncResult(user="test@example.com", events_updated=2)
        assert str(result) == "test@example.com: 2 updated"

    def test_str_with_errors(self):
        """Shows error count."""
        result = PersonalSyncResult(user="test@example.com", errors=["error1", "error2"])
        assert str(result) == "test@example.com: 2 errors"

    def test_str_with_multiple_changes(self):
        """Shows multiple change types."""
        result = PersonalSyncResult(
            user="test@example.com",
            events_created=3,
            events_deleted=2,
            errors=["error"],
        )
        assert str(result) == "test@example.com: 3 created, 2 deleted, 1 errors"


class TestPersonalCalendarSyncInit:
    """Tests for PersonalCalendarSync initialization."""

    @patch("sjifire.calendar.personal_sync.get_graph_credentials")
    @patch("sjifire.calendar.personal_sync.ClientSecretCredential")
    @patch("sjifire.calendar.personal_sync.GraphServiceClient")
    def test_initializes_with_credentials(self, mock_client, mock_cred, mock_get_creds):
        """Initializes Graph client with credentials."""
        mock_get_creds.return_value = ("tenant", "client", "secret")

        from sjifire.calendar.personal_sync import PersonalCalendarSync

        sync = PersonalCalendarSync()

        mock_get_creds.assert_called_once()
        mock_cred.assert_called_once_with(
            tenant_id="tenant", client_id="client", client_secret="secret"
        )
        assert sync._calendar_cache == {}


class TestConstants:
    """Tests for module constants."""

    def test_calendar_name(self):
        """Calendar name is set correctly."""
        assert CALENDAR_NAME == "Aladtec Schedule"

    def test_calendar_id_attribute(self):
        """Extension attribute name uses snake_case for SDK."""
        assert CALENDAR_ID_ATTRIBUTE == "extension_attribute5"
