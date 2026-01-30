"""Tests for sjifire.core.backup."""

import json

from sjifire.aladtec.models import Member
from sjifire.core.backup import (
    _member_to_dict,
    backup_aladtec_members,
    backup_entra_groups,
    backup_entra_users,
    get_backup_dir,
    list_backups,
)
from sjifire.entra.groups import EntraGroup
from sjifire.entra.users import EntraUser


class TestGetBackupDir:
    """Tests for get_backup_dir function."""

    def test_creates_directory(self, tmp_path):
        backup_dir = tmp_path / "new_backups"
        result = get_backup_dir(backup_dir)

        assert result == backup_dir
        assert backup_dir.exists()
        assert backup_dir.is_dir()

    def test_returns_existing_directory(self, tmp_path):
        backup_dir = tmp_path / "existing"
        backup_dir.mkdir()

        result = get_backup_dir(backup_dir)

        assert result == backup_dir

    def test_accepts_string_path(self, tmp_path):
        backup_dir = str(tmp_path / "string_path")
        result = get_backup_dir(backup_dir)

        assert result.exists()


class TestBackupAladtecMembers:
    """Tests for backup_aladtec_members function."""

    def test_creates_backup_file(self, temp_backup_dir):
        members = [
            Member(id="1", first_name="John", last_name="Doe"),
        ]

        filepath = backup_aladtec_members(members, temp_backup_dir)

        assert filepath.exists()
        assert filepath.suffix == ".json"
        assert "aladtec_members_" in filepath.name

    def test_backup_contains_correct_data(self, temp_backup_dir):
        members = [
            Member(
                id="1",
                first_name="John",
                last_name="Doe",
                email="john@example.com",
                positions=["Firefighter"],
            ),
        ]

        filepath = backup_aladtec_members(members, temp_backup_dir)

        with filepath.open() as f:
            data = json.load(f)

        assert data["backup_type"] == "aladtec_members"
        assert data["count"] == 1
        assert len(data["members"]) == 1
        assert data["members"][0]["first_name"] == "John"
        assert data["members"][0]["email"] == "john@example.com"

    def test_custom_prefix(self, temp_backup_dir):
        members = [Member(id="1", first_name="John", last_name="Doe")]

        filepath = backup_aladtec_members(members, temp_backup_dir, prefix="custom")

        assert "custom_members_" in filepath.name

    def test_empty_members_list(self, temp_backup_dir):
        filepath = backup_aladtec_members([], temp_backup_dir)

        with filepath.open() as f:
            data = json.load(f)

        assert data["count"] == 0
        assert data["members"] == []


class TestBackupEntraUsers:
    """Tests for backup_entra_users function."""

    def test_creates_backup_file(self, temp_backup_dir):
        users = [
            EntraUser(
                id="user-1",
                display_name="John Doe",
                first_name="John",
                last_name="Doe",
                email="john@sjifire.org",
                upn="john.doe@sjifire.org",
                employee_id="EMP001",
            ),
        ]

        filepath = backup_entra_users(users, temp_backup_dir)

        assert filepath.exists()
        assert "entra_users_" in filepath.name

    def test_backup_contains_correct_data(self, temp_backup_dir):
        users = [
            EntraUser(
                id="user-1",
                display_name="John Doe",
                first_name="John",
                last_name="Doe",
                email="john@sjifire.org",
                upn="john.doe@sjifire.org",
                employee_id="EMP001",
                account_enabled=True,
            ),
        ]

        filepath = backup_entra_users(users, temp_backup_dir)

        with filepath.open() as f:
            data = json.load(f)

        assert data["backup_type"] == "entra_users"
        assert data["count"] == 1
        assert data["users"][0]["display_name"] == "John Doe"
        assert data["users"][0]["upn"] == "john.doe@sjifire.org"


class TestMemberToDict:
    """Tests for _member_to_dict function."""

    def test_converts_all_fields(self):
        member = Member(
            id="EMP001",
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            phone="555-1234",
            home_phone="555-5678",
            employee_type="Captain",
            positions=["Firefighter", "EMT"],
            title="Captain",
            status="Active",
            work_group="A Shift",
            pay_profile="Career",
            employee_id="EMP001",
            station_assignment="Station 1",
            evip="Yes",
            date_hired="2020-01-15",
        )

        result = _member_to_dict(member)

        assert result["id"] == "EMP001"
        assert result["first_name"] == "John"
        assert result["last_name"] == "Doe"
        assert result["display_name"] == "John Doe"
        assert result["email"] == "john@example.com"
        assert result["positions"] == ["Firefighter", "EMT"]
        assert result["is_active"] is True

    def test_handles_none_values(self):
        member = Member(id="1", first_name="John", last_name="Doe")

        result = _member_to_dict(member)

        assert result["email"] is None
        assert result["phone"] is None
        assert result["positions"] == []


class TestListBackups:
    """Tests for list_backups function."""

    def test_lists_json_files(self, temp_backup_dir):
        # Create some backup files
        (temp_backup_dir / "backup1.json").write_text("{}")
        (temp_backup_dir / "backup2.json").write_text("{}")
        (temp_backup_dir / "not_backup.txt").write_text("")

        backups = list_backups(temp_backup_dir)

        assert len(backups) == 2
        assert all(b.suffix == ".json" for b in backups)

    def test_sorted_by_modification_time(self, temp_backup_dir):
        import time

        # Create files with different modification times
        file1 = temp_backup_dir / "older.json"
        file1.write_text("{}")
        time.sleep(0.1)

        file2 = temp_backup_dir / "newer.json"
        file2.write_text("{}")

        backups = list_backups(temp_backup_dir)

        # Newest first
        assert backups[0].name == "newer.json"
        assert backups[1].name == "older.json"

    def test_empty_directory(self, temp_backup_dir):
        backups = list_backups(temp_backup_dir)
        assert backups == []


class TestBackupEntraGroups:
    """Tests for backup_entra_groups function."""

    def test_creates_backup_file(self, temp_backup_dir):
        groups = [
            EntraGroup(
                id="group-1",
                display_name="Station 31",
                description="Members at Station 31",
                mail="station31@sjifire.org",
                mail_enabled=True,
                security_enabled=False,
                group_types=["Unified"],
            ),
        ]

        filepath = backup_entra_groups(groups, backup_dir=temp_backup_dir)

        assert filepath.exists()
        assert "entra_groups_" in filepath.name

    def test_backup_contains_correct_data(self, temp_backup_dir):
        groups = [
            EntraGroup(
                id="group-1",
                display_name="Station 31",
                description="Members at Station 31",
                mail="station31@sjifire.org",
                mail_enabled=True,
                security_enabled=False,
                group_types=["Unified"],
            ),
        ]

        filepath = backup_entra_groups(groups, backup_dir=temp_backup_dir)

        with filepath.open() as f:
            data = json.load(f)

        assert data["backup_type"] == "entra_groups"
        assert data["count"] == 1
        assert data["groups"][0]["display_name"] == "Station 31"
        assert data["groups"][0]["mail"] == "station31@sjifire.org"
        assert data["groups"][0]["group_type"] == "microsoft365"

    def test_backup_includes_memberships(self, temp_backup_dir):
        groups = [
            EntraGroup(
                id="group-1",
                display_name="Station 31",
                description=None,
                mail="station31@sjifire.org",
                mail_enabled=True,
                security_enabled=False,
                group_types=["Unified"],
            ),
        ]
        memberships = {"group-1": ["user-1", "user-2", "user-3"]}

        filepath = backup_entra_groups(groups, memberships=memberships, backup_dir=temp_backup_dir)

        with filepath.open() as f:
            data = json.load(f)

        assert data["groups"][0]["members"] == ["user-1", "user-2", "user-3"]

    def test_backup_without_memberships(self, temp_backup_dir):
        groups = [
            EntraGroup(
                id="group-1",
                display_name="Station 31",
                description=None,
                mail="station31@sjifire.org",
                mail_enabled=True,
                security_enabled=False,
                group_types=["Unified"],
            ),
        ]

        filepath = backup_entra_groups(groups, backup_dir=temp_backup_dir)

        with filepath.open() as f:
            data = json.load(f)

        assert "members" not in data["groups"][0]

    def test_custom_prefix(self, temp_backup_dir):
        groups = [
            EntraGroup(
                id="group-1",
                display_name="Station 31",
                description=None,
                mail=None,
                mail_enabled=False,
                security_enabled=True,
                group_types=[],
            ),
        ]

        filepath = backup_entra_groups(groups, backup_dir=temp_backup_dir, prefix="station")

        assert "station_groups_" in filepath.name
