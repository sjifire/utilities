"""Tests for sjifire.entra.aladtec_import."""

import json
from unittest.mock import patch

import pytest

from sjifire.aladtec.models import Member
from sjifire.entra.aladtec_import import AladtecImporter, ImportResult
from sjifire.entra.users import EntraUser


@pytest.fixture
def mock_config(tmp_path):
    """Create a mock config file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "entra_sync.json"
    config_file.write_text(
        json.dumps(
            {
                "company_name": "Test Fire Department",
                "domain": "testfire.org",
            }
        )
    )
    return tmp_path


@pytest.fixture
def importer(mock_config):
    """Create an AladtecImporter with mocked config."""
    with patch("sjifire.entra.aladtec_import.load_entra_sync_config") as mock_load:
        from sjifire.core.config import EntraSyncConfig

        mock_load.return_value = EntraSyncConfig(
            company_name="Test Fire Department",
            domain="testfire.org",
        )
        # Also mock the EntraUserManager to avoid API calls
        with patch("sjifire.entra.aladtec_import.EntraUserManager"):
            return AladtecImporter()


class TestImportResult:
    """Tests for ImportResult dataclass."""

    def test_total_processed(self):
        result = ImportResult(
            created=[{"member": "A"}],
            updated=[{"member": "B"}, {"member": "C"}],
            disabled=[],
            skipped=[{"member": "D"}],
            errors=[],
        )
        assert result.total_processed == 4

    def test_total_processed_empty(self):
        result = ImportResult()
        assert result.total_processed == 0

    def test_summary_format(self):
        result = ImportResult(
            created=[{"member": "A"}],
            updated=[{"member": "B"}],
            disabled=[{"member": "C"}],
            skipped=[{"member": "D"}],
            errors=[{"member": "E"}],
        )
        summary = result.summary()
        assert "Created: 1" in summary
        assert "Updated: 1" in summary
        assert "Disabled: 1" in summary
        assert "Skipped: 1" in summary
        assert "Errors: 1" in summary


class TestBuildDisplayName:
    """Tests for _build_display_name method."""

    def test_with_rank_captain(self, importer):
        member = Member(
            id="1",
            first_name="Kyle",
            last_name="Dodd",
            position="Captain",
        )
        assert importer._build_display_name(member) == "Captain Kyle Dodd"

    def test_with_rank_lieutenant(self, importer):
        member = Member(
            id="1",
            first_name="Tom",
            last_name="Eades",
            position="Lieutenant",
        )
        assert importer._build_display_name(member) == "Lieutenant Tom Eades"

    def test_with_rank_chief(self, importer):
        member = Member(
            id="1",
            first_name="Mike",
            last_name="Hartzell",
            position="Chief",
        )
        assert importer._build_display_name(member) == "Chief Mike Hartzell"

    def test_without_rank(self, importer):
        member = Member(
            id="1",
            first_name="John",
            last_name="Smith",
            position="Firefighter",
        )
        assert importer._build_display_name(member) == "John Smith"

    def test_with_rank_from_title(self, importer):
        member = Member(
            id="1",
            first_name="Jane",
            last_name="Doe",
            title="Captain",
        )
        assert importer._build_display_name(member) == "Captain Jane Doe"

    def test_no_position_or_title(self, importer):
        member = Member(id="1", first_name="Bob", last_name="Jones")
        assert importer._build_display_name(member) == "Bob Jones"


class TestNeedsUpdate:
    """Tests for _needs_update method."""

    def test_no_changes_needed(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id="123",
            job_title=None,
            mobile_phone="555-1234",
            office_location="Station 31",
            employee_type="Volunteer",
            company_name="Test Fire Department",
            extension_attribute1=None,
            extension_attribute2=None,
            extension_attribute3="Firefighter",
        )
        member = Member(
            id="123",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            employee_id="123",
            phone="555-1234",
            station_assignment="31",
            work_group="Volunteer",
            positions=["Firefighter"],
        )
        assert importer._needs_update(existing, member) is False

    def test_first_name_changed(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id=None,
        )
        member = Member(
            id="1",
            first_name="Jonathan",
            last_name="Smith",
            email="jsmith@testfire.org",
        )
        assert importer._needs_update(existing, member) is True

    def test_display_name_needs_rank_prefix(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="Kyle Dodd",  # Missing rank prefix
            first_name="Kyle",
            last_name="Dodd",
            email="kdodd@testfire.org",
            upn="kdodd@testfire.org",
            employee_id=None,
        )
        member = Member(
            id="1",
            first_name="Kyle",
            last_name="Dodd",
            email="kdodd@testfire.org",
            position="Captain",
        )
        # Should need update because display name should be "Captain Kyle Dodd"
        assert importer._needs_update(existing, member) is True

    def test_employee_type_changed(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id=None,
            employee_type="Volunteer",
        )
        member = Member(
            id="1",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            work_group="FT Line Staff",
        )
        assert importer._needs_update(existing, member) is True

    def test_extension_attribute_rank_changed(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="Captain John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id=None,
            extension_attribute1=None,  # No rank stored
        )
        member = Member(
            id="1",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            position="Captain",
        )
        assert importer._needs_update(existing, member) is True

    def test_extension_attribute_positions_changed(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id=None,
            extension_attribute3="Firefighter",
        )
        member = Member(
            id="1",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            positions=["Firefighter", "EMT"],  # Added EMT
        )
        assert importer._needs_update(existing, member) is True

    def test_hire_date_entra_empty_needs_update(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id=None,
            employee_hire_date=None,
        )
        member = Member(
            id="1",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            date_hired="2020-01-15",
        )
        assert importer._needs_update(existing, member) is True

    def test_hire_date_aladtec_older_updates(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id=None,
            employee_hire_date="2020-06-01T00:00:00",
        )
        member = Member(
            id="1",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            date_hired="2020-01-15",  # Older than Entra - should update
        )
        assert importer._needs_update(existing, member) is True

    def test_hire_date_conflict_skipped(self, importer):
        """When Aladtec date is newer than Entra, don't update (conflict)."""
        existing = EntraUser(
            id="user-1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id=None,
            employee_hire_date="2020-01-15T00:00:00",
            company_name="Test Fire Department",
        )
        member = Member(
            id="1",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            date_hired="2020-06-01",  # Newer than Entra - conflict, don't update
        )
        # Should NOT need update due to hire date (conflict logged but skipped)
        # But may need update for other reasons, so we need to ensure all other fields match
        existing.employee_type = None
        existing.extension_attribute1 = None
        existing.extension_attribute2 = None
        existing.extension_attribute3 = None
        member.work_group = None
        member.positions = []

        assert importer._needs_update(existing, member) is False

    def test_company_name_changed(self, importer):
        existing = EntraUser(
            id="user-1",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id=None,
            company_name="Old Company Name",
        )
        member = Member(
            id="1",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
        )
        assert importer._needs_update(existing, member) is True


class TestImporterConfig:
    """Tests for importer configuration loading."""

    def test_loads_company_name_from_config(self):
        with patch("sjifire.entra.aladtec_import.load_entra_sync_config") as mock_load:
            from sjifire.core.config import EntraSyncConfig

            mock_load.return_value = EntraSyncConfig(
                company_name="Custom Fire Dept",
                domain="custom.org",
            )
            with patch("sjifire.entra.aladtec_import.EntraUserManager"):
                importer = AladtecImporter()

            assert importer.company_name == "Custom Fire Dept"
            assert importer.domain == "custom.org"

    def test_override_config_with_params(self):
        with patch("sjifire.entra.aladtec_import.load_entra_sync_config") as mock_load:
            from sjifire.core.config import EntraSyncConfig

            mock_load.return_value = EntraSyncConfig(
                company_name="Config Company",
                domain="config.org",
            )
            with patch("sjifire.entra.aladtec_import.EntraUserManager"):
                importer = AladtecImporter(
                    domain="override.org",
                    company_name="Override Company",
                )

            assert importer.company_name == "Override Company"
            assert importer.domain == "override.org"
