"""Tests for sjifire.entra.aladtec_import."""

import json
from unittest.mock import AsyncMock, patch

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


class TestHandleExistingUserDisableInactive:
    """Tests for _handle_existing_user with disable_inactive functionality."""

    @pytest.fixture
    def active_entra_user(self):
        """An active Entra user."""
        return EntraUser(
            id="user-123",
            display_name="John Smith",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            upn="jsmith@testfire.org",
            employee_id="123",
            account_enabled=True,
        )

    @pytest.fixture
    def disabled_entra_user(self):
        """A disabled Entra user."""
        return EntraUser(
            id="user-456",
            display_name="Jane Doe",
            first_name="Jane",
            last_name="Doe",
            email="jdoe@testfire.org",
            upn="jdoe@testfire.org",
            employee_id="456",
            account_enabled=False,
        )

    @pytest.fixture
    def inactive_member(self):
        """An inactive Aladtec member."""
        return Member(
            id="123",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            status="Inactive",
        )

    @pytest.fixture
    def active_member(self):
        """An active Aladtec member."""
        return Member(
            id="789",
            first_name="Bob",
            last_name="Jones",
            email="bjones@testfire.org",
            status="Active",
        )

    async def test_disables_active_entra_user_when_member_inactive(
        self, importer, active_entra_user, inactive_member
    ):
        """Should disable Entra account when Aladtec member is inactive."""
        result = ImportResult()
        importer.user_manager.disable_user = AsyncMock(return_value=True)

        await importer._handle_existing_user(
            member=inactive_member,
            existing=active_entra_user,
            result=result,
            dry_run=False,
            disable_inactive=True,
        )

        importer.user_manager.disable_user.assert_called_once_with("user-123")
        assert len(result.disabled) == 1
        assert result.disabled[0]["member"] == "John Smith"
        assert result.disabled[0]["user_id"] == "user-123"
        assert len(result.errors) == 0

    async def test_dry_run_reports_would_disable(
        self, importer, active_entra_user, inactive_member
    ):
        """Dry run should report would disable without calling API."""
        result = ImportResult()
        importer.user_manager.disable_user = AsyncMock()

        await importer._handle_existing_user(
            member=inactive_member,
            existing=active_entra_user,
            result=result,
            dry_run=True,
            disable_inactive=True,
        )

        importer.user_manager.disable_user.assert_not_called()
        assert len(result.disabled) == 1
        assert result.disabled[0]["member"] == "John Smith"

    async def test_skips_already_disabled_user(
        self, importer, disabled_entra_user, inactive_member
    ):
        """Should skip if Entra account is already disabled."""
        result = ImportResult()
        inactive_member.email = "jdoe@testfire.org"
        inactive_member.first_name = "Jane"
        inactive_member.last_name = "Doe"
        importer.user_manager.disable_user = AsyncMock()

        await importer._handle_existing_user(
            member=inactive_member,
            existing=disabled_entra_user,
            result=result,
            dry_run=False,
            disable_inactive=True,
        )

        importer.user_manager.disable_user.assert_not_called()
        assert len(result.disabled) == 0
        assert len(result.skipped) == 1
        assert result.skipped[0]["reason"] == "already disabled"

    async def test_does_not_disable_when_flag_is_false(
        self, importer, active_entra_user, inactive_member
    ):
        """Should not disable when disable_inactive=False."""
        result = ImportResult()
        importer.user_manager.disable_user = AsyncMock()
        importer.user_manager.update_user = AsyncMock(return_value=True)

        # Make sure member needs no update so it gets skipped
        active_entra_user.company_name = importer.company_name

        await importer._handle_existing_user(
            member=inactive_member,
            existing=active_entra_user,
            result=result,
            dry_run=False,
            disable_inactive=False,
        )

        importer.user_manager.disable_user.assert_not_called()
        # Should be skipped or updated, not disabled
        assert len(result.disabled) == 0

    async def test_does_not_disable_active_member(self, importer, active_entra_user, active_member):
        """Should not disable when Aladtec member is active."""
        result = ImportResult()
        importer.user_manager.disable_user = AsyncMock()
        importer.user_manager.update_user = AsyncMock(return_value=True)
        active_entra_user.email = "bjones@testfire.org"
        active_entra_user.upn = "bjones@testfire.org"
        active_entra_user.company_name = importer.company_name

        await importer._handle_existing_user(
            member=active_member,
            existing=active_entra_user,
            result=result,
            dry_run=False,
            disable_inactive=True,
        )

        importer.user_manager.disable_user.assert_not_called()
        assert len(result.disabled) == 0

    async def test_records_error_when_disable_fails(
        self, importer, active_entra_user, inactive_member
    ):
        """Should record error when disable_user API call fails."""
        result = ImportResult()
        importer.user_manager.disable_user = AsyncMock(return_value=False)

        await importer._handle_existing_user(
            member=inactive_member,
            existing=active_entra_user,
            result=result,
            dry_run=False,
            disable_inactive=True,
        )

        assert len(result.disabled) == 0
        assert len(result.errors) == 1
        assert "Failed to disable" in result.errors[0]["error"]


class TestHandleNewUserInactive:
    """Tests for _handle_new_user with inactive members."""

    async def test_skips_inactive_member(self, importer):
        """Should not create account for inactive member."""
        result = ImportResult()
        inactive_member = Member(
            id="123",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            status="Inactive",
        )
        importer.user_manager.create_user = AsyncMock()

        await importer._handle_new_user(
            member=inactive_member,
            result=result,
            dry_run=False,
        )

        importer.user_manager.create_user.assert_not_called()
        assert len(result.created) == 0
        assert len(result.skipped) == 1
        assert "inactive member" in result.skipped[0]["reason"]

    async def test_skips_inactive_member_dry_run(self, importer):
        """Should skip inactive member even in dry run."""
        result = ImportResult()
        inactive_member = Member(
            id="123",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            status="Inactive",
        )

        await importer._handle_new_user(
            member=inactive_member,
            result=result,
            dry_run=True,
        )

        assert len(result.created) == 0
        assert len(result.skipped) == 1

    async def test_creates_active_member(self, importer):
        """Should create account for active member."""
        result = ImportResult()
        active_member = Member(
            id="123",
            first_name="John",
            last_name="Smith",
            email="jsmith@testfire.org",
            status="Active",
        )
        importer.user_manager.create_user = AsyncMock(
            return_value=EntraUser(
                id="new-user-id",
                display_name="John Smith",
                first_name="John",
                last_name="Smith",
                email="jsmith@testfire.org",
                upn="jsmith@testfire.org",
                employee_id=None,
            )
        )

        await importer._handle_new_user(
            member=active_member,
            result=result,
            dry_run=False,
        )

        importer.user_manager.create_user.assert_called_once()
        assert len(result.created) == 1
        assert result.created[0]["member"] == "John Smith"


class TestImportMembersDisableInactive:
    """Integration tests for import_members with disable_inactive."""

    async def test_disables_multiple_inactive_members(self, importer):
        """Should disable all inactive members with active Entra accounts."""
        members = [
            Member(
                id="1",
                first_name="John",
                last_name="Smith",
                email="jsmith@testfire.org",
                status="Inactive",
            ),
            Member(
                id="2",
                first_name="Jane",
                last_name="Doe",
                email="jdoe@testfire.org",
                status="Inactive",
            ),
            Member(
                id="3",
                first_name="Bob",
                last_name="Jones",
                email="bjones@testfire.org",
                status="Active",
            ),
        ]

        existing_users = [
            EntraUser(
                id="u1",
                display_name="John Smith",
                first_name="John",
                last_name="Smith",
                email="jsmith@testfire.org",
                upn="jsmith@testfire.org",
                employee_id=None,
                account_enabled=True,
            ),
            EntraUser(
                id="u2",
                display_name="Jane Doe",
                first_name="Jane",
                last_name="Doe",
                email="jdoe@testfire.org",
                upn="jdoe@testfire.org",
                employee_id=None,
                account_enabled=True,
            ),
            EntraUser(
                id="u3",
                display_name="Bob Jones",
                first_name="Bob",
                last_name="Jones",
                email="bjones@testfire.org",
                upn="bjones@testfire.org",
                employee_id=None,
                account_enabled=True,
                company_name="Test Fire Department",
            ),
        ]

        importer.user_manager.get_users = AsyncMock(return_value=existing_users)
        importer.user_manager.disable_user = AsyncMock(return_value=True)
        importer.user_manager.update_user = AsyncMock(return_value=True)

        result = await importer.import_members(members, dry_run=False, disable_inactive=True)

        assert len(result.disabled) == 2
        assert importer.user_manager.disable_user.call_count == 2
        disabled_names = [d["member"] for d in result.disabled]
        assert "John Smith" in disabled_names
        assert "Jane Doe" in disabled_names

    async def test_skips_inactive_members_not_in_entra(self, importer):
        """Inactive members without Entra accounts should be skipped, not created."""
        members = [
            Member(
                id="1",
                first_name="John",
                last_name="Smith",
                email="jsmith@testfire.org",
                status="Inactive",
            ),
            Member(
                id="2",
                first_name="Jane",
                last_name="Doe",
                email="jdoe@testfire.org",
                status="Inactive",
            ),
        ]

        importer.user_manager.get_users = AsyncMock(return_value=[])  # No existing users
        importer.user_manager.create_user = AsyncMock()

        result = await importer.import_members(members, dry_run=False, disable_inactive=True)

        importer.user_manager.create_user.assert_not_called()
        assert len(result.created) == 0
        assert len(result.skipped) == 2
        for skip in result.skipped:
            assert "inactive member" in skip["reason"]

    async def test_mixed_active_and_inactive_members(self, importer):
        """Test with mix of active/inactive members and various Entra states."""
        members = [
            # Inactive in Aladtec, active in Entra → should disable
            Member(
                id="1",
                first_name="Inactive",
                last_name="ToDisable",
                email="disable@testfire.org",
                status="Inactive",
            ),
            # Inactive in Aladtec, already disabled in Entra → should skip
            Member(
                id="2",
                first_name="Inactive",
                last_name="AlreadyDisabled",
                email="already@testfire.org",
                status="Inactive",
            ),
            # Inactive in Aladtec, not in Entra → should skip (not create)
            Member(
                id="3",
                first_name="Inactive",
                last_name="NotInEntra",
                email="notexist@testfire.org",
                status="Inactive",
            ),
            # Active in Aladtec, in Entra → should update/skip based on changes
            Member(
                id="4",
                first_name="Active",
                last_name="Member",
                email="active@testfire.org",
                status="Active",
            ),
        ]

        existing_users = [
            EntraUser(
                id="u1",
                display_name="Inactive ToDisable",
                first_name="Inactive",
                last_name="ToDisable",
                email="disable@testfire.org",
                upn="disable@testfire.org",
                employee_id=None,
                account_enabled=True,
            ),
            EntraUser(
                id="u2",
                display_name="Inactive AlreadyDisabled",
                first_name="Inactive",
                last_name="AlreadyDisabled",
                email="already@testfire.org",
                upn="already@testfire.org",
                employee_id=None,
                account_enabled=False,
            ),
            EntraUser(
                id="u4",
                display_name="Active Member",
                first_name="Active",
                last_name="Member",
                email="active@testfire.org",
                upn="active@testfire.org",
                employee_id=None,
                account_enabled=True,
                company_name="Test Fire Department",
            ),
        ]

        importer.user_manager.get_users = AsyncMock(return_value=existing_users)
        importer.user_manager.disable_user = AsyncMock(return_value=True)
        importer.user_manager.update_user = AsyncMock(return_value=True)

        result = await importer.import_members(members, dry_run=False, disable_inactive=True)

        # 1 disabled (Inactive ToDisable)
        assert len(result.disabled) == 1
        assert result.disabled[0]["member"] == "Inactive ToDisable"

        # 2 skipped (AlreadyDisabled + NotInEntra)
        skipped_reasons = {s["member"]: s["reason"] for s in result.skipped}
        assert "Inactive AlreadyDisabled" in skipped_reasons
        assert "already disabled" in skipped_reasons["Inactive AlreadyDisabled"]
        assert "Inactive NotInEntra" in skipped_reasons
        assert "inactive member" in skipped_reasons["Inactive NotInEntra"]

    async def test_dry_run_does_not_call_disable(self, importer):
        """Dry run should not actually disable any accounts."""
        members = [
            Member(
                id="1",
                first_name="John",
                last_name="Smith",
                email="jsmith@testfire.org",
                status="Inactive",
            ),
        ]

        existing_users = [
            EntraUser(
                id="u1",
                display_name="John Smith",
                first_name="John",
                last_name="Smith",
                email="jsmith@testfire.org",
                upn="jsmith@testfire.org",
                employee_id=None,
                account_enabled=True,
            ),
        ]

        importer.user_manager.get_users = AsyncMock(return_value=existing_users)
        importer.user_manager.disable_user = AsyncMock()

        result = await importer.import_members(members, dry_run=True, disable_inactive=True)

        importer.user_manager.disable_user.assert_not_called()
        assert len(result.disabled) == 1  # Reported as would-be-disabled


class TestMemberIsActiveProperty:
    """Tests for Member.is_active property edge cases in disable context."""

    def test_status_none_is_active(self):
        """Member with no status should be considered active."""
        member = Member(id="1", first_name="John", last_name="Doe", status=None)
        assert member.is_active is True

    def test_status_active_lowercase(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="active")
        assert member.is_active is True

    def test_status_active_uppercase(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="ACTIVE")
        assert member.is_active is True

    def test_status_inactive(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="Inactive")
        assert member.is_active is False

    def test_status_inactive_lowercase(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="inactive")
        assert member.is_active is False

    def test_status_other_value_is_inactive(self):
        """Any status other than 'active' or None should be inactive."""
        member = Member(id="1", first_name="John", last_name="Doe", status="On Leave")
        assert member.is_active is False

    def test_status_terminated_is_inactive(self):
        member = Member(id="1", first_name="John", last_name="Doe", status="Terminated")
        assert member.is_active is False


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
