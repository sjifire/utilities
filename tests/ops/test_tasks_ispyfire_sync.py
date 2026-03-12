"""Tests for iSpyFire sync background task."""

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.ops.tasks.ispyfire_sync import ispyfire_sync


# ---------------------------------------------------------------------------
# Mock data classes
# ---------------------------------------------------------------------------


@dataclass
class _MockEntraUser:
    """Lightweight stand-in for EntraUser with the fields ispyfire_sync reads."""

    id: str = "entra-1"
    email: str = "alice@sjifire.org"
    first_name: str = "Alice"
    last_name: str = "Smith"
    display_name: str = "Alice Smith"
    mobile_phone: str = "360-555-0001"
    extension_attribute1: str = "Firefighter"
    extension_attribute3: str = "Firefighter"
    extension_attribute4: str = ""
    upn: str = "alice.smith@sjifire.org"
    employee_id: str = "E001"
    account_enabled: bool = True
    job_title: str | None = None
    business_phones: list[str] | None = None
    office_location: str | None = None
    employee_hire_date: str | None = None
    employee_type: str | None = None
    personal_email: str | None = None
    department: str | None = None
    company_name: str | None = None
    extension_attribute2: str | None = None

    @property
    def positions(self):
        if not self.extension_attribute3:
            return set()
        return {p.strip() for p in self.extension_attribute3.split(",") if p.strip()}

    @property
    def schedules(self):
        if not self.extension_attribute4:
            return set()
        return {s.strip() for s in self.extension_attribute4.split(",") if s.strip()}


@dataclass
class _MockISpyFirePerson:
    """Lightweight stand-in for ISpyFirePerson."""

    id: str = "ispy-1"
    first_name: str = "Alice"
    last_name: str = "Smith"
    email: str | None = "alice@sjifire.org"
    cell_phone: str | None = "360-555-0001"
    title: str | None = "Firefighter"
    is_active: bool = True
    is_login_active: bool = True
    is_utility: bool = False
    group_set_acls: list[str] = field(default_factory=list)
    responder_types: list[str] = field(default_factory=list)
    message_email: bool = False
    message_cell: bool = False

    @property
    def display_name(self):
        return f"{self.first_name} {self.last_name}"


@dataclass
class _MockComparison:
    """Stand-in for SyncComparison with only the fields the task reads."""

    to_add: list = field(default_factory=list)
    to_update: list = field(default_factory=list)
    to_remove: list = field(default_factory=list)
    matched: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TASK_MODULE = "sjifire.ops.tasks.ispyfire_sync"


def _patch_all(*, employees=None, people=None, comparison=None):
    """Return a combined context manager that patches all external deps."""
    if employees is None:
        employees = []
    if people is None:
        people = []
    if comparison is None:
        comparison = _MockComparison()

    mock_user_mgr = MagicMock()
    mock_user_mgr.get_employees = AsyncMock(return_value=employees)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_people = MagicMock(return_value=people)
    mock_client.get_person_by_email = MagicMock(return_value=None)
    mock_client.create_and_invite = MagicMock(return_value=_MockISpyFirePerson(id="new-1"))
    mock_client.update_person = MagicMock(return_value=True)
    mock_client.reactivate_person = MagicMock(return_value=True)
    mock_client.deactivate_person = MagicMock(return_value=True)

    class _Patches:
        def __init__(self):
            self.user_mgr = mock_user_mgr
            self.client = mock_client
            self.comparison = comparison
            self._patches = [
                patch("sjifire.entra.users.EntraUserManager", return_value=mock_user_mgr),
                patch("sjifire.ispyfire.client.ISpyFireClient", return_value=mock_client),
                patch("sjifire.ispyfire.sync.compare_entra_to_ispyfire", return_value=comparison),
                patch("sjifire.ispyfire.sync.entra_user_to_ispyfire_person", return_value=_MockISpyFirePerson()),
                patch("sjifire.ispyfire.sync.get_responder_types", return_value=["FF"]),
            ]

        def __enter__(self):
            for p in self._patches:
                p.__enter__()
            return self

        def __exit__(self, *args):
            for p in reversed(self._patches):
                p.__exit__(*args)

    return _Patches()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestISpyFireSync:
    async def test_no_changes_needed(self):
        """Returns 0 when comparison shows no changes and no inactive matched."""
        with _patch_all() as ctx:
            result = await ispyfire_sync()
        assert result == 0

    async def test_add_new_users(self):
        """Creates new users via create_and_invite, counts successes."""
        user1 = _MockEntraUser(email="new1@sjifire.org")
        user2 = _MockEntraUser(email="new2@sjifire.org")
        comparison = _MockComparison(to_add=[user1, user2])

        with _patch_all(employees=[user1, user2], comparison=comparison) as ctx:
            result = await ispyfire_sync()

        assert result == 2
        assert ctx.client.create_and_invite.call_count == 2

    async def test_add_existing_reactivates(self):
        """When to_add user already exists in iSpyFire, reactivates instead."""
        user = _MockEntraUser(email="exists@sjifire.org")
        comparison = _MockComparison(to_add=[user])

        existing = _MockISpyFirePerson(id="old-1", email="exists@sjifire.org", is_active=False)

        with _patch_all(employees=[user], comparison=comparison) as ctx:
            ctx.client.get_person_by_email = MagicMock(return_value=existing)
            result = await ispyfire_sync()

        assert result == 1
        ctx.client.reactivate_person.assert_called_once()
        ctx.client.create_and_invite.assert_not_called()

    async def test_update_users(self):
        """Updates users with changed fields."""
        user = _MockEntraUser(first_name="Updated")
        person = _MockISpyFirePerson(first_name="Old")
        comparison = _MockComparison(to_update=[(user, person)])

        with _patch_all(employees=[user], comparison=comparison) as ctx:
            result = await ispyfire_sync()

        assert result == 1
        ctx.client.update_person.assert_called_once()

    async def test_reactivate_matched_inactive(self):
        """Reactivates matched users that are inactive in iSpyFire."""
        user = _MockEntraUser()
        person = _MockISpyFirePerson(is_active=False)
        comparison = _MockComparison(matched=[(user, person)])

        with _patch_all(employees=[user], comparison=comparison) as ctx:
            result = await ispyfire_sync()

        assert result == 1
        ctx.client.reactivate_person.assert_called_once()

    async def test_deactivate_removed(self):
        """Deactivates people removed from Entra."""
        person1 = _MockISpyFirePerson(id="rm-1")
        person2 = _MockISpyFirePerson(id="rm-2")
        comparison = _MockComparison(to_remove=[person1, person2])

        with _patch_all(comparison=comparison) as ctx:
            result = await ispyfire_sync()

        assert result == 2
        assert ctx.client.deactivate_person.call_count == 2

    async def test_mixed_changes(self):
        """Handles adds + updates + reactivations + deactivations together."""
        new_user = _MockEntraUser(email="new@sjifire.org")
        upd_user = _MockEntraUser(email="upd@sjifire.org")
        upd_person = _MockISpyFirePerson(id="upd-1")
        matched_user = _MockEntraUser()
        inactive_person = _MockISpyFirePerson(is_active=False)
        removed = _MockISpyFirePerson(id="rm-1")

        comparison = _MockComparison(
            to_add=[new_user],
            to_update=[(upd_user, upd_person)],
            to_remove=[removed],
            matched=[(matched_user, inactive_person)],
        )

        with _patch_all(
            employees=[new_user, upd_user, matched_user],
            comparison=comparison,
        ) as ctx:
            result = await ispyfire_sync()

        # 1 add + 1 update + 1 reactivate (matched inactive) + 1 deactivate = 4
        assert result == 4
