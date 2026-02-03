"""Tests for sjifire.calendar.models module."""

from datetime import date

import pytest

from sjifire.calendar.models import (
    AllDayDutyEvent,
    CrewMember,
    SyncResult,
    clean_position,
    position_sort_key,
    section_sort_key,
)


class TestSectionSortKey:
    """Tests for section_sort_key function."""

    def test_s31_first(self):
        """S31 should sort first."""
        assert section_sort_key("S31") < section_sort_key("S32")
        assert section_sort_key("S31") < section_sort_key("S33")
        assert section_sort_key("S31") < section_sort_key("Chief Officer")

    def test_stations_before_chief(self):
        """Stations should sort before Chief Officer."""
        assert section_sort_key("S32") < section_sort_key("Chief Officer")
        assert section_sort_key("S33") < section_sort_key("Chief Officer")

    def test_chief_before_backup(self):
        """Chief Officer should sort before Backup Duty."""
        assert section_sort_key("Chief Officer") < section_sort_key("Backup Duty")

    def test_backup_before_standby(self):
        """Backup Duty should sort before Standby."""
        assert section_sort_key("Backup Duty") < section_sort_key("S31 Standby")

    def test_standby_before_marine(self):
        """Standby should sort before Marine."""
        assert section_sort_key("S31 Standby") < section_sort_key("FB31 Marine")

    def test_marine_before_support(self):
        """Marine should sort before Support."""
        assert section_sort_key("FB31 Marine") < section_sort_key("Support")

    def test_station_standby_not_with_stations(self):
        """Station Standby should sort with Standby, not stations."""
        assert section_sort_key("S31 Standby") > section_sort_key("S32")
        assert section_sort_key("S32 Standby") > section_sort_key("S33")


class TestPositionSortKey:
    """Tests for position_sort_key function."""

    def test_chief_first(self):
        """Chief should sort first."""
        assert position_sort_key("Chief") < position_sort_key("Captain")

    def test_captain_before_lieutenant(self):
        """Captain should sort before Lieutenant."""
        assert position_sort_key("Captain") < position_sort_key("Lieutenant")

    def test_lieutenant_before_ao(self):
        """Lieutenant should sort before Apparatus Operator."""
        assert position_sort_key("Lieutenant") < position_sort_key("Apparatus Operator")

    def test_ao_before_firefighter(self):
        """Apparatus Operator should sort before Firefighter."""
        assert position_sort_key("Apparatus Operator") < position_sort_key("Firefighter")

    def test_firefighter_before_emt(self):
        """Firefighter should sort before EMT."""
        assert position_sort_key("Firefighter") < position_sort_key("EMT")

    def test_unknown_position_last(self):
        """Unknown positions should sort last."""
        assert position_sort_key("Unknown Position") > position_sort_key("EMT")
        assert position_sort_key("Random") > position_sort_key("Support")

    def test_position_with_colon(self):
        """Positions with colons should still match."""
        assert position_sort_key("Captain:") == position_sort_key("Captain")


class TestCleanPosition:
    """Tests for clean_position function."""

    def test_removes_colon(self):
        """Remove colons from position."""
        assert clean_position("Captain:") == "Captain"

    def test_removes_trailing_colon(self):
        """Remove trailing colon."""
        assert clean_position("Firefighter:") == "Firefighter"

    def test_strips_whitespace(self):
        """Strip whitespace."""
        assert clean_position("  Captain  ") == "Captain"

    def test_handles_multiple_colons(self):
        """Handle multiple colons."""
        assert clean_position("Captain: Special:") == "Captain Special"

    def test_no_change_needed(self):
        """No change when already clean."""
        assert clean_position("Firefighter") == "Firefighter"


class TestCrewMember:
    """Tests for CrewMember dataclass."""

    def test_format_html_basic(self):
        """Basic HTML formatting."""
        member = CrewMember(name="John Doe", position="Captain")
        html = member.format_html()

        assert "<b>Captain:</b>" in html
        assert "John Doe" in html

    def test_format_html_with_email(self):
        """HTML formatting with email."""
        member = CrewMember(
            name="John Doe",
            position="Captain",
            email="john.doe@sjifire.org",
        )
        html = member.format_html()

        assert 'href="mailto:john.doe@sjifire.org"' in html
        assert ">email<" in html

    def test_format_html_with_phone(self):
        """HTML formatting with phone."""
        member = CrewMember(
            name="John Doe",
            position="Captain",
            phone="555-123-4567",
        )
        html = member.format_html()

        assert 'href="tel:+15551234567"' in html
        assert "555-123-4567" in html

    def test_format_html_with_email_and_phone(self):
        """HTML formatting with both email and phone."""
        member = CrewMember(
            name="John Doe",
            position="Captain",
            email="john.doe@sjifire.org",
            phone="555-123-4567",
        )
        html = member.format_html()

        assert "mailto:" in html
        assert "tel:" in html
        assert " | " in html  # Separator between email and phone

    def test_format_html_cleans_position(self):
        """HTML formatting cleans position."""
        member = CrewMember(name="John Doe", position="Captain:")
        html = member.format_html()

        assert "<b>Captain:</b>" in html  # Colon removed from position, then added back
        assert "Captain::" not in html  # No double colon

    def test_format_html_escapes_special_chars(self):
        """HTML formatting escapes special characters."""
        member = CrewMember(name="John <script>", position="Captain")
        html = member.format_html()

        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_format_text_basic(self):
        """Basic text formatting."""
        member = CrewMember(name="John Doe", position="Captain")
        text = member.format_text()

        assert text == "Captain: John Doe"

    def test_format_text_cleans_position(self):
        """Text formatting cleans position."""
        member = CrewMember(name="John Doe", position="Captain:")
        text = member.format_text()

        assert text == "Captain: John Doe"


class TestAllDayDutyEvent:
    """Tests for AllDayDutyEvent dataclass."""

    @pytest.fixture
    def sample_crew(self):
        """Create sample crew dict."""
        return {
            "S31": [
                CrewMember(name="John Doe", position="Captain", email="john@test.com"),
                CrewMember(name="Jane Smith", position="Firefighter"),
            ],
            "S32": [
                CrewMember(name="Bob Johnson", position="Apparatus Operator"),
            ],
        }

    def test_subject_is_on_duty(self, sample_crew):
        """Subject is always 'On Duty'."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="B",
            from_1800_crew=sample_crew,
        )
        assert event.subject == "On Duty"

    def test_body_html_has_until_section(self, sample_crew):
        """Body HTML has 'Until 6 PM' section."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="B",
            from_1800_crew={},
        )
        html = event.body_html

        assert "Until 6 PM" in html
        assert "(A)" in html

    def test_body_html_has_from_section(self, sample_crew):
        """Body HTML has 'From 6 PM' section."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="",
            until_1800_crew={},
            from_1800_platoon="B",
            from_1800_crew=sample_crew,
        )
        html = event.body_html

        assert "From 6 PM" in html
        assert "(B)" in html

    def test_body_html_has_both_sections(self, sample_crew):
        """Body HTML has both sections when both have crew."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="B",
            from_1800_crew=sample_crew,
        )
        html = event.body_html

        assert "Until 6 PM" in html
        assert "From 6 PM" in html

    def test_body_html_has_aladtec_link(self, sample_crew):
        """Body HTML has Aladtec link."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="",
            from_1800_crew={},
        )
        html = event.body_html

        assert "Aladtec" in html
        assert "https://secure17.aladtec.com/sjifire/" in html

    def test_body_html_uses_table_format(self, sample_crew):
        """Body HTML uses table format."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="",
            from_1800_crew={},
        )
        html = event.body_html

        assert "<table" in html
        assert "<tr>" in html
        assert "<td" in html

    def test_body_html_includes_crew_names(self, sample_crew):
        """Body HTML includes crew names."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="",
            from_1800_crew={},
        )
        html = event.body_html

        assert "John Doe" in html
        assert "Jane Smith" in html
        assert "Bob Johnson" in html

    def test_body_html_includes_positions(self, sample_crew):
        """Body HTML includes positions."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="",
            from_1800_crew={},
        )
        html = event.body_html

        assert "Captain" in html
        assert "Firefighter" in html
        assert "Apparatus Operator" in html

    def test_body_html_includes_contact_links(self, sample_crew):
        """Body HTML includes contact links."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="",
            from_1800_crew={},
        )
        html = event.body_html

        assert "mailto:john@test.com" in html
        assert ">email<" in html

    def test_body_text_has_sections(self, sample_crew):
        """Body text has section labels."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="B",
            from_1800_crew=sample_crew,
        )
        text = event.body_text

        assert "Until 6 PM" in text
        assert "From 6 PM" in text

    def test_body_text_includes_crew(self, sample_crew):
        """Body text includes crew."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="A",
            until_1800_crew=sample_crew,
            from_1800_platoon="",
            from_1800_crew={},
        )
        text = event.body_text

        assert "John Doe" in text
        assert "Captain:" in text

    def test_empty_platoon_no_parentheses(self):
        """Empty platoon doesn't add parentheses."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_1800_platoon="",
            until_1800_crew={"S31": [CrewMember(name="Test", position="FF")]},
            from_1800_platoon="",
            from_1800_crew={},
        )
        html = event.body_html

        assert "()" not in html
        assert "Until 6 PM</h3>" in html or "Until 6 PM<" in html


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_total_processed(self):
        """Calculate total processed."""
        result = SyncResult(
            events_created=5,
            events_updated=3,
            events_deleted=1,
            events_unchanged=10,
        )
        assert result.total_processed == 19

    def test_str_all_fields(self):
        """String representation with all fields."""
        result = SyncResult(
            events_created=5,
            events_updated=3,
            events_deleted=1,
            events_unchanged=10,
            errors=["Error 1"],
        )
        s = str(result)

        assert "5 created" in s
        assert "3 updated" in s
        assert "1 deleted" in s
        assert "10 unchanged" in s
        assert "1 errors" in s

    def test_str_partial_fields(self):
        """String representation with partial fields."""
        result = SyncResult(events_created=5)
        s = str(result)

        assert "5 created" in s
        assert "updated" not in s
        assert "deleted" not in s
        assert "unchanged" not in s

    def test_str_no_changes(self):
        """String representation with no changes."""
        result = SyncResult()
        assert str(result) == "No changes"

    def test_errors_default_empty(self):
        """Errors default to empty list."""
        result = SyncResult()
        assert result.errors == []
