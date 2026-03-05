"""Tests for sjifire.aladtec.scraper."""

import pytest

from sjifire.aladtec.member_scraper import AladtecMemberScraper


class TestAladtecMemberScraperCSVParsing:
    """Tests for CSV parsing methods."""

    def test_parse_csv_basic(self, sample_csv_content, mock_env_vars):
        scraper = AladtecMemberScraper()
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
        scraper = AladtecMemberScraper()
        members = scraper._parse_csv(sample_csv_content)

        john = members[0]
        assert john.positions == ["Firefighter", "EMT"]

    def test_parse_csv_single_position(self, sample_csv_content, mock_env_vars):
        scraper = AladtecMemberScraper()
        members = scraper._parse_csv(sample_csv_content)

        jane = members[1]
        assert jane.positions == ["Apparatus Operator"]

    def test_parse_csv_missing_email(self, sample_csv_content, mock_env_vars):
        scraper = AladtecMemberScraper()
        members = scraper._parse_csv(sample_csv_content)

        bob = members[2]
        assert bob.email is None

    def test_parse_csv_missing_optional_fields(self, sample_csv_content, mock_env_vars):
        scraper = AladtecMemberScraper()
        members = scraper._parse_csv(sample_csv_content)

        jane = members[1]
        assert jane.home_phone is None
        assert jane.evip is None


class TestAladtecMemberScraperInactiveCSVParsing:
    """Tests for inactive members CSV parsing."""

    def test_parse_inactive_csv(self, sample_inactive_csv_content, mock_env_vars):
        scraper = AladtecMemberScraper()
        members = scraper._parse_inactive_csv(sample_inactive_csv_content)

        assert len(members) == 2

    def test_parse_inactive_csv_name_format(self, sample_inactive_csv_content, mock_env_vars):
        scraper = AladtecMemberScraper()
        members = scraper._parse_inactive_csv(sample_inactive_csv_content)

        # Name is "Doe, John" in CSV
        john = members[0]
        assert john.first_name == "John"
        assert john.last_name == "Doe"
        assert john.email == "john.doe@sjifire.org"

    def test_parse_inactive_csv_status(self, sample_inactive_csv_content, mock_env_vars):
        scraper = AladtecMemberScraper()
        members = scraper._parse_inactive_csv(sample_inactive_csv_content)

        for member in members:
            assert member.status == "Inactive"
            assert member.is_active is False

    def test_parse_inactive_csv_missing_email(self, sample_inactive_csv_content, mock_env_vars):
        scraper = AladtecMemberScraper()
        members = scraper._parse_inactive_csv(sample_inactive_csv_content)

        former = members[1]
        assert former.email is None


class TestAladtecMemberScraperCSVRowParsing:
    """Tests for individual CSV row parsing."""

    def test_parse_csv_row_basic(self, mock_env_vars):
        scraper = AladtecMemberScraper()
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
        scraper = AladtecMemberScraper()
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
        scraper = AladtecMemberScraper()
        row = {"email": "john@example.com"}
        member = scraper._parse_csv_row(row)

        assert member is None

    def test_parse_csv_row_multiple_emails(self, mock_env_vars):
        scraper = AladtecMemberScraper()
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
        scraper = AladtecMemberScraper()
        row = {"name": "Doe, John"}
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == "John"
        assert member.last_name == "Doe"

    def test_parse_csv_row_name_column_without_comma(self, mock_env_vars):
        scraper = AladtecMemberScraper()
        row = {"name": "John Doe"}
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == "John"
        assert member.last_name == "Doe"

    def test_parse_csv_row_generates_id(self, mock_env_vars):
        scraper = AladtecMemberScraper()
        row = {
            "first name": "John",
            "last name": "Doe",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.id == "john.doe"

    def test_parse_csv_row_uses_employee_id(self, mock_env_vars):
        scraper = AladtecMemberScraper()
        row = {
            "first name": "John",
            "last name": "Doe",
            "employee id": "EMP001",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.id == "EMP001"
        assert member.employee_id == "EMP001"


class TestAladtecMemberScraperContextManager:
    """Tests for context manager functionality."""

    def test_context_manager_creates_client(self, mock_env_vars):
        with AladtecMemberScraper() as scraper:
            assert scraper.client is not None

    def test_context_manager_closes_client(self, mock_env_vars):
        scraper = AladtecMemberScraper()
        with scraper:
            pass
        assert scraper.client is None

    def test_requires_context_manager_for_login(self, mock_env_vars):
        scraper = AladtecMemberScraper()
        with pytest.raises(RuntimeError, match="must be used as context manager"):
            scraper.login()

    def test_requires_context_manager_for_get_members(self, mock_env_vars):
        scraper = AladtecMemberScraper()
        with pytest.raises(RuntimeError, match="must be used as context manager"):
            scraper.get_members()


class TestGetMemberPositions:
    """Tests for get_member_positions HTML parsing."""

    def test_parses_positions_from_list_items(self, mock_env_vars):
        """Should parse positions from <li> elements in view mode."""
        from unittest.mock import MagicMock

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        # Simulate HTML response with positions as list items
        html = """
        <table>
            <tr>
                <td>Positions:</td>
                <td class="value">
                    <ul class="ul-arrow">
                        <li>Firefighter</li>
                        <li>EMT</li>
                        <li>Wildland Firefighter</li>
                    </ul>
                </td>
            </tr>
        </table>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        scraper.client.get.return_value = mock_response

        positions = scraper.get_member_positions("123")

        assert positions == ["Firefighter", "EMT", "Wildland Firefighter"]

    def test_parses_positions_from_checkboxes(self, mock_env_vars):
        """Should parse positions from checked checkboxes in edit mode."""
        from unittest.mock import MagicMock

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        # Simulate HTML response with positions as checkboxes
        html = """
        <table>
            <tr>
                <td>Positions:</td>
                <td class="value">
                    <input type="checkbox" id="pos1" checked>
                    <label for="pos1">Firefighter</label>
                    <input type="checkbox" id="pos2">
                    <label for="pos2">EMT</label>
                    <input type="checkbox" id="pos3" checked>
                    <label for="pos3">Captain</label>
                </td>
            </tr>
        </table>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        scraper.client.get.return_value = mock_response

        positions = scraper.get_member_positions("123")

        assert positions == ["Firefighter", "Captain"]

    def test_returns_empty_list_when_no_positions_section(self, mock_env_vars):
        """Should return empty list if Positions section not found."""
        from unittest.mock import MagicMock

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        html = "<html><body>No positions here</body></html>"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        scraper.client.get.return_value = mock_response

        positions = scraper.get_member_positions("123")

        assert positions == []

    def test_prefers_list_items_over_checkboxes(self, mock_env_vars):
        """Should use list items when both formats present."""
        from unittest.mock import MagicMock

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        # HTML with both list items and checkboxes
        html = """
        <table>
            <tr>
                <td>Positions:</td>
                <td class="value">
                    <ul><li>From List</li></ul>
                    <input type="checkbox" id="pos1" checked>
                    <label for="pos1">From Checkbox</label>
                </td>
            </tr>
        </table>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        scraper.client.get.return_value = mock_response

        positions = scraper.get_member_positions("123")

        # Should only get list items, not checkboxes
        assert positions == ["From List"]


class TestEnrichMemberDetails:
    """Tests for enrich_member_details failure behavior."""

    def _make_member(self, first, last, employee_type="Lieutenant"):
        """Create a Member with CSV-derived positions (simulating pre-enrichment state)."""
        from sjifire.aladtec.models import Member

        positions = [p.strip() for p in employee_type.split(",") if p.strip()]
        return Member(
            id=f"{first.lower()}.{last.lower()}",
            first_name=first,
            last_name=last,
            positions=positions,
        )

    def test_enrichment_success_overwrites_csv_positions(self, mock_env_vars):
        """Successful enrichment should replace CSV-derived positions."""
        from unittest.mock import MagicMock, patch

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        member = self._make_member("Harry", "See", employee_type="Lieutenant")
        assert member.positions == ["Lieutenant"]

        html = """
        <table>
            <tr>
                <td>Positions:</td>
                <td><ul><li>Apparatus Operator</li></ul></td>
            </tr>
            <tr>
                <td>Schedules:</td>
                <td><ul><li>A Shift</li></ul></td>
            </tr>
        </table>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        with patch.object(scraper, "get_user_id_map", return_value={"See, Harry": "42"}):
            scraper.client.get.return_value = mock_response
            result = scraper.enrich_member_details([member])

        assert result[0].positions == ["Apparatus Operator"]
        assert result[0].schedules == ["A Shift"]

    def test_enrichment_failure_raises_runtime_error(self, mock_env_vars):
        """Failed detail page fetch should raise RuntimeError, not silently continue."""
        from unittest.mock import MagicMock, patch

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        member = self._make_member("Harry", "See", employee_type="Lieutenant")

        # Simulate 429 rate limit response (persists after retries)
        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch.object(scraper, "get_user_id_map", return_value={"See, Harry": "42"}):
            # Bypass tenacity wait to speed up test
            scraper._get_with_retry.retry.wait = lambda *a, **kw: 0
            scraper.client.get.return_value = mock_response
            with pytest.raises(RuntimeError, match="Enrichment failed for 1 member"):
                scraper.enrich_member_details([member])

        # Positions should NOT have been updated (still CSV value)
        assert member.positions == ["Lieutenant"]

    def test_enrichment_failure_preserves_csv_positions(self, mock_env_vars):
        """When enrichment fails, the original CSV-derived positions remain unchanged."""
        from unittest.mock import MagicMock, patch

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        member = self._make_member("Harry", "See", employee_type="Lieutenant")

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.object(scraper, "get_user_id_map", return_value={"See, Harry": "42"}):
            scraper.client.get.return_value = mock_response
            with pytest.raises(RuntimeError):
                scraper.enrich_member_details([member])

        # CSV-derived positions should remain (but sync should be aborted by the error)
        assert member.positions == ["Lieutenant"]

    def test_enrichment_retries_on_429_then_succeeds(self, mock_env_vars):
        """429 should be retried; success on retry should enrich correctly."""
        from unittest.mock import MagicMock, patch

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        member = self._make_member("Harry", "See", employee_type="Lieutenant")

        html = """
        <table>
            <tr><td>Positions:</td><td><ul><li>Apparatus Operator</li></ul></td></tr>
            <tr><td>Schedules:</td><td><ul><li>A Shift</li></ul></td></tr>
        </table>
        """

        mock_429 = MagicMock()
        mock_429.status_code = 429

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.text = html

        with patch.object(scraper, "get_user_id_map", return_value={"See, Harry": "42"}):
            # Bypass tenacity wait to speed up test
            scraper._get_with_retry.retry.wait = lambda *a, **kw: 0
            # First call: 429, second call: 200
            scraper.client.get.side_effect = [mock_429, mock_200]
            result = scraper.enrich_member_details([member])

        assert result[0].positions == ["Apparatus Operator"]
        assert scraper.client.get.call_count == 2

    def test_enrichment_no_user_id_is_not_failure(self, mock_env_vars):
        """Members with no matching user ID should be skipped without error."""
        from unittest.mock import MagicMock, patch

        scraper = AladtecMemberScraper()
        scraper.client = MagicMock()

        member = self._make_member("Unknown", "Person")

        # Empty user map — no IDs to look up
        with patch.object(scraper, "get_user_id_map", return_value={}):
            result = scraper.enrich_member_details([member])

        # Should succeed — no detail page fetch attempted
        assert len(result) == 1


class TestParseCSVRowEdgeCases:
    """Tests for _parse_csv_row edge cases."""

    def test_none_key_in_row(self, mock_env_vars):
        """csv.DictReader produces None keys when rows have more values than headers."""
        scraper = AladtecMemberScraper()
        row = {
            "first name": "Jordan",
            "last name": "Pollack",
            "email": "jpollack@sjifire.org",
            None: "extra value",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == "Jordan"
        assert member.last_name == "Pollack"
        assert member.email == "jpollack@sjifire.org"

    def test_none_key_with_all_fields(self, mock_env_vars):
        """None key shouldn't interfere with parsing any real fields."""
        scraper = AladtecMemberScraper()
        row = {
            "first name": "Jordan",
            "last name": "Pollack",
            "email": "jpollack@sjifire.org",
            "title": "Division Chief",
            "employee type": "Chief",
            "work group": "Contractor",
            "station assignment": "31",
            None: "overflow",
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.title == "Division Chief"
        assert member.employee_type == "Chief"
        assert member.work_group == "Contractor"
        assert member.station_assignment == "31"

    def test_multiple_none_keys_in_row(self, mock_env_vars):
        """Multiple extra columns produce a single None key (last value wins)."""
        scraper = AladtecMemberScraper()
        # DictReader uses restkey=None, so extra values are a list under None
        row = {
            "first name": "John",
            "last name": "Doe",
            None: ["extra1", "extra2"],
        }
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == "John"
        assert member.last_name == "Doe"
