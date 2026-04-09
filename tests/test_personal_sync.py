"""Tests for sjifire.calendar.personal_sync module."""

from datetime import date
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from sjifire.aladtec.schedule_scraper import ScheduleEntry
from sjifire.calendar.personal_sync import (
    ExistingEvent,
    PersonalSyncResult,
    _parse_graph_datetime,
    make_event_body,
    make_event_subject,
    normalize_body_for_comparison,
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
        assert "automatically imported from Aladtec" in body
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

    @patch("sjifire.calendar.personal_sync.get_graph_client")
    def test_initializes_with_credentials(self, mock_get_client):
        """Initializes Graph client via get_graph_client factory."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        from sjifire.calendar.personal_sync import PersonalCalendarSync

        sync = PersonalCalendarSync()

        mock_get_client.assert_called_once()
        assert sync.client is mock_client
        assert sync._calendar_cache == {}


class TestParseGraphDatetime:
    """Tests for _parse_graph_datetime helper function."""

    @pytest.fixture(autouse=True)
    def mock_local_tz(self):
        """Use US/Pacific as the local timezone for all tests."""
        with patch(
            "sjifire.calendar.personal_sync.get_timezone",
            return_value=ZoneInfo("US/Pacific"),
        ):
            yield

    def test_utc_converts_to_local(self):
        """UTC datetime is converted to configured local timezone."""
        result = _parse_graph_datetime("2026-02-15T08:00:00", "UTC")
        assert result.date() == date(2026, 2, 15)
        assert result.strftime("%H:%M") == "00:00"  # UTC-8
        assert result.tzinfo is not None

    def test_iana_timezone_converts(self):
        """IANA timezone (e.g., America/New_York) is converted to local."""
        result = _parse_graph_datetime("2026-02-15T15:00:00", "America/New_York")
        # 3 PM ET = noon PT (ET is UTC-5, PT is UTC-8)
        assert result.strftime("%H:%M") == "12:00"

    def test_unknown_timezone_falls_back_to_local(self):
        """Unknown timezone falls back to local without crashing."""
        result = _parse_graph_datetime("2026-02-15T18:00:00", "Invalid/Timezone")
        assert result.strftime("%H:%M") == "18:00"
        assert result.tzinfo == ZoneInfo("US/Pacific")

    def test_no_timezone_assumes_local(self):
        """None timezone assumes local timezone."""
        result = _parse_graph_datetime("2026-02-15T18:00:00", None)
        assert result.strftime("%H:%M") == "18:00"
        assert result.tzinfo == ZoneInfo("US/Pacific")

    def test_microseconds_stripped(self):
        """Microseconds in datetime string are stripped cleanly."""
        result = _parse_graph_datetime("2026-02-15T18:00:00.0000000", None)
        assert result.strftime("%H:%M") == "18:00"

    def test_invalid_datetime_raises_valueerror(self):
        """Invalid datetime string raises ValueError."""
        with pytest.raises(ValueError):
            _parse_graph_datetime("not-a-date", None)

    def test_utc_case_insensitive(self):
        """UTC detection is case-insensitive."""
        result = _parse_graph_datetime("2026-02-15T08:00:00", "utc")
        assert result.strftime("%H:%M") == "00:00"  # Same as uppercase UTC


class TestNormalizeBodyForComparison:
    """Tests for normalize_body_for_comparison function."""

    def test_strips_html_tags(self):
        """Removes HTML tags from body."""
        html = "<html><body><p>Hello World</p></body></html>"
        assert normalize_body_for_comparison(html) == "Hello World"

    def test_normalizes_whitespace(self):
        """Collapses multiple spaces and newlines."""
        text = "Hello\n\n  World\t\tTest"
        assert normalize_body_for_comparison(text) == "Hello World Test"

    def test_handles_exchange_html_format(self):
        """Handles Microsoft Exchange HTML conversion format."""
        html = (
            '<html><head><meta http-equiv="Content-Type" content="text/html">\r\n'
            "</head>\r\n<body>\r\n"
            '<div class="PlainText">Position: Captain<br>\r\n'
            "Section: S31</div>\r\n</body></html>"
        )
        result = normalize_body_for_comparison(html)
        assert "Position: Captain" in result
        assert "Section: S31" in result

    def test_plain_text_unchanged(self):
        """Plain text content normalized correctly."""
        text = "Position: Captain\nSection: S31"
        result = normalize_body_for_comparison(text)
        assert result == "Position: Captain Section: S31"

    def test_empty_string(self):
        """Empty string returns empty."""
        assert normalize_body_for_comparison("") == ""


class TestExistingEvent:
    """Tests for ExistingEvent dataclass."""

    def test_creates_with_event_id_and_body(self):
        """Creates ExistingEvent with required fields."""
        event = ExistingEvent(event_id="abc123", body="test body")
        assert event.event_id == "abc123"
        assert event.body == "test body"

    def test_empty_body(self):
        """Handles empty body."""
        event = ExistingEvent(event_id="abc123", body="")
        assert event.body == ""
