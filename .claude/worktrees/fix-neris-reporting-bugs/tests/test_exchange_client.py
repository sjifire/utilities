"""Tests for exchange/client.py - Exchange Online PowerShell client."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sjifire.exchange.client import ExchangeGroup, ExchangeOnlineClient

# Test password for certificate authentication (not a real secret)
TEST_CERT_PASSWORD = "test-password"


# =============================================================================
# Mock Credentials
# =============================================================================


@dataclass
class MockExchangeCredentials:
    """Mock credentials for testing."""

    tenant_id: str = "test-tenant-id"
    client_id: str = "test-client-id"
    organization: str = "test.org"
    certificate_thumbprint: str | None = None
    certificate_path: Path | None = None
    certificate_password: str | None = None


# =============================================================================
# Test ExchangeGroup Dataclass
# =============================================================================


class TestExchangeGroup:
    """Tests for ExchangeGroup dataclass."""

    def test_all_fields(self):
        """ExchangeGroup should store all fields."""
        group = ExchangeGroup(
            identity="test-group",
            display_name="Test Group",
            primary_smtp_address="test@example.com",
            group_type="MailEnabledSecurity",
            members=["user1@example.com", "user2@example.com"],
        )
        assert group.identity == "test-group"
        assert group.display_name == "Test Group"
        assert group.primary_smtp_address == "test@example.com"
        assert group.group_type == "MailEnabledSecurity"
        assert group.members == ["user1@example.com", "user2@example.com"]

    def test_members_optional(self):
        """Members field should be optional."""
        group = ExchangeGroup(
            identity="test-group",
            display_name="Test Group",
            primary_smtp_address="test@example.com",
            group_type="MailEnabledSecurity",
        )
        assert group.members is None


# =============================================================================
# Test Client Initialization
# =============================================================================


class TestExchangeOnlineClientInit:
    """Tests for ExchangeOnlineClient initialization."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_init_uses_credentials(self, mock_get_creds):
        """Client should load credentials on init."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )

        client = ExchangeOnlineClient()

        assert client.tenant_id == "test-tenant-id"
        assert client.client_id == "test-client-id"
        assert client.organization == "test.org"
        assert client.certificate_thumbprint == "ABC123"

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_init_override_organization(self, mock_get_creds):
        """Organization can be overridden in init."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )

        client = ExchangeOnlineClient(organization="override.org")

        assert client.organization == "override.org"

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_init_override_certificate_thumbprint(self, mock_get_creds):
        """Certificate thumbprint can be overridden."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )

        client = ExchangeOnlineClient(certificate_thumbprint="XYZ789")

        assert client.certificate_thumbprint == "XYZ789"

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_init_certificate_path(self, mock_get_creds):
        """Certificate path can be provided."""
        mock_get_creds.return_value = MockExchangeCredentials()

        client = ExchangeOnlineClient(
            certificate_path=Path("/path/to/cert.pfx"),
            certificate_password=TEST_CERT_PASSWORD,
        )

        assert client.certificate_path == Path("/path/to/cert.pfx")
        assert client.certificate_password == TEST_CERT_PASSWORD


# =============================================================================
# Test _build_connect_command
# =============================================================================


class TestBuildConnectCommand:
    """Tests for _build_connect_command method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_connect_with_thumbprint(self, mock_get_creds):
        """Connect command with certificate thumbprint."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )

        client = ExchangeOnlineClient()
        cmd = client._build_connect_command()

        assert "Connect-ExchangeOnline" in cmd
        assert "-CertificateThumbprint 'ABC123'" in cmd
        assert "-AppId 'test-client-id'" in cmd
        assert "-Organization 'test.org'" in cmd
        assert "*>$null" in cmd  # Output suppression

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_connect_with_certificate_path(self, mock_get_creds):
        """Connect command with certificate file path."""
        mock_get_creds.return_value = MockExchangeCredentials()

        client = ExchangeOnlineClient(
            certificate_path=Path("/path/to/cert.pfx"),
            certificate_password=TEST_CERT_PASSWORD,
        )
        cmd = client._build_connect_command()

        assert "Connect-ExchangeOnline" in cmd
        assert "-CertificateFilePath '/path/to/cert.pfx'" in cmd
        assert "-CertificatePassword (ConvertTo-SecureString" in cmd
        assert f"-String '{TEST_CERT_PASSWORD}'" in cmd
        assert "-AsPlainText -Force)" in cmd

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_connect_with_certificate_path_no_password(self, mock_get_creds):
        """Connect command with certificate file but no password (Key Vault certs)."""
        mock_get_creds.return_value = MockExchangeCredentials()

        client = ExchangeOnlineClient(
            certificate_path=Path("/path/to/cert.pfx"),
            certificate_password="",  # Empty password
        )
        cmd = client._build_connect_command()

        assert "-CertificateFilePath '/path/to/cert.pfx'" in cmd
        assert "-CertificatePassword" not in cmd  # Should not include password param

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_connect_prefers_path_over_thumbprint(self, mock_get_creds):
        """Certificate path should be preferred over thumbprint."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )

        client = ExchangeOnlineClient(
            certificate_path=Path("/path/to/cert.pfx"),
            certificate_password=TEST_CERT_PASSWORD,
        )
        cmd = client._build_connect_command()

        assert "-CertificateFilePath" in cmd
        assert "-CertificateThumbprint" not in cmd

    @patch("sjifire.exchange.client.get_exchange_credentials")
    def test_connect_no_auth_raises(self, mock_get_creds):
        """Should raise ValueError if no authentication method provided."""
        mock_get_creds.return_value = MockExchangeCredentials()

        client = ExchangeOnlineClient()

        with pytest.raises(ValueError) as exc_info:
            client._build_connect_command()
        assert "certificate_thumbprint or certificate_path" in str(exc_info.value)


# =============================================================================
# Test _run_powershell
# =============================================================================


class TestRunPowerShell:
    """Tests for _run_powershell method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_success_json(self, mock_run, mock_get_creds):
        """Successful PowerShell execution with JSON output."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"Identity": "test-group", "DisplayName": "Test Group"}',
            stderr="",
        )

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Get-DistributionGroup"])

        assert result == {"Identity": "test-group", "DisplayName": "Test Group"}

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_success_array(self, mock_run, mock_get_creds):
        """Successful PowerShell execution with JSON array output."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"Identity": "group1"}, {"Identity": "group2"}]',
            stderr="",
        )

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Get-DistributionGroup"])

        assert result == [{"Identity": "group1"}, {"Identity": "group2"}]

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_success_raw(self, mock_run, mock_get_creds):
        """Successful PowerShell execution with raw text output."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="SUCCESS",
            stderr="",
        )

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Write-Output 'SUCCESS'"], parse_json=False)

        assert result == "SUCCESS"

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_empty_output(self, mock_run, mock_get_creds):
        """PowerShell with empty output returns empty dict."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Get-DistributionGroup"])

        assert result == {}

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_error(self, mock_run, mock_get_creds):
        """PowerShell error returns None."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Command failed",
        )

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Bad-Command"])

        assert result is None

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_timeout(self, mock_run, mock_get_creds):
        """PowerShell timeout returns None."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pwsh", timeout=120)

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Long-Running-Command"])

        assert result is None

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_not_found(self, mock_run, mock_get_creds):
        """PowerShell not installed returns None."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.side_effect = FileNotFoundError("pwsh not found")

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Get-DistributionGroup"])

        assert result is None

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_json_with_banner(self, mock_run, mock_get_creds):
        """PowerShell output with banner text before JSON."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='Banner: Connection established\n{"Identity": "test-group"}',
            stderr="",
        )

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Get-DistributionGroup"])

        # Should extract JSON from output
        assert result == {"Identity": "test-group"}

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch("subprocess.run")
    def test_run_powershell_invalid_json(self, mock_run, mock_get_creds):
        """PowerShell output with non-JSON returns raw wrapper."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Not JSON output",
            stderr="",
        )

        client = ExchangeOnlineClient()
        result = client._run_powershell(["Get-DistributionGroup"])

        # Should return raw output wrapper
        assert result == {"raw": "Not JSON output"}


# =============================================================================
# Test get_distribution_group
# =============================================================================


class TestGetDistributionGroup:
    """Tests for get_distribution_group method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_get_group_found(self, mock_run_ps, mock_get_creds):
        """Should return ExchangeGroup when found."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = {
            "Identity": "test-group",
            "DisplayName": "Test Group",
            "PrimarySmtpAddress": "test@example.com",
            "RecipientTypeDetails": "MailEnabledSecurity",
        }

        client = ExchangeOnlineClient()
        result = await client.get_distribution_group("test-group")

        assert result is not None
        assert result.identity == "test-group"
        assert result.display_name == "Test Group"
        assert result.primary_smtp_address == "test@example.com"
        assert result.group_type == "MailEnabledSecurity"

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_get_group_not_found(self, mock_run_ps, mock_get_creds):
        """Should return None when group not found."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = {}

        client = ExchangeOnlineClient()
        result = await client.get_distribution_group("nonexistent")

        assert result is None

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_get_group_error(self, mock_run_ps, mock_get_creds):
        """Should return None on PowerShell error."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = None

        client = ExchangeOnlineClient()
        result = await client.get_distribution_group("test-group")

        assert result is None


# =============================================================================
# Test create_mail_enabled_security_group
# =============================================================================


class TestCreateMailEnabledSecurityGroup:
    """Tests for create_mail_enabled_security_group method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_create_group_success(self, mock_run_ps, mock_get_creds):
        """Should return ExchangeGroup on successful creation."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = {
            "Identity": "test-group",
            "DisplayName": "Test Group",
            "PrimarySmtpAddress": "test@example.com",
            "RecipientTypeDetails": "MailEnabledSecurity",
        }

        client = ExchangeOnlineClient()
        result = await client.create_mail_enabled_security_group(
            name="test-group",
            display_name="Test Group",
            alias="testgroup",
            primary_smtp_address="test@example.com",
        )

        assert result is not None
        assert result.display_name == "Test Group"
        assert result.group_type == "MailEnabledSecurity"

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_create_group_with_members(self, mock_run_ps, mock_get_creds):
        """Should build command with members."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = {"Identity": "test-group", "DisplayName": "Test"}

        client = ExchangeOnlineClient()
        await client.create_mail_enabled_security_group(
            name="test-group",
            display_name="Test Group",
            alias="testgroup",
            members=["user1@example.com", "user2@example.com"],
        )

        # Check that the command includes members
        call_args = mock_run_ps.call_args[0][0]
        cmd = call_args[0]  # First command
        assert "-Members @('user1@example.com', 'user2@example.com')" in cmd

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_create_group_with_notes(self, mock_run_ps, mock_get_creds):
        """Should escape single quotes in notes."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = {"Identity": "test-group", "DisplayName": "Test"}

        client = ExchangeOnlineClient()
        await client.create_mail_enabled_security_group(
            name="test-group",
            display_name="Test Group",
            alias="testgroup",
            notes="It's a test group",
        )

        call_args = mock_run_ps.call_args[0][0]
        cmd = call_args[0]
        assert "-Notes 'It''s a test group'" in cmd  # Escaped quote

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_create_group_failure(self, mock_run_ps, mock_get_creds):
        """Should return None on failure."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = None

        client = ExchangeOnlineClient()
        result = await client.create_mail_enabled_security_group(
            name="test-group",
            display_name="Test Group",
            alias="testgroup",
        )

        assert result is None


# =============================================================================
# Test update_distribution_group_description
# =============================================================================


class TestUpdateDistributionGroupDescription:
    """Tests for update_distribution_group_description method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_update_description_success(self, mock_run_ps, mock_get_creds):
        """Should return True on success."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "SUCCESS"

        client = ExchangeOnlineClient()
        result = await client.update_distribution_group_description(
            identity="test-group",
            description="Updated description",
        )

        assert result is True

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_update_description_escapes_quotes(self, mock_run_ps, mock_get_creds):
        """Should escape single quotes in description."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "SUCCESS"

        client = ExchangeOnlineClient()
        await client.update_distribution_group_description(
            identity="test-group",
            description="It's automated",
        )

        call_args = mock_run_ps.call_args[0][0]
        cmd = call_args[0]
        assert "It''s automated" in cmd  # Escaped quote

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_update_description_failure(self, mock_run_ps, mock_get_creds):
        """Should return False on failure."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = None

        client = ExchangeOnlineClient()
        result = await client.update_distribution_group_description(
            identity="test-group",
            description="Updated",
        )

        assert result is False


# =============================================================================
# Test update_distribution_group_managed_by
# =============================================================================


class TestUpdateDistributionGroupManagedBy:
    """Tests for update_distribution_group_managed_by method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_update_managed_by_success(self, mock_run_ps, mock_get_creds):
        """Should return True on success."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "SUCCESS"

        client = ExchangeOnlineClient()
        result = await client.update_distribution_group_managed_by(
            identity="test-group",
            managed_by="owner@example.com",
        )

        assert result is True

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_update_managed_by_includes_bypass(self, mock_run_ps, mock_get_creds):
        """Should include BypassSecurityGroupManagerCheck flag."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "SUCCESS"

        client = ExchangeOnlineClient()
        await client.update_distribution_group_managed_by(
            identity="test-group",
            managed_by="owner@example.com",
        )

        call_args = mock_run_ps.call_args[0][0]
        cmd = call_args[0]
        assert "-BypassSecurityGroupManagerCheck" in cmd


# =============================================================================
# Test delete_distribution_group
# =============================================================================


class TestDeleteDistributionGroup:
    """Tests for delete_distribution_group method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_delete_group_success(self, mock_run_ps, mock_get_creds):
        """Should return True on successful deletion."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "SUCCESS"

        client = ExchangeOnlineClient()
        result = await client.delete_distribution_group("test-group")

        assert result is True

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "get_distribution_group")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_delete_group_already_deleted(self, mock_run_ps, mock_get_group, mock_get_creds):
        """Should return True if group doesn't exist."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = None  # Indicates failure/not found
        mock_get_group.return_value = None  # Group doesn't exist

        client = ExchangeOnlineClient()
        result = await client.delete_distribution_group("nonexistent")

        assert result is True


# =============================================================================
# Test get_distribution_group_members
# =============================================================================


class TestGetDistributionGroupMembers:
    """Tests for get_distribution_group_members method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_get_members_multiple(self, mock_run_ps, mock_get_creds):
        """Should return list of member emails."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = [
            {"PrimarySmtpAddress": "User1@Example.com"},
            {"PrimarySmtpAddress": "User2@Example.com"},
        ]

        client = ExchangeOnlineClient()
        result = await client.get_distribution_group_members("test-group")

        assert result == ["user1@example.com", "user2@example.com"]  # Lowercased

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_get_members_single(self, mock_run_ps, mock_get_creds):
        """Should handle single member (returns dict not list)."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = {"PrimarySmtpAddress": "User@Example.com"}

        client = ExchangeOnlineClient()
        result = await client.get_distribution_group_members("test-group")

        assert result == ["user@example.com"]

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_get_members_empty(self, mock_run_ps, mock_get_creds):
        """Should return empty list for empty group."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = {}

        client = ExchangeOnlineClient()
        result = await client.get_distribution_group_members("test-group")

        assert result == []

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_get_members_error(self, mock_run_ps, mock_get_creds):
        """Should return empty list on error."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = None

        client = ExchangeOnlineClient()
        result = await client.get_distribution_group_members("test-group")

        assert result == []


# =============================================================================
# Test add_distribution_group_member
# =============================================================================


class TestAddDistributionGroupMember:
    """Tests for add_distribution_group_member method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_add_member_success(self, mock_run_ps, mock_get_creds):
        """Should return True on successful add."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "SUCCESS"

        client = ExchangeOnlineClient()
        result = await client.add_distribution_group_member("test-group", "user@example.com")

        assert result is True

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_add_member_already_member(self, mock_run_ps, mock_get_creds):
        """Should return True when already a member (idempotent)."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "user@example.com is already a member of test-group"

        client = ExchangeOnlineClient()
        result = await client.add_distribution_group_member("test-group", "user@example.com")

        assert result is True

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_add_member_failure(self, mock_run_ps, mock_get_creds):
        """Should return False on failure."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "Error: Group not found"

        client = ExchangeOnlineClient()
        result = await client.add_distribution_group_member("test-group", "user@example.com")

        assert result is False


# =============================================================================
# Test remove_distribution_group_member
# =============================================================================


class TestRemoveDistributionGroupMember:
    """Tests for remove_distribution_group_member method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_remove_member_success(self, mock_run_ps, mock_get_creds):
        """Should return True on successful remove."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "SUCCESS"

        client = ExchangeOnlineClient()
        result = await client.remove_distribution_group_member("test-group", "user@example.com")

        assert result is True

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @patch.object(ExchangeOnlineClient, "_run_powershell")
    @pytest.mark.asyncio
    async def test_remove_member_failure(self, mock_run_ps, mock_get_creds):
        """Should return False on failure."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )
        mock_run_ps.return_value = "Error: Group not found"

        client = ExchangeOnlineClient()
        result = await client.remove_distribution_group_member("test-group", "user@example.com")

        assert result is False


# =============================================================================
# Test close
# =============================================================================


class TestClose:
    """Tests for close method."""

    @patch("sjifire.exchange.client.get_exchange_credentials")
    @pytest.mark.asyncio
    async def test_close_is_noop(self, mock_get_creds):
        """Close should be a no-op for subprocess approach."""
        mock_get_creds.return_value = MockExchangeCredentials(
            certificate_thumbprint="ABC123",
        )

        client = ExchangeOnlineClient()
        # Should not raise
        await client.close()
