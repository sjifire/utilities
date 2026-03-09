"""Tests for sjifire.scripts.signature_sync."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.entra.users import EntraUser
from sjifire.scripts.signature_sync import (
    _format_phone,
    _get_phone_line,
    _get_title_line,
    load_template,
    remove_transport_rule,
    sync_custom_attributes,
)

OFFICE_PHONE = "(360) 378-5334"


@pytest.fixture
def template():
    """Load the default signature template."""
    return load_template("default")


def make_user(
    display_name="John Doe",
    first_name="John",
    last_name="Doe",
    email="john@sjifire.org",
    rank=None,
    job_title=None,
    mobile_phone=None,
) -> EntraUser:
    """Create a test EntraUser."""
    return EntraUser(
        id="123",
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        email=email,
        upn=email,
        employee_id="EMP001",
        extension_attribute1=rank,
        job_title=job_title,
        mobile_phone=mobile_phone,
    )


class TestFormatPhone:
    """Tests for _format_phone function."""

    def test_ten_digit_raw(self):
        assert _format_phone("3603177060") == "(360) 317-7060"

    def test_already_formatted(self):
        assert _format_phone("(360) 555-1234") == "(360) 555-1234"

    def test_eleven_digit_with_country_code(self):
        assert _format_phone("13605551234") == "(360) 555-1234"

    def test_dashes_only(self):
        assert _format_phone("360-317-7060") == "(360) 317-7060"

    def test_dots(self):
        assert _format_phone("360.317.7060") == "(360) 317-7060"

    def test_short_number_returned_as_is(self):
        assert _format_phone("5551234") == "5551234"

    def test_international_returned_as_is(self):
        assert _format_phone("+44 20 7946 0958") == "+44 20 7946 0958"


class TestGetTitleLine:
    """Tests for _get_title_line function."""

    def test_rank_and_job_title(self):
        user = make_user(rank="Captain", job_title="Training Officer")
        assert _get_title_line(user) == "Captain - Training Officer"

    def test_rank_only(self):
        user = make_user(rank="Captain")
        assert _get_title_line(user) == "Captain"

    def test_job_title_only(self):
        user = make_user(job_title="Executive Assistant")
        assert _get_title_line(user) == "Executive Assistant"

    def test_rank_equals_job_title_no_duplicate(self):
        user = make_user(rank="Administrative Assistant", job_title="Administrative Assistant")
        assert _get_title_line(user) == "Administrative Assistant"

    def test_neither(self):
        user = make_user()
        assert _get_title_line(user) == ""

    def test_strips_br_from_corrupted_rank(self):
        user = make_user(rank="Captain<br>")
        assert _get_title_line(user) == "Captain"

    def test_strips_br_from_corrupted_rank_with_job_title(self):
        user = make_user(rank="Lieutenant<br>", job_title="Marine Coordinator")
        assert _get_title_line(user) == "Lieutenant - Marine Coordinator"


class TestGetPhoneLine:
    """Tests for _get_phone_line function."""

    def test_office_only(self):
        user = make_user()
        assert _get_phone_line(user, OFFICE_PHONE) == f"Office: {OFFICE_PHONE}"

    def test_with_cell(self):
        user = make_user(mobile_phone="(360) 555-1234")
        assert (
            _get_phone_line(user, OFFICE_PHONE) == f"Office: {OFFICE_PHONE} | Cell: (360) 555-1234"
        )

    def test_raw_cell_gets_formatted(self):
        user = make_user(mobile_phone="3603177060")
        assert (
            _get_phone_line(user, OFFICE_PHONE) == f"Office: {OFFICE_PHONE} | Cell: (360) 317-7060"
        )


class TestSyncCustomAttributes:
    """Tests for sync_custom_attributes function."""

    def test_dry_run_returns_success(self, template):
        users = [make_user(rank="Captain")]
        success, failure, errors = sync_custom_attributes(users, template, dry_run=True)
        assert success == len(users)
        assert failure == 0
        assert errors == []

    def test_dry_run_remove(self, template):
        users = [make_user()]
        success, failure, _errors = sync_custom_attributes(
            users, template, dry_run=True, remove=True
        )
        assert success == len(users)
        assert failure == 0

    def test_calls_powershell_batch(self, template):
        users = [
            make_user(display_name="User 1", email="u1@sjifire.org", rank="Captain"),
            make_user(display_name="User 2", email="u2@sjifire.org", job_title="Admin"),
        ]

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = {
                "Success": 2,
                "Failure": 0,
                "Errors": [],
            }
            mock_cls.return_value = mock_client

            success, failure, errors = sync_custom_attributes(users, template, dry_run=False)

        assert success == 2
        assert failure == 0
        assert errors == []
        mock_client._run_powershell.assert_called_once()

        # Verify the script contains Set-Mailbox for both users
        commands = mock_client._run_powershell.call_args[0][0]
        script = " ".join(commands)
        assert "u1@sjifire.org" in script
        assert "u2@sjifire.org" in script
        assert "CustomAttribute6" in script
        assert "CustomAttribute7" in script
        assert "CustomAttribute8" in script
        assert "Captain" in script
        assert "Admin" in script

    def test_attr1_includes_br_for_titled_user(self, template):
        users = [make_user(email="titled@sjifire.org", rank="Captain")]

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = {
                "Success": 1,
                "Failure": 0,
                "Errors": [],
            }
            mock_cls.return_value = mock_client

            sync_custom_attributes(users, template, dry_run=False)

        commands = mock_client._run_powershell.call_args[0][0]
        script = " ".join(commands)
        assert "Captain<br>" in script

    def test_attr1_empty_for_untitled_user(self, template):
        users = [make_user(email="notitled@sjifire.org")]

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = {
                "Success": 1,
                "Failure": 0,
                "Errors": [],
            }
            mock_cls.return_value = mock_client

            sync_custom_attributes(users, template, dry_run=False)

        commands = mock_client._run_powershell.call_args[0][0]
        script = " ".join(commands)
        assert "-CustomAttribute6 ''" in script
        assert "-CustomAttribute8 ''" in script

    def test_handles_failures(self, template):
        users = [make_user()]

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = {
                "Success": 0,
                "Failure": 1,
                "Errors": "john@sjifire.org: mailbox not found",
            }
            mock_cls.return_value = mock_client

            success, failure, errors = sync_custom_attributes(users, template, dry_run=False)

        assert success == 0
        assert failure == 1
        assert len(errors) == 1

    def test_handles_script_failure(self, template):
        users = [make_user()]

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = None
            mock_cls.return_value = mock_client

            success, failure, _errors = sync_custom_attributes(users, template, dry_run=False)

        assert success == 0
        assert failure == len(users)


class TestSyncTransportRule:
    """Tests for sync_transport_rule function."""

    def test_dry_run(self, template):
        from sjifire.scripts.signature_sync import sync_transport_rule

        ok, error = sync_transport_rule(template, dry_run=True)
        assert ok is True
        assert error is None

    def test_creates_rule(self, template):
        from sjifire.scripts.signature_sync import sync_transport_rule

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = "Creating new rule: SJIFR\nSUCCESS"
            mock_cls.return_value = mock_client

            ok, error = sync_transport_rule(template, dry_run=False)

        assert ok is True
        assert error is None

    def test_handles_failure(self, template):
        from sjifire.scripts.signature_sync import sync_transport_rule

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = None
            mock_cls.return_value = mock_client

            ok, error = sync_transport_rule(template, dry_run=False)

        assert ok is False
        assert "Failed" in error


class TestRemoveTransportRule:
    """Tests for remove_transport_rule function."""

    def test_dry_run(self, template):
        ok, error = remove_transport_rule(template, dry_run=True)
        assert ok is True
        assert error is None

    def test_removes_rule(self, template):
        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = "REMOVED: SJIFR Email Signature"
            mock_cls.return_value = mock_client

            ok, error = remove_transport_rule(template, dry_run=False)

        assert ok is True
        assert error is None

    def test_handles_not_found(self, template):
        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = "NOT_FOUND: SJIFR Email Signature"
            mock_cls.return_value = mock_client

            ok, error = remove_transport_rule(template, dry_run=False)

        assert ok is True
        assert error is None

    def test_handles_failure(self, template):
        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_cls:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = None
            mock_cls.return_value = mock_client

            ok, error = remove_transport_rule(template, dry_run=False)

        assert ok is False
        assert "Failed" in error


class TestRunSync:
    """Tests for run_sync function."""

    async def test_preview_requires_email(self):
        from sjifire.scripts.signature_sync import run_sync

        exit_code = await run_sync(preview=True, email=None)
        assert exit_code == 1

    async def test_preview_mode(self):
        from sjifire.scripts.signature_sync import run_sync

        mock_user = make_user(email="test@sjifire.org", rank="Captain")

        with patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.get_employees = AsyncMock(return_value=[mock_user])
            mock_mgr_cls.return_value = mock_mgr

            exit_code = await run_sync(preview=True, email="test@sjifire.org")

        assert exit_code == 0

    async def test_filters_to_sjifire_domain(self):
        from sjifire.scripts.signature_sync import run_sync

        users = [
            make_user(email="user1@sjifire.org"),
            make_user(email="user2@otherdomain.com"),
        ]

        with (
            patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_mgr_cls,
            patch("sjifire.scripts.signature_sync.sync_custom_attributes") as mock_sync,
            patch("sjifire.scripts.signature_sync.sync_transport_rule") as mock_rule,
            patch("sjifire.scripts.signature_sync.ensure_trusted_domain") as mock_trusted,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_employees = AsyncMock(return_value=users)
            mock_mgr_cls.return_value = mock_mgr
            mock_sync.return_value = (1, 0, [])
            mock_rule.return_value = (True, None)
            mock_trusted.return_value = (1, 0, [])

            await run_sync(dry_run=False)

        synced_users = mock_sync.call_args[0][0]
        assert len(synced_users) == 1
        assert synced_users[0].email == "user1@sjifire.org"

    async def test_returns_error_when_user_not_found(self):
        from sjifire.scripts.signature_sync import run_sync

        with patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.get_employees = AsyncMock(return_value=[])
            mock_mgr_cls.return_value = mock_mgr

            exit_code = await run_sync(email="notfound@sjifire.org")

        assert exit_code == 1

    async def test_returns_success(self):
        from sjifire.scripts.signature_sync import run_sync

        users = [make_user()]

        with (
            patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_mgr_cls,
            patch("sjifire.scripts.signature_sync.sync_custom_attributes") as mock_sync,
            patch("sjifire.scripts.signature_sync.sync_transport_rule") as mock_rule,
            patch("sjifire.scripts.signature_sync.ensure_trusted_domain") as mock_trusted,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_employees = AsyncMock(return_value=users)
            mock_mgr_cls.return_value = mock_mgr
            mock_sync.return_value = (1, 0, [])
            mock_rule.return_value = (True, None)
            mock_trusted.return_value = (1, 0, [])

            exit_code = await run_sync(dry_run=False)

        assert exit_code == 0

    async def test_skips_transport_rule_for_single_user(self):
        from sjifire.scripts.signature_sync import run_sync

        users = [make_user(email="test@sjifire.org")]

        with (
            patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_mgr_cls,
            patch("sjifire.scripts.signature_sync.sync_custom_attributes") as mock_sync,
            patch("sjifire.scripts.signature_sync.sync_transport_rule") as mock_rule,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_employees = AsyncMock(return_value=users)
            mock_mgr_cls.return_value = mock_mgr
            mock_sync.return_value = (1, 0, [])

            mock_rule.return_value = (True, None)

            exit_code = await run_sync(email="test@sjifire.org")

        assert exit_code == 0
        mock_rule.assert_not_called()

    async def test_remove_mode(self):
        from sjifire.scripts.signature_sync import run_sync

        users = [make_user()]

        with (
            patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_mgr_cls,
            patch("sjifire.scripts.signature_sync.sync_custom_attributes") as mock_sync,
            patch("sjifire.scripts.signature_sync.remove_transport_rule") as mock_remove,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_employees = AsyncMock(return_value=users)
            mock_mgr_cls.return_value = mock_mgr
            mock_sync.return_value = (1, 0, [])
            mock_remove.return_value = (True, None)

            exit_code = await run_sync(remove=True)

        assert exit_code == 0
        mock_sync.assert_called_once()
        # Verify remove=True was passed (args: users, template, dry_run, remove)
        assert mock_sync.call_args[0][3] is True
        mock_remove.assert_called_once()

    async def test_returns_error_on_transport_rule_failure(self):
        from sjifire.scripts.signature_sync import run_sync

        users = [make_user()]

        with (
            patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_mgr_cls,
            patch("sjifire.scripts.signature_sync.sync_custom_attributes") as mock_sync,
            patch("sjifire.scripts.signature_sync.sync_transport_rule") as mock_rule,
            patch("sjifire.scripts.signature_sync.ensure_trusted_domain") as mock_trusted,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_employees = AsyncMock(return_value=users)
            mock_mgr_cls.return_value = mock_mgr
            mock_sync.return_value = (1, 0, [])
            mock_rule.return_value = (False, "PowerShell error")
            mock_trusted.return_value = (1, 0, [])

            exit_code = await run_sync(dry_run=False)

        assert exit_code == 1


class TestLoadTemplate:
    """Tests for load_template function."""

    def test_loads_default_template(self, template):
        assert template.rule_name == "SJIFR Email Signature"
        assert template.office_phone == "(360) 378-5334"
        assert template.company_name_text == "San Juan Island Fire & Rescue"
        assert template.company_name_html == "San Juan Island Fire &amp; Rescue"

    def test_html_contains_exchange_tokens(self, template):
        assert "%%FirstName%%" in template.rule_html
        assert "%%LastName%%" in template.rule_html
        assert "%%CustomAttribute6%%" in template.rule_html
        assert "%%CustomAttribute7%%" in template.rule_html

    def test_text_contains_exchange_tokens(self, template):
        assert "%%FirstName%%" in template.rule_text
        assert "%%CustomAttribute8%%" in template.rule_text

    def test_html_has_no_unresolved_placeholders(self, template):
        import re

        unresolved = re.findall(r"\{\{[^}]+\}\}", template.rule_html)
        assert unresolved == [], f"Unresolved placeholders: {unresolved}"

    def test_text_has_no_unresolved_placeholders(self, template):
        import re

        unresolved = re.findall(r"\{\{[^}]+\}\}", template.rule_text)
        assert unresolved == [], f"Unresolved placeholders: {unresolved}"

    def test_missing_template_raises(self):
        with pytest.raises(FileNotFoundError):
            load_template("nonexistent")
