"""Tests for sjifire.scripts.signature_sync."""

from unittest.mock import AsyncMock, MagicMock, patch

from sjifire.entra.users import EntraUser
from sjifire.scripts.signature_sync import (
    COMPANY_NAME,
    OFFICE_PHONE,
    generate_signature_html,
    generate_signature_text,
)


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


class TestGenerateSignatureHtml:
    """Tests for generate_signature_html function."""

    def test_user_with_rank(self):
        user = make_user(first_name="Karl", last_name="Kuetzing", rank="Captain")
        html = generate_signature_html(user)

        assert "Karl Kuetzing" in html
        assert "Captain" in html
        assert COMPANY_NAME in html
        assert "<strong" in html

    def test_user_with_job_title_no_rank(self):
        user = make_user(first_name="Robin", last_name="Garcia", job_title="Executive Assistant, Finance")
        html = generate_signature_html(user)

        assert "Robin Garcia" in html
        assert "Executive Assistant, Finance" in html
        assert COMPANY_NAME in html

    def test_user_with_rank_and_job_title_shows_both(self):
        user = make_user(first_name="John", last_name="Doe", rank="Captain", job_title="Training Officer")
        html = generate_signature_html(user)

        assert "John Doe" in html
        assert "Captain - Training Officer" in html
        assert COMPANY_NAME in html

    def test_user_with_no_rank_or_title(self):
        user = make_user(first_name="Adam", last_name="Greene")
        html = generate_signature_html(user)

        assert "Adam Greene" in html
        assert COMPANY_NAME in html
        # Should not have an extra line for title
        assert "<br>\n<span" in html or "<br>\n" in html

    def test_uses_first_last_name_not_display_name(self):
        user = make_user(
            display_name="Chief Jane Smith",
            first_name="Jane",
            last_name="Smith",
            rank="Chief",
        )
        html = generate_signature_html(user)

        # Should use first/last name, not display_name with rank prefix
        assert "Jane Smith" in html
        assert "Chief Jane Smith" not in html

    def test_fallback_to_first_last_name(self):
        user = make_user(
            display_name=None,
            first_name="Jane",
            last_name="Smith",
        )
        html = generate_signature_html(user)

        assert "Jane Smith" in html

    def test_html_structure(self):
        user = make_user(display_name="Test User", rank="Lieutenant")
        html = generate_signature_html(user)

        assert "<p style=" in html
        assert "<strong style=" in html
        assert "<span style=" in html
        assert "color: #333" in html
        assert "color: #666" in html


class TestGenerateSignatureText:
    """Tests for generate_signature_text function."""

    def test_user_with_rank(self):
        user = make_user(first_name="Karl", last_name="Kuetzing", rank="Captain")
        text = generate_signature_text(user)

        assert text == f"Karl Kuetzing\nCaptain\n{COMPANY_NAME}\nOffice: {OFFICE_PHONE}"

    def test_user_with_job_title_no_rank(self):
        user = make_user(first_name="Robin", last_name="Garcia", job_title="Executive Assistant")
        text = generate_signature_text(user)

        assert text == f"Robin Garcia\nExecutive Assistant\n{COMPANY_NAME}\nOffice: {OFFICE_PHONE}"

    def test_user_with_rank_and_job_title_shows_both(self):
        user = make_user(first_name="John", last_name="Doe", rank="Captain", job_title="Training Officer")
        text = generate_signature_text(user)

        assert text == f"John Doe\nCaptain - Training Officer\n{COMPANY_NAME}\nOffice: {OFFICE_PHONE}"

    def test_user_with_no_rank_or_title(self):
        user = make_user(first_name="Adam", last_name="Greene")
        text = generate_signature_text(user)

        assert text == f"Adam Greene\n{COMPANY_NAME}\nOffice: {OFFICE_PHONE}"

    def test_user_with_cell_phone(self):
        user = make_user(first_name="Karl", last_name="Kuetzing", rank="Captain", mobile_phone="(360) 555-1234")
        text = generate_signature_text(user)

        assert text == f"Karl Kuetzing\nCaptain\n{COMPANY_NAME}\nOffice: {OFFICE_PHONE} | Cell: (360) 555-1234"

    def test_fallback_to_first_last_name(self):
        user = make_user(
            display_name=None,
            first_name="Jane",
            last_name="Smith",
        )
        text = generate_signature_text(user)

        assert "Jane Smith" in text


class TestSyncUserSignature:
    """Tests for sync_user_signature function."""

    async def test_returns_false_for_user_without_email(self):
        from sjifire.scripts.signature_sync import sync_user_signature

        user = make_user(email=None)
        client = MagicMock()

        success, error = await sync_user_signature(client, user)

        assert success is False
        assert error == "No email address"

    async def test_dry_run_returns_success(self):
        from sjifire.scripts.signature_sync import sync_user_signature

        user = make_user()
        client = MagicMock()

        success, error = await sync_user_signature(client, user, dry_run=True)

        assert success is True
        assert error is None
        client._run_powershell.assert_not_called()

    async def test_calls_powershell_with_signature(self):
        from sjifire.scripts.signature_sync import sync_user_signature

        user = make_user(display_name="Test User", rank="Captain")
        client = MagicMock()
        client._run_powershell.return_value = "SUCCESS"

        success, error = await sync_user_signature(client, user, dry_run=False)

        assert success is True
        assert error is None
        client._run_powershell.assert_called_once()

        # Verify PowerShell script contains expected content
        script = client._run_powershell.call_args[0][0][0]
        assert "Set-MailboxMessageConfiguration" in script
        assert user.email in script
        assert "-AutoAddSignature $true" in script
        assert "-AutoAddSignatureOnReply $true" in script

    async def test_handles_powershell_failure(self):
        from sjifire.scripts.signature_sync import sync_user_signature

        user = make_user()
        client = MagicMock()
        client._run_powershell.return_value = "Error: Something went wrong"

        success, error = await sync_user_signature(client, user, dry_run=False)

        assert success is False
        assert "Failed to set signature" in error


class TestSyncSignatures:
    """Tests for sync_signatures function."""

    async def test_processes_all_users(self):
        from sjifire.scripts.signature_sync import sync_signatures

        users = [
            make_user(display_name="User 1", email="user1@sjifire.org"),
            make_user(display_name="User 2", email="user2@sjifire.org"),
        ]

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = "SUCCESS"
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            success, failure, errors = await sync_signatures(users, dry_run=False)

        assert success == 2
        assert failure == 0
        assert errors == []

    async def test_counts_failures(self):
        from sjifire.scripts.signature_sync import sync_signatures

        users = [
            make_user(display_name="User 1", email="user1@sjifire.org"),
            make_user(display_name="User 2", email=None),  # Will fail - no email
        ]

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client._run_powershell.return_value = "SUCCESS"
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            success, failure, errors = await sync_signatures(users, dry_run=False)

        assert success == 1
        assert failure == 1
        assert len(errors) == 1

    async def test_dry_run_skips_powershell(self):
        from sjifire.scripts.signature_sync import sync_signatures

        users = [make_user()]

        with patch("sjifire.scripts.signature_sync.ExchangeOnlineClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            success, _failure, _errors = await sync_signatures(users, dry_run=True)

        assert success == 1
        mock_client._run_powershell.assert_not_called()


class TestRunSync:
    """Tests for run_sync function."""

    async def test_preview_requires_email(self):
        from sjifire.scripts.signature_sync import run_sync

        exit_code = await run_sync(preview=True, email=None)

        assert exit_code == 1

    async def test_preview_mode_shows_signature(self):
        from sjifire.scripts.signature_sync import run_sync

        mock_user = make_user(
            display_name="Test User", email="test@sjifire.org", rank="Captain"
        )

        with patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.get_employees = AsyncMock(return_value=[mock_user])
            mock_manager_class.return_value = mock_manager

            exit_code = await run_sync(preview=True, email="test@sjifire.org")

        assert exit_code == 0

    async def test_filters_to_sjifire_domain(self):
        from sjifire.scripts.signature_sync import run_sync

        users = [
            make_user(email="user1@sjifire.org"),
            make_user(email="user2@otherdomain.com"),
        ]

        with (
            patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_manager_class,
            patch("sjifire.scripts.signature_sync.sync_signatures") as mock_sync,
        ):
            mock_manager = MagicMock()
            mock_manager.get_employees = AsyncMock(return_value=users)
            mock_manager_class.return_value = mock_manager
            mock_sync.return_value = (1, 0, [])

            await run_sync(dry_run=False)

        # Should only sync sjifire.org users
        synced_users = mock_sync.call_args[0][0]
        assert len(synced_users) == 1
        assert synced_users[0].email == "user1@sjifire.org"

    async def test_returns_error_when_user_not_found(self):
        from sjifire.scripts.signature_sync import run_sync

        with patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.get_employees = AsyncMock(return_value=[])
            mock_manager_class.return_value = mock_manager

            exit_code = await run_sync(email="notfound@sjifire.org")

        assert exit_code == 1

    async def test_returns_success_when_all_sync(self):
        from sjifire.scripts.signature_sync import run_sync

        users = [make_user()]

        with (
            patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_manager_class,
            patch("sjifire.scripts.signature_sync.sync_signatures") as mock_sync,
        ):
            mock_manager = MagicMock()
            mock_manager.get_employees = AsyncMock(return_value=users)
            mock_manager_class.return_value = mock_manager
            mock_sync.return_value = (1, 0, [])

            exit_code = await run_sync(dry_run=False)

        assert exit_code == 0

    async def test_returns_failure_when_some_fail(self):
        from sjifire.scripts.signature_sync import run_sync

        users = [make_user()]

        with (
            patch("sjifire.scripts.signature_sync.EntraUserManager") as mock_manager_class,
            patch("sjifire.scripts.signature_sync.sync_signatures") as mock_sync,
        ):
            mock_manager = MagicMock()
            mock_manager.get_employees = AsyncMock(return_value=users)
            mock_manager_class.return_value = mock_manager
            mock_sync.return_value = (0, 1, ["error"])

            exit_code = await run_sync(dry_run=False)

        assert exit_code == 1
