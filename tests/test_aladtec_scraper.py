"""Tests for sjifire.aladtec.scraper."""

import pytest

from sjifire.aladtec.scraper import AladtecScraper


class TestAladtecScraperCSVParsing:
    """Tests for CSV parsing methods."""

    def test_parse_csv_basic(self, sample_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_csv(sample_csv_content)

        assert len(members) == 3

        # Check first member
        john = members[0]
        assert john.first_name == "John"
        assert john.last_name == "Doe"
        assert john.email == "john.doe@sjifire.org"
        assert john.phone == "555-1234"
        assert john.home_phone == "555-5678"
        assert john.status == "Active"
        assert john.work_group == "A Shift"
        assert john.employee_id == "EMP001"

    def test_parse_csv_multiple_positions(self, sample_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_csv(sample_csv_content)

        john = members[0]
        assert john.positions == ["Firefighter", "EMT"]

    def test_parse_csv_single_position(self, sample_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_csv(sample_csv_content)

        jane = members[1]
        assert jane.positions == ["Apparatus Operator"]

    def test_parse_csv_missing_email(self, sample_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_csv(sample_csv_content)

        bob = members[2]
        assert bob.email is None

    def test_parse_csv_missing_optional_fields(self, sample_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_csv(sample_csv_content)

        jane = members[1]
        assert jane.home_phone is None
        assert jane.evip is None


class TestAladtecScraperInactiveCSVParsing:
    """Tests for inactive members CSV parsing."""

    def test_parse_inactive_csv(self, sample_inactive_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_inactive_csv(sample_inactive_csv_content)

        assert len(members) == 2

    def test_parse_inactive_csv_name_format(self, sample_inactive_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_inactive_csv(sample_inactive_csv_content)

        # Name is "Doe, John" in CSV
        john = members[0]
        assert john.first_name == "John"
        assert john.last_name == "Doe"
        assert john.email == "john.doe@sjifire.org"

    def test_parse_inactive_csv_status(self, sample_inactive_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_inactive_csv(sample_inactive_csv_content)

        for member in members:
            assert member.status == "Inactive"
            assert member.is_active is False

    def test_parse_inactive_csv_missing_email(self, sample_inactive_csv_content, mock_env_vars):
        scraper = AladtecScraper()
        members = scraper._parse_inactive_csv(sample_inactive_csv_content)

        former = members[1]
        assert former.email is None


class TestAladtecScraperCSVRowParsing:
    """Tests for individual CSV row parsing."""

    def test_parse_csv_row_basic(self, mock_env_vars):
        scraper = AladtecScraper()
        row = {
            "first name": "John",
            "last name": "Doe",
            "email": "john.doe@sjifire.org",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == "John"
        assert member.last_name == "Doe"
        assert member.email == "john.doe@sjifire.org"

    def test_parse_csv_row_normalized_keys(self, mock_env_vars):
        scraper = AladtecScraper()
        row = {
            "First Name": "John",
            "Last Name": "Doe",
            " Email ": "john@example.com",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == "John"
        assert member.last_name == "Doe"

    def test_parse_csv_row_missing_name(self, mock_env_vars):
        scraper = AladtecScraper()
        row = {"email": "john@example.com"}
        member = scraper._parse_csv_row(row)

        assert member is None

    def test_parse_csv_row_multiple_emails(self, mock_env_vars):
        scraper = AladtecScraper()
        row = {
            "first name": "John",
            "last name": "Doe",
            "email": "john.doe@sjifire.org, john.personal@gmail.com",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.email == "john.doe@sjifire.org"
        assert member.personal_email == "john.personal@gmail.com"

    def test_parse_csv_row_name_column_with_comma(self, mock_env_vars):
        scraper = AladtecScraper()
        row = {"name": "Doe, John"}
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == "John"
        assert member.last_name == "Doe"

    def test_parse_csv_row_name_column_without_comma(self, mock_env_vars):
        scraper = AladtecScraper()
        row = {"name": "John Doe"}
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == "John"
        assert member.last_name == "Doe"

    def test_parse_csv_row_generates_id(self, mock_env_vars):
        scraper = AladtecScraper()
        row = {
            "first name": "John",
            "last name": "Doe",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.id == "john.doe"

    def test_parse_csv_row_uses_employee_id(self, mock_env_vars):
        scraper = AladtecScraper()
        row = {
            "first name": "John",
            "last name": "Doe",
            "employee id": "EMP001",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.id == "EMP001"
        assert member.employee_id == "EMP001"


class TestAladtecScraperContextManager:
    """Tests for context manager functionality."""

    def test_context_manager_creates_client(self, mock_env_vars):
        with AladtecScraper() as scraper:
            assert scraper.client is not None

    def test_context_manager_closes_client(self, mock_env_vars):
        scraper = AladtecScraper()
        with scraper:
            pass
        assert scraper.client is None

    def test_requires_context_manager_for_login(self, mock_env_vars):
        scraper = AladtecScraper()
        with pytest.raises(RuntimeError, match="must be used as context manager"):
            scraper.login()

    def test_requires_context_manager_for_get_members(self, mock_env_vars):
        scraper = AladtecScraper()
        with pytest.raises(RuntimeError, match="must be used as context manager"):
            scraper.get_members()
