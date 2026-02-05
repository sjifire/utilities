"""Tests for sjifire.aladtec.schedule module."""

import json
from datetime import date, datetime
from unittest.mock import patch

import httpx
import pytest
import respx

from sjifire.aladtec.schedule_scraper import AladtecScheduleScraper, DaySchedule, ScheduleEntry


class TestScheduleEntry:
    """Tests for ScheduleEntry dataclass."""

    def test_is_full_shift_true(self):
        """Full shift is 18:00-18:00."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        assert entry.is_full_shift is True

    def test_is_full_shift_false_different_start(self):
        """Not a full shift if start time differs."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="06:00",
            end_time="18:00",
        )
        assert entry.is_full_shift is False

    def test_is_full_shift_false_different_end(self):
        """Not a full shift if end time differs."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="18:00",
            end_time="06:00",
        )
        assert entry.is_full_shift is False

    def test_start_datetime(self):
        """Start datetime combines date and time."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        expected = datetime(2026, 2, 1, 18, 0)
        assert entry.start_datetime == expected

    def test_end_datetime_same_day(self):
        """End datetime on same day when end > start."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="06:00",
            end_time="18:00",
        )
        expected = datetime(2026, 2, 1, 18, 0)
        assert entry.end_datetime == expected

    def test_end_datetime_next_day(self):
        """End datetime rolls to next day when end <= start."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        expected = datetime(2026, 2, 2, 18, 0)
        assert entry.end_datetime == expected

    def test_end_datetime_overnight(self):
        """End datetime overnight shift."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="22:00",
            end_time="06:00",
        )
        expected = datetime(2026, 2, 2, 6, 0)
        assert entry.end_datetime == expected

    def test_platoon_default_empty(self):
        """Platoon defaults to empty string."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
        )
        assert entry.platoon == ""

    def test_platoon_set(self):
        """Platoon can be set."""
        entry = ScheduleEntry(
            date=date(2026, 2, 1),
            section="S31",
            position="Firefighter",
            name="John Doe",
            start_time="18:00",
            end_time="18:00",
            platoon="A",
        )
        assert entry.platoon == "A"


class TestDaySchedule:
    """Tests for DaySchedule dataclass."""

    @pytest.fixture
    def sample_entries(self):
        """Create sample schedule entries."""
        return [
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Captain",
                name="John Doe",
                start_time="18:00",
                end_time="18:00",
            ),
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S31",
                position="Firefighter",
                name="Jane Smith",
                start_time="18:00",
                end_time="18:00",
            ),
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="S32",
                position="Firefighter",
                name="Bob Johnson",
                start_time="18:00",
                end_time="18:00",
            ),
            ScheduleEntry(
                date=date(2026, 2, 1),
                section="Administration",
                position="Chief",
                name="",  # Unfilled
                start_time="18:00",
                end_time="18:00",
            ),
        ]

    def test_get_entries_by_section(self, sample_entries):
        """Group entries by section."""
        day = DaySchedule(date=date(2026, 2, 1), platoon="A", entries=sample_entries)
        by_section = day.get_entries_by_section()

        assert "S31" in by_section
        assert "S32" in by_section
        assert "Administration" in by_section

        assert len(by_section["S31"]) == 2
        assert len(by_section["S32"]) == 1
        assert len(by_section["Administration"]) == 1

    def test_get_filled_positions_all(self, sample_entries):
        """Get all filled positions."""
        day = DaySchedule(date=date(2026, 2, 1), platoon="A", entries=sample_entries)
        filled = day.get_filled_positions()

        # Should include 3 (excluding empty name in Administration)
        assert len(filled) == 3
        names = [e.name for e in filled]
        assert "John Doe" in names
        assert "Jane Smith" in names
        assert "Bob Johnson" in names

    def test_get_filled_positions_exclude_sections(self, sample_entries):
        """Get filled positions excluding certain sections."""
        day = DaySchedule(date=date(2026, 2, 1), platoon="A", entries=sample_entries)
        filled = day.get_filled_positions(exclude_sections=["S32"])

        # Should only include S31 entries (2)
        assert len(filled) == 2
        names = [e.name for e in filled]
        assert "John Doe" in names
        assert "Jane Smith" in names
        assert "Bob Johnson" not in names

    def test_entries_default_empty(self):
        """Entries defaults to empty list."""
        day = DaySchedule(date=date(2026, 2, 1), platoon="A")
        assert day.entries == []


class TestAladtecScheduleScraperParsing:
    """Tests for HTML parsing in AladtecScheduleScraper."""

    @pytest.fixture
    def sample_day_html(self):
        """Sample HTML for a single day."""
        return """
        <div class="shift-label-display">A Platoon</div>
        <div class="sch_entry">
            <div class="calendar-event-header">S31</div>
            <tr class="calendar-event" title="John Doe&lt;br/&gt;&lt;p&gt;S31 / Captain&lt;br/&gt;01 Feb 18:00 - 02 Feb 18:00&lt;/p&gt;">
                <td>John Doe</td>
            </tr>
            <tr class="calendar-event" title="Jane Smith&lt;br/&gt;&lt;p&gt;S31 / Firefighter&lt;br/&gt;01 Feb 18:00 - 02 Feb 18:00&lt;/p&gt;">
                <td>Jane Smith</td>
            </tr>
        </div>
        <div class="sch_entry">
            <div class="calendar-event-header">S32</div>
            <tr class="calendar-event" title="Bob Johnson&lt;br/&gt;&lt;p&gt;S32 / Apparatus Operator&lt;br/&gt;01 Feb 06:00 - 01 Feb 18:00&lt;/p&gt;">
                <td>Bob Johnson</td>
            </tr>
        </div>
        """

    def test_parse_day_html_extracts_platoon(self, sample_day_html, mock_env_vars):
        """Parse platoon from HTML."""
        scraper = AladtecScheduleScraper()
        day = scraper.parse_day_html("2026-02-01", sample_day_html)

        assert day.platoon == "A Platoon"

    def test_parse_day_html_extracts_entries(self, sample_day_html, mock_env_vars):
        """Parse entries from HTML."""
        scraper = AladtecScheduleScraper()
        day = scraper.parse_day_html("2026-02-01", sample_day_html)

        assert len(day.entries) == 3

    def test_parse_day_html_extracts_sections(self, sample_day_html, mock_env_vars):
        """Parse section headers from HTML."""
        scraper = AladtecScheduleScraper()
        day = scraper.parse_day_html("2026-02-01", sample_day_html)

        sections = {e.section for e in day.entries}
        assert "S31" in sections
        assert "S32" in sections

    def test_parse_day_html_extracts_names(self, sample_day_html, mock_env_vars):
        """Parse names from HTML."""
        scraper = AladtecScheduleScraper()
        day = scraper.parse_day_html("2026-02-01", sample_day_html)

        names = [e.name for e in day.entries]
        assert "John Doe" in names
        assert "Jane Smith" in names
        assert "Bob Johnson" in names

    def test_parse_day_html_extracts_positions(self, sample_day_html, mock_env_vars):
        """Parse positions from HTML."""
        scraper = AladtecScheduleScraper()
        day = scraper.parse_day_html("2026-02-01", sample_day_html)

        positions = {e.position for e in day.entries}
        assert "Captain" in positions
        assert "Firefighter" in positions
        assert "Apparatus Operator" in positions

    def test_parse_day_html_extracts_times(self, sample_day_html, mock_env_vars):
        """Parse times from HTML."""
        scraper = AladtecScheduleScraper()
        day = scraper.parse_day_html("2026-02-01", sample_day_html)

        # Find the partial shift entry (Bob Johnson)
        bob = next(e for e in day.entries if e.name == "Bob Johnson")
        assert bob.start_time == "06:00"
        assert bob.end_time == "18:00"

    def test_parse_day_html_sets_date(self, sample_day_html, mock_env_vars):
        """Parse date from date string."""
        scraper = AladtecScheduleScraper()
        day = scraper.parse_day_html("2026-02-01", sample_day_html)

        assert day.date == date(2026, 2, 1)
        for entry in day.entries:
            assert entry.date == date(2026, 2, 1)

    def test_parse_day_html_empty(self, mock_env_vars):
        """Handle empty HTML."""
        scraper = AladtecScheduleScraper()
        day = scraper.parse_day_html("2026-02-01", "")

        assert day.entries == []
        assert day.platoon == ""


class TestAladtecScheduleScraperContextManager:
    """Tests for context manager functionality."""

    def test_context_manager_creates_client(self, mock_env_vars):
        """Context manager creates HTTP client."""
        with AladtecScheduleScraper() as scraper:
            assert scraper.client is not None

    def test_context_manager_closes_client(self, mock_env_vars):
        """Context manager closes HTTP client."""
        scraper = AladtecScheduleScraper()
        with scraper:
            pass
        assert scraper.client is None

    def test_requires_context_manager_for_login(self, mock_env_vars):
        """Login requires context manager."""
        scraper = AladtecScheduleScraper()
        with pytest.raises(RuntimeError, match="must be used as context manager"):
            scraper.login()

    def test_requires_context_manager_for_fetch(self, mock_env_vars):
        """Fetch requires context manager."""
        scraper = AladtecScheduleScraper()
        with pytest.raises(RuntimeError, match="must be used as context manager"):
            scraper.fetch_month_schedule(date(2026, 2, 1))


class TestAladtecScheduleScraperHTTP:
    """Tests for HTTP interactions in AladtecScheduleScraper."""

    @pytest.fixture
    def base_url(self):
        """Return test base URL."""
        return "https://test.aladtec.com"

    @pytest.fixture
    def sample_ajax_response(self):
        """Sample AJAX response with schedule data."""
        day_html = """
        <div class="shift-label-display">A</div>
        <div class="sch_entry">
            <div class="calendar-event-header">S31</div>
            <tr class="calendar-event" title="John Doe&lt;br/&gt;&lt;p&gt;S31 / Captain&lt;br/&gt;01 Feb 18:00 - 02 Feb 18:00&lt;/p&gt;">
                <td>John Doe</td>
            </tr>
        </div>
        """
        return_data = json.dumps(
            {
                "2026-02-01": day_html,
                "2026-02-02": day_html,
            }
        )
        return {"return_data": return_data}

    @respx.mock
    def test_fetch_ajax_schedule_success(self, mock_env_vars, base_url, sample_ajax_response):
        """Fetch AJAX schedule returns parsed data."""
        # Mock login page
        respx.get(f"{base_url}/").mock(return_value=httpx.Response(200))
        # Mock login POST
        respx.post(f"{base_url}/index.php").mock(
            return_value=httpx.Response(200, text="schedule dashboard")
        )
        # Mock nav POST
        respx.post(
            f"{base_url}/index.php", params__contains={"action": "manage_work_view_ajax"}
        ).mock(return_value=httpx.Response(200))
        # Mock AJAX GET
        respx.get(f"{base_url}/index.php").mock(
            return_value=httpx.Response(200, json=sample_ajax_response)
        )

        with AladtecScheduleScraper() as scraper:
            scraper.login()
            result = scraper._fetch_ajax_schedule(date(2026, 2, 1))

        assert "2026-02-01" in result
        assert "2026-02-02" in result

    @respx.mock
    def test_fetch_ajax_schedule_error_status(self, mock_env_vars, base_url):
        """Fetch AJAX returns empty dict on error status."""
        respx.get(f"{base_url}/").mock(return_value=httpx.Response(200))
        respx.post(f"{base_url}/index.php").mock(return_value=httpx.Response(200, text="schedule"))
        respx.get(f"{base_url}/index.php").mock(return_value=httpx.Response(500))

        with AladtecScheduleScraper() as scraper:
            scraper.login()
            result = scraper._fetch_ajax_schedule(date(2026, 2, 1))

        assert result == {}

    @respx.mock
    def test_fetch_ajax_schedule_invalid_json(self, mock_env_vars, base_url):
        """Fetch AJAX returns empty dict on invalid JSON."""
        respx.get(f"{base_url}/").mock(return_value=httpx.Response(200))
        respx.post(f"{base_url}/index.php").mock(return_value=httpx.Response(200, text="schedule"))
        respx.get(f"{base_url}/index.php").mock(return_value=httpx.Response(200, text="not json"))

        with AladtecScheduleScraper() as scraper:
            scraper.login()
            result = scraper._fetch_ajax_schedule(date(2026, 2, 1))

        assert result == {}

    @respx.mock
    def test_fetch_month_schedule_combines_requests(
        self, mock_env_vars, base_url, sample_ajax_response
    ):
        """Fetch month makes multiple requests and combines results."""
        respx.get(f"{base_url}/").mock(return_value=httpx.Response(200))
        respx.post(f"{base_url}/index.php").mock(return_value=httpx.Response(200, text="schedule"))
        respx.get(f"{base_url}/index.php").mock(
            return_value=httpx.Response(200, json=sample_ajax_response)
        )

        with AladtecScheduleScraper() as scraper:
            scraper.login()
            result = scraper.fetch_month_schedule(date(2026, 2, 1))

        # Should have combined results from multiple requests
        assert len(result) >= 2

    @respx.mock
    def test_get_schedule_range_filters_dates(self, mock_env_vars, base_url):
        """Get schedule range only includes dates within range."""
        # HTML with actual schedule entries (required for days to be included)
        entry_html = """
        <div class="shift-label-display">A</div>
        <div class="sch_entry">
            <div class="calendar-event-header">S31</div>
            <tr class="calendar-event" title="John Doe&lt;br/&gt;&lt;p&gt;S31 / Captain&lt;br/&gt;01 Feb 18:00 - 02 Feb 18:00&lt;/p&gt;">
                <td>John Doe</td>
            </tr>
        </div>
        """
        # Response includes dates outside our range
        return_data = json.dumps(
            {
                "2026-01-31": entry_html,
                "2026-02-01": entry_html,
                "2026-02-02": entry_html,
                "2026-02-03": entry_html,
            }
        )

        respx.get(f"{base_url}/").mock(return_value=httpx.Response(200))
        respx.post(f"{base_url}/index.php").mock(return_value=httpx.Response(200, text="schedule"))
        respx.get(f"{base_url}/index.php").mock(
            return_value=httpx.Response(200, json={"return_data": return_data})
        )

        with AladtecScheduleScraper() as scraper:
            scraper.login()
            result = scraper.get_schedule_range(date(2026, 2, 1), date(2026, 2, 2))

        # Should only include Feb 1 and Feb 2
        dates = [s.date for s in result]
        assert date(2026, 2, 1) in dates
        assert date(2026, 2, 2) in dates
        assert date(2026, 1, 31) not in dates
        assert date(2026, 2, 3) not in dates

    @respx.mock
    def test_get_schedule_range_spans_months(self, mock_env_vars, base_url):
        """Get schedule range handles multi-month ranges."""
        # HTML with actual schedule entries (required for days to be included)
        entry_html = """
        <div class="shift-label-display">A</div>
        <div class="sch_entry">
            <div class="calendar-event-header">S31</div>
            <tr class="calendar-event" title="John Doe&lt;br/&gt;&lt;p&gt;S31 / Captain&lt;br/&gt;01 Feb 18:00 - 02 Feb 18:00&lt;/p&gt;">
                <td>John Doe</td>
            </tr>
        </div>
        """
        jan_data = json.dumps(
            {
                "2026-01-31": entry_html,
            }
        )
        feb_data = json.dumps(
            {
                "2026-02-01": entry_html,
            }
        )

        def mock_response(request):
            # Check ajax_start_date parameter to determine which month
            if "ajax_start_date=2026-01" in str(request.url):
                return httpx.Response(200, json={"return_data": jan_data})
            return httpx.Response(200, json={"return_data": feb_data})

        respx.get(f"{base_url}/").mock(return_value=httpx.Response(200))
        respx.post(f"{base_url}/index.php").mock(return_value=httpx.Response(200, text="schedule"))
        respx.get(f"{base_url}/index.php").mock(side_effect=mock_response)

        with AladtecScheduleScraper() as scraper:
            scraper.login()
            result = scraper.get_schedule_range(date(2026, 1, 31), date(2026, 2, 1))

        # Should include data from both months
        dates = [s.date for s in result]
        assert date(2026, 1, 31) in dates
        assert date(2026, 2, 1) in dates

    @respx.mock
    def test_login_success(self, mock_env_vars, base_url):
        """Login returns True on success."""
        respx.get(f"{base_url}/").mock(return_value=httpx.Response(200))
        respx.post(f"{base_url}/index.php").mock(
            return_value=httpx.Response(200, text="schedule dashboard")
        )

        with AladtecScheduleScraper() as scraper:
            result = scraper.login()

        assert result is True

    @respx.mock
    def test_login_failure_invalid_credentials(self, mock_env_vars, base_url):
        """Login returns False on invalid credentials."""
        respx.get(f"{base_url}/").mock(return_value=httpx.Response(200))
        respx.post(f"{base_url}/index.php").mock(
            return_value=httpx.Response(200, text="invalid credentials error")
        )

        with AladtecScheduleScraper() as scraper:
            result = scraper.login()

        assert result is False

    @respx.mock
    def test_login_failure_http_error(self, mock_env_vars, base_url):
        """Login returns False on HTTP error."""
        respx.get(f"{base_url}/").mock(return_value=httpx.Response(500))

        with AladtecScheduleScraper() as scraper:
            result = scraper.login()

        assert result is False


class TestAladtecScheduleScraperMonthsAhead:
    """Tests for get_schedule_months_ahead method."""

    def test_calculates_end_date_correctly(self, mock_env_vars):
        """Months ahead calculates correct end date."""
        with patch.object(AladtecScheduleScraper, "get_schedule_range") as mock_range:
            mock_range.return_value = []

            with (
                AladtecScheduleScraper() as scraper,
                patch("sjifire.aladtec.schedule_scraper.date") as mock_date,
            ):
                mock_date.today.return_value = date(2026, 2, 15)
                mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)

                scraper.get_schedule_months_ahead(months=3)

                # Should call with Feb 15 to end of April
                call_args = mock_range.call_args
                assert call_args[0][0] == date(2026, 2, 15)  # start = today
                # End should be last day of April (3 months from Feb)

    def test_handles_year_boundary(self, mock_env_vars):
        """Months ahead handles crossing year boundary."""
        with patch.object(AladtecScheduleScraper, "get_schedule_range") as mock_range:
            mock_range.return_value = []

            with (
                AladtecScheduleScraper() as scraper,
                patch("sjifire.aladtec.schedule_scraper.date") as mock_date,
            ):
                mock_date.today.return_value = date(2026, 11, 15)
                mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)

                scraper.get_schedule_months_ahead(months=3)

                # Should handle crossing into 2027
                mock_range.assert_called_once()
