"""Tests for sjifire.core.normalize."""

from sjifire.core.normalize import (
    clean_name_for_display,
    format_phone,
    normalize_email,
    normalize_name,
    normalize_name_part,
    normalize_phone,
    validate_email,
)


class TestFormatPhone:
    """Tests for format_phone function."""

    def test_formats_us_number(self):
        result = format_phone("5551234567")
        assert result == "(555) 123-4567"

    def test_formats_number_with_dashes(self):
        result = format_phone("555-123-4567")
        assert result == "(555) 123-4567"

    def test_formats_number_with_dots(self):
        result = format_phone("555.123.4567")
        assert result == "(555) 123-4567"

    def test_formats_number_with_country_code(self):
        result = format_phone("+1 555 123 4567")
        assert result == "(555) 123-4567"

    def test_returns_none_for_empty(self):
        assert format_phone(None) is None
        assert format_phone("") is None

    def test_returns_stripped_for_invalid(self):
        result = format_phone("  abc  ")
        assert result == "abc"


class TestNormalizePhone:
    """Tests for normalize_phone function."""

    def test_extracts_digits(self):
        result = normalize_phone("(555) 123-4567")
        assert result == "5551234567"

    def test_handles_country_code(self):
        result = normalize_phone("+1 555 123 4567")
        assert result == "15551234567"

    def test_returns_none_for_empty(self):
        assert normalize_phone(None) is None
        assert normalize_phone("") is None

    def test_returns_none_for_no_digits(self):
        assert normalize_phone("abc") is None


class TestValidateEmail:
    """Tests for validate_email function."""

    def test_validates_good_email(self):
        result = validate_email("test@example.com")
        assert result == "test@example.com"

    def test_normalizes_email(self):
        # email-validator preserves local part case, lowercases domain
        result = validate_email("  TEST@EXAMPLE.COM  ")
        assert result == "TEST@example.com"

    def test_returns_none_for_invalid(self):
        result = validate_email("not-an-email")
        assert result is None

    def test_returns_none_for_empty(self):
        assert validate_email(None) is None
        assert validate_email("") is None

    def test_logs_warning_for_invalid(self, caplog):
        validate_email("bad-email", context="Test User")
        assert "Invalid email" in caplog.text
        assert "Test User" in caplog.text


class TestNormalizeEmail:
    """Tests for normalize_email function."""

    def test_lowercases_email(self):
        result = normalize_email("TEST@EXAMPLE.COM")
        assert result == "test@example.com"

    def test_strips_whitespace(self):
        result = normalize_email("  test@example.com  ")
        assert result == "test@example.com"

    def test_returns_none_for_empty(self):
        assert normalize_email(None) is None
        assert normalize_email("") is None


class TestNormalizeName:
    """Tests for normalize_name function."""

    def test_combines_first_and_last(self):
        result = normalize_name("John", "Doe")
        assert result == "john doe"

    def test_lowercases(self):
        result = normalize_name("JOHN", "DOE")
        assert result == "john doe"

    def test_strips_whitespace(self):
        result = normalize_name("  John  ", "  Doe  ")
        assert result == "john doe"

    def test_handles_none(self):
        result = normalize_name(None, "Doe")
        assert result == " doe"

        result = normalize_name("John", None)
        assert result == "john "

        result = normalize_name(None, None)
        assert result == " "


class TestNormalizeNamePart:
    """Tests for normalize_name_part function."""

    def test_lowercases(self):
        result = normalize_name_part("JOHN")
        assert result == "john"

    def test_removes_spaces(self):
        result = normalize_name_part("Mary Jane")
        assert result == "maryjane"

    def test_removes_apostrophes(self):
        result = normalize_name_part("O'Brien")
        assert result == "obrien"

    def test_handles_combined(self):
        result = normalize_name_part("Mary Jane O'Brien")
        assert result == "maryjaneobrien"

    def test_returns_empty_for_none(self):
        assert normalize_name_part(None) == ""
        assert normalize_name_part("") == ""


class TestCleanNameForDisplay:
    """Tests for clean_name_for_display function."""

    def test_strips_whitespace(self):
        result = clean_name_for_display("  John Doe  ")
        assert result == "John Doe"

    def test_normalizes_internal_spaces(self):
        result = clean_name_for_display("John    Doe")
        assert result == "John Doe"

    def test_handles_tabs_and_newlines(self):
        result = clean_name_for_display("John\t\nDoe")
        assert result == "John Doe"

    def test_returns_empty_for_none(self):
        assert clean_name_for_display(None) == ""
        assert clean_name_for_display("") == ""
