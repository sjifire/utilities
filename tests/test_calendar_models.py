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
    """Tests for section_sort_key function.

    Sort order priority (soft matching, case-insensitive):
    1. Chief (matches "chief", "chief officer", etc.)
    2. S31 (the primary station)
    3. Backup (matches "backup", "backup duty officer", etc.)
    4. Support (matches "support")
    5. Other stations (S32, S33, etc.) - sorted by number
    6. Everything else - sorted alphabetically
    """

    def test_chief_first(self):
        """Chief sections should sort first (soft match)."""
        assert section_sort_key("Chief") < section_sort_key("S31")
        assert section_sort_key("Chief Officer") < section_sort_key("S31")
        assert section_sort_key("chief on call") < section_sort_key("S31")

    def test_s31_second(self):
        """S31 should sort after Chief but before Backup."""
        assert section_sort_key("S31") < section_sort_key("Backup")
        assert section_sort_key("S31") < section_sort_key("S32")

    def test_backup_third(self):
        """Backup sections should sort after S31 (soft match)."""
        assert section_sort_key("Backup") < section_sort_key("Support")
        assert section_sort_key("Backup Duty Officer") < section_sort_key("Support")
        assert section_sort_key("backup") < section_sort_key("Support")

    def test_support_fourth(self):
        """Support should sort after Backup but before other stations."""
        assert section_sort_key("Support") < section_sort_key("S32")
        assert section_sort_key("support") < section_sort_key("S33")

    def test_other_stations_sorted_numerically(self):
        """Other stations (not S31) should sort numerically after Support."""
        assert section_sort_key("S32") < section_sort_key("S33")
        assert section_sort_key("S33") < section_sort_key("S34")

    def test_other_sections_last(self):
        """Unknown sections should sort last, alphabetically."""
        assert section_sort_key("S36") < section_sort_key("Unknown Section")
        assert section_sort_key("Alpha") < section_sort_key("Zebra")

    # Edge cases for soft matching

    def test_chief_case_insensitive(self):
        """Chief matching is case-insensitive."""
        assert section_sort_key("CHIEF") < section_sort_key("S31")
        assert section_sort_key("Chief") < section_sort_key("S31")
        assert section_sort_key("chief") < section_sort_key("S31")
        assert section_sort_key("ChIeF") < section_sort_key("S31")

    def test_chief_partial_match(self):
        """Chief matches partial strings containing 'chief'."""
        assert section_sort_key("Chief Officer") < section_sort_key("S31")
        assert section_sort_key("Chief on Call") < section_sort_key("S31")
        assert section_sort_key("Acting Chief") < section_sort_key("S31")
        assert section_sort_key("Battalion Chief") < section_sort_key("S31")

    def test_backup_case_insensitive(self):
        """Backup matching is case-insensitive."""
        assert section_sort_key("BACKUP") < section_sort_key("Support")
        assert section_sort_key("Backup") < section_sort_key("Support")
        assert section_sort_key("backup") < section_sort_key("Support")

    def test_backup_partial_match(self):
        """Backup matches partial strings containing 'backup'."""
        assert section_sort_key("Backup Duty") < section_sort_key("Support")
        assert section_sort_key("Backup Duty Officer") < section_sort_key("Support")
        assert section_sort_key("On-Call Backup") < section_sort_key("Support")

    def test_support_case_insensitive(self):
        """Support matching is case-insensitive."""
        assert section_sort_key("SUPPORT") < section_sort_key("S32")
        assert section_sort_key("Support") < section_sort_key("S32")
        assert section_sort_key("support") < section_sort_key("S32")

    def test_s31_case_insensitive(self):
        """S31 matching is case-insensitive."""
        assert section_sort_key("s31") < section_sort_key("Backup")
        assert section_sort_key("S31") < section_sort_key("Backup")

    def test_station_31_alternative_format(self):
        """'Station 31' is treated same as S31."""
        assert section_sort_key("Station 31") < section_sort_key("Backup")
        assert section_sort_key("station 31") < section_sort_key("Backup")

    def test_full_sort_order(self):
        """Verify complete sort order with all priority levels."""
        sections = [
            "S35",
            "Support",
            "S31",
            "Unknown",
            "Backup Duty",
            "Chief Officer",
            "S32",
        ]
        sorted_sections = sorted(sections, key=section_sort_key)

        # Expected order: Chief, S31, Backup, Support, S32, S35, Unknown
        assert sorted_sections == [
            "Chief Officer",
            "S31",
            "Backup Duty",
            "Support",
            "S32",
            "S35",
            "Unknown",
        ]

    def test_combined_keywords_chief_wins(self):
        """When section contains multiple keywords, earliest priority wins."""
        # 'Chief Backup' contains both 'chief' (priority 0) and 'backup' (priority 2)
        # Chief should win since it has higher priority (lower number)
        assert section_sort_key("Chief Backup") < section_sort_key("S31")
        assert section_sort_key("Chief Backup") < section_sort_key("Backup")


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
            until_crew=sample_crew,
            from_crew=sample_crew,
            until_platoon="A",
            from_platoon="B",
        )
        assert event.subject == "On Duty"

    def test_body_html_has_until_section(self, sample_crew, mock_env_vars):
        """Body HTML has 'Until 1800' section."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew={},
            until_platoon="A",
            from_platoon="B",
        )
        html = event.body_html

        assert "Until 1800" in html
        assert "(A)" in html

    def test_body_html_has_from_section(self, sample_crew, mock_env_vars):
        """Body HTML has 'From 1800' section."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew={},
            from_crew=sample_crew,
            until_platoon="",
            from_platoon="B",
        )
        html = event.body_html

        assert "From 1800" in html
        assert "(B)" in html

    def test_body_html_has_both_sections(self, sample_crew, mock_env_vars):
        """Body HTML has both sections when both have crew."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew=sample_crew,
            until_platoon="A",
            from_platoon="B",
        )
        html = event.body_html

        assert "Until 1800" in html
        assert "From 1800" in html

    def test_body_html_has_aladtec_link(self, sample_crew, mock_env_vars):
        """Body HTML has Aladtec link."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew={},
            until_platoon="A",
            from_platoon="",
        )
        html = event.body_html

        assert "Aladtec" in html
        assert "https://test.aladtec.com" in html

    def test_body_html_uses_table_format(self, sample_crew, mock_env_vars):
        """Body HTML uses table format."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew={},
            until_platoon="A",
            from_platoon="",
        )
        html = event.body_html

        assert "<table" in html
        assert "<tr>" in html
        assert "<td" in html

    def test_body_html_includes_crew_names(self, sample_crew, mock_env_vars):
        """Body HTML includes crew names."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew={},
            until_platoon="A",
            from_platoon="",
        )
        html = event.body_html

        assert "John Doe" in html
        assert "Jane Smith" in html
        assert "Bob Johnson" in html

    def test_body_html_includes_positions(self, sample_crew, mock_env_vars):
        """Body HTML includes positions."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew={},
            until_platoon="A",
            from_platoon="",
        )
        html = event.body_html

        assert "Captain" in html
        assert "Firefighter" in html
        assert "Apparatus Operator" in html

    def test_body_html_includes_contact_links(self, sample_crew, mock_env_vars):
        """Body HTML includes contact links."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew={},
            until_platoon="A",
            from_platoon="",
        )
        html = event.body_html

        assert "mailto:john@test.com" in html
        assert ">email<" in html

    def test_body_text_has_sections(self, sample_crew):
        """Body text has section labels."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew=sample_crew,
            until_platoon="A",
            from_platoon="B",
        )
        text = event.body_text

        assert "Until 1800" in text
        assert "From 1800" in text

    def test_body_text_includes_crew(self, sample_crew):
        """Body text includes crew."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew={},
            until_platoon="A",
            from_platoon="",
        )
        text = event.body_text

        assert "John Doe" in text
        assert "Captain:" in text

    def test_empty_platoon_no_parentheses(self, mock_env_vars):
        """Empty platoon doesn't add parentheses."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew={"S31": [CrewMember(name="Test", position="FF")]},
            from_crew={},
            until_platoon="",
            from_platoon="",
        )
        html = event.body_html

        assert "()" not in html
        assert "Until 1800</h3>" in html or "Until 1800<" in html

    def test_custom_shift_change_hour(self, sample_crew, mock_env_vars):
        """Custom shift change hour displays correctly."""
        event = AllDayDutyEvent(
            event_date=date(2026, 2, 1),
            until_crew=sample_crew,
            from_crew=sample_crew,
            shift_change_hour=7,  # 7 AM shift change
        )
        html = event.body_html

        assert "Until 0700" in html
        assert "From 0700" in html
        assert "1800" not in html


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
