"""Tests for scripts/ispyfire_sync.py - iSpyFire sync CLI script."""

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.scripts.ispyfire_sync import (
    backup_ispyfire_people,
    main,
    print_comparison_report,
    run_sync,
)

# Test path for mocked project root (not a real temp directory)
TEST_PROJECT_ROOT = Path("/mock/project/root")


# =============================================================================
# Mock Data Classes
# =============================================================================


@dataclass
class MockISpyFirePerson:
    """Mock iSpyFire person for testing."""

    id: str
    first_name: str
    last_name: str
    email: str
    cell_phone: str | None = None
    title: str | None = None
    is_active: bool = True
    is_login_active: bool = True
    group_set_acls: list | None = None

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass
class MockEntraUser:
    """Mock Entra user for testing."""

    id: str
    first_name: str | None
    last_name: str | None
    email: str | None
    display_name: str | None = None
    mobile_phone: str | None = None
    extension_attribute1: str | None = None  # Rank
    extension_attribute3: str | None = None  # Positions


@dataclass
class MockComparison:
    """Mock comparison result for testing."""

    entra_operational: list
    ispyfire_people: list
    matched: list
    to_add: list
    to_update: list
    to_remove: list
    skipped_no_phone: list
    skipped_no_operational: list


# =============================================================================
# Test backup_ispyfire_people
# =============================================================================


class TestBackupIspyfirePeople:
    """Tests for backup_ispyfire_people function."""

    def test_creates_backup_directory(self, tmp_path):
        """Should create backup directory if it doesn't exist."""
        backup_dir = tmp_path / "nonexistent" / "backups"
        person = MockISpyFirePerson(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
        )

        backup_ispyfire_people([person], backup_dir)

        assert backup_dir.exists()

    def test_creates_backup_file(self, tmp_path):
        """Should create a backup file with timestamp."""
        person = MockISpyFirePerson(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
        )

        result = backup_ispyfire_people([person], tmp_path)

        assert result.exists()
        assert result.name.startswith("ispyfire_people_")
        assert result.suffix == ".json"

    def test_backup_contains_person_data(self, tmp_path):
        """Should serialize person data to JSON."""
        person = MockISpyFirePerson(
            id="123",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            cell_phone="555-1234",
            title="Captain",
            is_active=True,
            is_login_active=True,
            group_set_acls=["group1"],
        )

        result = backup_ispyfire_people([person], tmp_path)

        with result.open() as f:
            data = json.load(f)

        assert len(data) == 1
        assert data[0]["id"] == "123"
        assert data[0]["firstName"] == "John"
        assert data[0]["lastName"] == "Doe"
        assert data[0]["email"] == "john@example.com"
        assert data[0]["cellPhone"] == "555-1234"
        assert data[0]["title"] == "Captain"
        assert data[0]["isActive"] is True
        assert data[0]["isLoginActive"] is True
        assert data[0]["groupSetACLs"] == ["group1"]

    def test_backup_multiple_people(self, tmp_path):
        """Should backup multiple people."""
        people = [
            MockISpyFirePerson(
                id="1", first_name="John", last_name="Doe", email="john@example.com"
            ),
            MockISpyFirePerson(
                id="2", first_name="Jane", last_name="Smith", email="jane@example.com"
            ),
        ]

        result = backup_ispyfire_people(people, tmp_path)

        with result.open() as f:
            data = json.load(f)

        assert len(data) == 2

    def test_returns_backup_path(self, tmp_path):
        """Should return the path to the backup file."""
        person = MockISpyFirePerson(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
        )

        result = backup_ispyfire_people([person], tmp_path)

        assert isinstance(result, Path)
        assert result.parent == tmp_path


# =============================================================================
# Test print_comparison_report
# =============================================================================


class TestPrintComparisonReport:
    """Tests for print_comparison_report function."""

    def test_prints_summary(self, capsys):
        """Should print summary section."""
        comparison = MockComparison(
            entra_operational=[],
            ispyfire_people=[],
            matched=[],
            to_add=[],
            to_update=[],
            to_remove=[],
            skipped_no_phone=[],
            skipped_no_operational=[],
        )

        print_comparison_report(comparison)
        captured = capsys.readouterr()

        assert "SUMMARY:" in captured.out
        assert "Entra operational users:" in captured.out
        assert "iSpyFire people:" in captured.out

    def test_prints_to_add_section(self, capsys):
        """Should print TO ADD section when there are users to add."""
        user = MockEntraUser(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            mobile_phone="555-1234",
            extension_attribute1="Captain",
        )
        comparison = MockComparison(
            entra_operational=[user],
            ispyfire_people=[],
            matched=[],
            to_add=[user],
            to_update=[],
            to_remove=[],
            skipped_no_phone=[],
            skipped_no_operational=[],
        )

        print_comparison_report(comparison)
        captured = capsys.readouterr()

        assert "TO ADD TO ISPYFIRE" in captured.out
        assert "john@example.com" in captured.out

    def test_prints_to_remove_section(self, capsys):
        """Should print TO REMOVE section when there are people to remove."""
        person = MockISpyFirePerson(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
        )
        comparison = MockComparison(
            entra_operational=[],
            ispyfire_people=[person],
            matched=[],
            to_add=[],
            to_update=[],
            to_remove=[person],
            skipped_no_phone=[],
            skipped_no_operational=[],
        )

        print_comparison_report(comparison)
        captured = capsys.readouterr()

        assert "TO REMOVE FROM ISPYFIRE" in captured.out
        assert "John Doe" in captured.out

    def test_prints_skipped_no_phone(self, capsys):
        """Should print skipped users with no phone."""
        user = MockEntraUser(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
        )
        comparison = MockComparison(
            entra_operational=[user],
            ispyfire_people=[],
            matched=[],
            to_add=[],
            to_update=[],
            to_remove=[],
            skipped_no_phone=[user],
            skipped_no_operational=[],
        )

        print_comparison_report(comparison)
        captured = capsys.readouterr()

        assert "SKIPPED - NO CELL PHONE" in captured.out


# =============================================================================
# Test run_sync
# =============================================================================


class TestRunSync:
    """Tests for run_sync function."""

    @pytest.mark.asyncio
    @patch("sjifire.scripts.ispyfire_sync.ISpyFireClient")
    @patch("sjifire.scripts.ispyfire_sync.EntraUserManager")
    @patch("sjifire.scripts.ispyfire_sync.compare_entra_to_ispyfire")
    @patch("sjifire.scripts.ispyfire_sync.get_project_root")
    async def test_dry_run_returns_zero(
        self, mock_root, mock_compare, mock_entra_manager, mock_ispy_client
    ):
        """Dry run should return 0 and make no changes."""
        mock_root.return_value = TEST_PROJECT_ROOT

        # Mock Entra
        mock_manager = AsyncMock()
        mock_manager.get_employees.return_value = []
        mock_entra_manager.return_value = mock_manager

        # Mock iSpyFire
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get_people.return_value = []
        mock_ispy_client.return_value = mock_client

        # Mock comparison
        mock_compare.return_value = MockComparison(
            entra_operational=[],
            ispyfire_people=[],
            matched=[],
            to_add=[],
            to_update=[],
            to_remove=[],
            skipped_no_phone=[],
            skipped_no_operational=[],
        )

        result = await run_sync(dry_run=True)

        assert result == 0
        mock_client.create_and_invite.assert_not_called()

    @pytest.mark.asyncio
    @patch("sjifire.scripts.ispyfire_sync.ISpyFireClient")
    @patch("sjifire.scripts.ispyfire_sync.EntraUserManager")
    @patch("sjifire.scripts.ispyfire_sync.compare_entra_to_ispyfire")
    @patch("sjifire.scripts.ispyfire_sync.get_project_root")
    async def test_single_email_filters_users(
        self, mock_root, mock_compare, mock_entra_manager, mock_ispy_client
    ):
        """Single email mode should filter to just that user."""
        mock_root.return_value = TEST_PROJECT_ROOT

        # Mock Entra with multiple users
        user1 = MockEntraUser(id="1", first_name="John", last_name="Doe", email="john@example.com")
        user2 = MockEntraUser(
            id="2", first_name="Jane", last_name="Smith", email="jane@example.com"
        )
        mock_manager = AsyncMock()
        mock_manager.get_employees.return_value = [user1, user2]
        mock_entra_manager.return_value = mock_manager

        # Mock iSpyFire
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get_people.return_value = []
        mock_ispy_client.return_value = mock_client

        # Mock comparison
        mock_compare.return_value = MockComparison(
            entra_operational=[user1],
            ispyfire_people=[],
            matched=[],
            to_add=[],
            to_update=[],
            to_remove=[],
            skipped_no_phone=[],
            skipped_no_operational=[],
        )

        await run_sync(dry_run=True, single_email="john@example.com")

        # Verify comparison was called with filtered list
        call_args = mock_compare.call_args[0]
        assert len(call_args[0]) == 1
        assert call_args[0][0].email == "john@example.com"

    @pytest.mark.asyncio
    @patch("sjifire.scripts.ispyfire_sync.ISpyFireClient")
    @patch("sjifire.scripts.ispyfire_sync.EntraUserManager")
    @patch("sjifire.scripts.ispyfire_sync.get_project_root")
    async def test_single_email_not_found_returns_error(
        self, mock_root, mock_entra_manager, mock_ispy_client
    ):
        """Single email mode should return 1 if user not found."""
        mock_root.return_value = TEST_PROJECT_ROOT

        # Mock Entra with no matching user
        mock_manager = AsyncMock()
        mock_manager.get_employees.return_value = []
        mock_entra_manager.return_value = mock_manager

        result = await run_sync(dry_run=True, single_email="notfound@example.com")

        assert result == 1

    @pytest.mark.asyncio
    @patch("sjifire.scripts.ispyfire_sync.entra_user_to_ispyfire_person")
    @patch("sjifire.scripts.ispyfire_sync.ISpyFireClient")
    @patch("sjifire.scripts.ispyfire_sync.EntraUserManager")
    @patch("sjifire.scripts.ispyfire_sync.compare_entra_to_ispyfire")
    @patch("sjifire.scripts.ispyfire_sync.get_project_root")
    async def test_creates_new_users(
        self, mock_root, mock_compare, mock_entra_manager, mock_ispy_client, mock_convert
    ):
        """Should create new users when not in dry-run mode."""
        mock_root.return_value = TEST_PROJECT_ROOT

        # Mock Entra
        user = MockEntraUser(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            mobile_phone="555-1234",
        )
        mock_manager = AsyncMock()
        mock_manager.get_employees.return_value = [user]
        mock_entra_manager.return_value = mock_manager

        # Mock iSpyFire
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get_people.return_value = []
        mock_client.create_and_invite.return_value = MockISpyFirePerson(
            id="new-1", first_name="John", last_name="Doe", email="john@example.com"
        )
        mock_ispy_client.return_value = mock_client

        # Mock comparison with user to add
        mock_compare.return_value = MockComparison(
            entra_operational=[user],
            ispyfire_people=[],
            matched=[],
            to_add=[user],
            to_update=[],
            to_remove=[],
            skipped_no_phone=[],
            skipped_no_operational=[],
        )

        # Mock conversion
        mock_person = MockISpyFirePerson(
            id="", first_name="John", last_name="Doe", email="john@example.com"
        )
        mock_convert.return_value = mock_person

        result = await run_sync(dry_run=False)

        assert result == 0
        mock_client.create_and_invite.assert_called_once()

    @pytest.mark.asyncio
    @patch("sjifire.scripts.ispyfire_sync.backup_ispyfire_people")
    @patch("sjifire.scripts.ispyfire_sync.ISpyFireClient")
    @patch("sjifire.scripts.ispyfire_sync.EntraUserManager")
    @patch("sjifire.scripts.ispyfire_sync.compare_entra_to_ispyfire")
    @patch("sjifire.scripts.ispyfire_sync.get_project_root")
    async def test_deactivates_removed_users(
        self, mock_root, mock_compare, mock_entra_manager, mock_ispy_client, mock_backup
    ):
        """Should deactivate users not in Entra."""
        mock_root.return_value = TEST_PROJECT_ROOT
        mock_backup.return_value = Path("/mock/backup.json")

        # Mock Entra (empty)
        mock_manager = AsyncMock()
        mock_manager.get_employees.return_value = []
        mock_entra_manager.return_value = mock_manager

        # Mock iSpyFire with existing person
        person = MockISpyFirePerson(
            id="1", first_name="John", last_name="Doe", email="john@example.com"
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get_people.return_value = [person]
        mock_client.deactivate_person.return_value = True
        mock_ispy_client.return_value = mock_client

        # Mock comparison with person to remove
        mock_compare.return_value = MockComparison(
            entra_operational=[],
            ispyfire_people=[person],
            matched=[],
            to_add=[],
            to_update=[],
            to_remove=[person],
            skipped_no_phone=[],
            skipped_no_operational=[],
        )

        result = await run_sync(dry_run=False)

        assert result == 0
        mock_client.deactivate_person.assert_called_once_with("1", email="john@example.com")


# =============================================================================
# Test main (CLI)
# =============================================================================


class TestMain:
    """Tests for main CLI function."""

    @patch("asyncio.run")
    def test_dry_run_flag(self, mock_async_run):
        """--dry-run flag should set dry_run=True."""
        mock_async_run.return_value = 0

        with patch("sys.argv", ["ispyfire-sync", "--dry-run"]):
            main()

        mock_async_run.assert_called_once()

    @patch("asyncio.run")
    def test_email_flag(self, mock_async_run):
        """--email flag should filter to single user."""
        mock_async_run.return_value = 0

        with patch("sys.argv", ["ispyfire-sync", "--email", "test@example.com"]):
            main()

        mock_async_run.assert_called_once()

    def test_invalid_email_returns_error(self):
        """Invalid email should return exit code 1."""
        with patch("sys.argv", ["ispyfire-sync", "--email", "not-an-email"]):
            result = main()

        assert result == 1

    @patch("asyncio.run")
    def test_verbose_flag_sets_debug_level(self, mock_async_run):
        """--verbose flag should set DEBUG log level."""
        mock_async_run.return_value = 0

        with patch("sys.argv", ["ispyfire-sync", "--verbose", "--dry-run"]):
            main()

        mock_async_run.assert_called_once()


# =============================================================================
# Test edge cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_backup_empty_list(self, tmp_path):
        """Should handle empty people list."""
        result = backup_ispyfire_people([], tmp_path)

        with result.open() as f:
            data = json.load(f)

        assert data == []

    def test_backup_person_with_none_fields(self, tmp_path):
        """Should handle person with None fields."""
        person = MockISpyFirePerson(
            id="1",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            cell_phone=None,
            title=None,
            group_set_acls=None,
        )

        result = backup_ispyfire_people([person], tmp_path)

        with result.open() as f:
            data = json.load(f)

        assert data[0]["cellPhone"] is None
        assert data[0]["title"] is None
        assert data[0]["groupSetACLs"] is None
