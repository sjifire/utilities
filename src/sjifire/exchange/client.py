"""Exchange Online PowerShell client.

Executes Exchange Online PowerShell cmdlets via subprocess to manage
mail-enabled security groups and distribution lists.

This uses the official Exchange Online PowerShell module which is fully
supported by Microsoft.

Prerequisites:
1. Install Exchange Online Management module:
   Install-Module -Name ExchangeOnlineManagement

2. For app-only (unattended) authentication, you need:
   - Azure AD App Registration with Exchange.ManageAsApp permission
   - A certificate (self-signed or CA-signed) uploaded to the app
   - The certificate installed locally (or accessible as .pfx file)
   - App assigned "Exchange Recipient Administrator" role

References:
- https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2
- https://learn.microsoft.com/en-us/powershell/exchange/exchange-online-powershell-v2
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tenacity import retry, retry_if_result, stop_after_attempt, wait_exponential

from sjifire.core.config import get_graph_credentials

logger = logging.getLogger(__name__)


@dataclass
class ExchangeGroup:
    """Represents an Exchange Online mail-enabled group."""

    identity: str  # Group identity (name or email)
    display_name: str
    primary_smtp_address: str
    group_type: str  # "MailEnabledSecurity" or "Distribution"
    members: list[str] | None = None


class ExchangeOnlineClient:
    """Client for Exchange Online PowerShell operations.

    Executes Exchange cmdlets via subprocess using the official
    ExchangeOnlineManagement PowerShell module.
    """

    def __init__(
        self,
        certificate_thumbprint: str | None = None,
        certificate_path: Path | str | None = None,
        certificate_password: str | None = None,
        organization: str = "sjifire.org",
    ) -> None:
        """Initialize the Exchange Online client.

        Args:
            certificate_thumbprint: Thumbprint of installed certificate (Windows)
            certificate_path: Path to .pfx certificate file (cross-platform)
            certificate_password: Password for the .pfx file
            organization: The organization domain (e.g., "sjifire.org")
        """
        self.tenant_id, self.client_id, _ = get_graph_credentials()
        self.certificate_thumbprint = certificate_thumbprint
        self.certificate_path = certificate_path
        self.certificate_password = certificate_password
        self.organization = organization
        self._connected = False

    def _build_connect_command(self) -> str:
        """Build the Connect-ExchangeOnline command."""
        # Note: -ShowBanner:$false doesn't work via subprocess, so we omit it
        # The banner output is ignored anyway since we parse JSON from stdout
        # Prefer certificate_path over thumbprint (thumbprint is Windows-only)
        if self.certificate_path:
            # Cross-platform: Use certificate file
            # For empty password (Key Vault certs), skip the -CertificatePassword param
            if self.certificate_password:
                secure_str = (
                    f"-CertificatePassword (ConvertTo-SecureString "
                    f"-String '{self.certificate_password}' -AsPlainText -Force) "
                )
            else:
                secure_str = ""
            return (
                f"Connect-ExchangeOnline "
                f"-AppId '{self.client_id}' "
                f"-CertificateFilePath '{self.certificate_path}' "
                f"{secure_str}"
                f"-Organization '{self.organization}'"
            )
        elif self.certificate_thumbprint:
            # Windows: Use installed certificate by thumbprint
            return (
                f"Connect-ExchangeOnline "
                f"-AppId '{self.client_id}' "
                f"-CertificateThumbprint '{self.certificate_thumbprint}' "
                f"-Organization '{self.organization}'"
            )
        else:
            raise ValueError("Either certificate_thumbprint or certificate_path must be provided")

    def _run_powershell(self, commands: list[str], parse_json: bool = True) -> dict | str | None:
        """Run PowerShell commands and return the result.

        Args:
            commands: List of PowerShell commands to execute
            parse_json: If True, parse output as JSON

        Returns:
            Parsed JSON dict, raw string output, or None on failure
        """
        # Build the full script with connection
        full_script = [
            # Import module
            "Import-Module ExchangeOnlineManagement -ErrorAction Stop",
            # Connect
            self._build_connect_command(),
            # Run commands
            *commands,
            # Disconnect
            "Disconnect-ExchangeOnline -Confirm:$false",
        ]

        script = "; ".join(full_script)

        try:
            result = subprocess.run(  # noqa: S603
                ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                logger.error(f"PowerShell error: {result.stderr}")
                return None

            output = result.stdout.strip()
            if not output:
                return {} if parse_json else ""

            if parse_json:
                try:
                    return json.loads(output)
                except json.JSONDecodeError:
                    # Banner text may precede JSON - try to find JSON in output
                    json_start = output.find("{")
                    if json_start == -1:
                        json_start = output.find("[")
                    if json_start != -1:
                        try:
                            return json.loads(output[json_start:])
                        except json.JSONDecodeError:
                            pass
                    # Only warn if output looks like it might contain JSON data
                    # (not just banner text or empty results)
                    if "{" in output or "[" in output:
                        logger.warning(f"Failed to parse JSON output: {output[:200]}")
                    return {"raw": output}

            return output

        except subprocess.TimeoutExpired:
            logger.error("PowerShell command timed out")
            return None
        except FileNotFoundError:
            logger.error("PowerShell (pwsh) not found. Install PowerShell 7+.")
            return None
        except Exception as e:
            logger.error(f"Failed to run PowerShell: {e}")
            return None

    async def get_distribution_group(self, identity: str) -> ExchangeGroup | None:
        """Get a distribution group or mail-enabled security group by identity.

        Args:
            identity: Group name, alias, or email address

        Returns:
            ExchangeGroup if found, None otherwise
        """
        select_fields = "Identity, DisplayName, PrimarySmtpAddress, RecipientTypeDetails"
        commands = [
            f"$group = Get-DistributionGroup -Identity '{identity}' -ErrorAction SilentlyContinue",
            f"if ($group) {{ $group | Select-Object {select_fields} | ConvertTo-Json }}",
        ]

        result = self._run_powershell(commands)
        if result and isinstance(result, dict) and "Identity" in result:
            return ExchangeGroup(
                identity=result.get("Identity", identity),
                display_name=result.get("DisplayName", ""),
                primary_smtp_address=result.get("PrimarySmtpAddress", ""),
                group_type=result.get("RecipientTypeDetails", ""),
            )
        return None

    async def create_mail_enabled_security_group(
        self,
        name: str,
        display_name: str,
        alias: str,
        primary_smtp_address: str | None = None,
        members: list[str] | None = None,
        managed_by: str | None = None,
    ) -> ExchangeGroup | None:
        """Create a new mail-enabled security group.

        Args:
            name: Internal name of the group
            display_name: Display name shown in address book
            alias: Email alias (without domain)
            primary_smtp_address: Full email address (optional)
            members: List of member email addresses to add
            managed_by: Email of the group owner/manager

        Returns:
            Created ExchangeGroup or None on failure
        """
        # Build New-DistributionGroup command
        cmd_parts = [
            f"New-DistributionGroup -Name '{name}'",
            f"-DisplayName '{display_name}'",
            f"-Alias '{alias}'",
            "-Type 'Security'",  # Creates mail-enabled security group
        ]

        if primary_smtp_address:
            cmd_parts.append(f"-PrimarySmtpAddress '{primary_smtp_address}'")

        if managed_by:
            cmd_parts.append(f"-ManagedBy '{managed_by}'")

        if members:
            members_str = "', '".join(members)
            cmd_parts.append(f"-Members @('{members_str}')")

        create_cmd = " ".join(cmd_parts)

        select_fields = "Identity, DisplayName, PrimarySmtpAddress, RecipientTypeDetails"
        commands = [
            f"$group = {create_cmd}",
            f"$group | Select-Object {select_fields} | ConvertTo-Json",
        ]

        result = self._run_powershell(commands)
        if result and isinstance(result, dict) and "Identity" in result:
            logger.info(f"Created mail-enabled security group: {display_name}")
            return ExchangeGroup(
                identity=result.get("Identity", name),
                display_name=result.get("DisplayName", display_name),
                primary_smtp_address=result.get("PrimarySmtpAddress", primary_smtp_address or ""),
                group_type="MailEnabledSecurity",
            )

        logger.error(f"Failed to create mail-enabled security group: {name}")
        return None

    async def get_distribution_group_members(self, identity: str) -> list[str]:
        """Get members of a distribution group or mail-enabled security group.

        Args:
            identity: Group name, alias, or email address

        Returns:
            List of member email addresses
        """
        commands = [
            f"Get-DistributionGroupMember -Identity '{identity}' "
            "| Select-Object PrimarySmtpAddress | ConvertTo-Json",
        ]

        result = self._run_powershell(commands)
        if not result:
            return []

        members: list[str] = []
        # Handle single member (dict) vs multiple (list)
        if isinstance(result, dict):
            if "PrimarySmtpAddress" in result:
                members.append(result["PrimarySmtpAddress"].lower())
        elif isinstance(result, list):
            members.extend(
                m["PrimarySmtpAddress"].lower()
                for m in result
                if isinstance(m, dict) and "PrimarySmtpAddress" in m
            )

        return members

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_result(lambda r: r == "RETRY"),
        reraise=True,
    )
    def _add_member_with_retry(self, identity: str, member: str) -> str:
        """Add member with retry logic for transient errors.

        Returns:
            "SUCCESS", "ALREADY_MEMBER", "RETRY" (for transient errors), or "FAILED"
        """
        commands = [
            f"Add-DistributionGroupMember -Identity '{identity}' "
            f"-Member '{member}' -BypassSecurityGroupManagerCheck -ErrorAction Stop",
            "Write-Output 'SUCCESS'",
        ]

        result = self._run_powershell(commands, parse_json=False)
        result_str = str(result) if result else ""

        if "SUCCESS" in result_str:
            return "SUCCESS"

        if "already a member" in result_str.lower():
            return "ALREADY_MEMBER"

        # Check for transient Azure AD sync errors - retry these
        transient_indicators = [
            "transient",
            "retry a couple of minutes",
            "does not exist or one of its queried reference-property",
            "DualWrite",
        ]
        if any(indicator in result_str for indicator in transient_indicators):
            logger.warning(f"Transient error adding {member} to {identity}, will retry...")
            return "RETRY"

        return "FAILED"

    async def add_distribution_group_member(
        self,
        identity: str,
        member: str,
    ) -> bool:
        """Add a member to a distribution group or mail-enabled security group.

        Args:
            identity: Group name, alias, or email address
            member: Member email address to add

        Returns:
            True if successful
        """
        result = self._add_member_with_retry(identity, member)

        if result == "SUCCESS":
            logger.info(f"Added {member} to {identity}")
            return True

        if result == "ALREADY_MEMBER":
            logger.debug(f"{member} is already a member of {identity}")
            return True

        logger.error(f"Failed to add {member} to {identity}")
        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_result(lambda r: r == "RETRY"),
        reraise=True,
    )
    def _remove_member_with_retry(self, identity: str, member: str) -> str:
        """Remove member with retry logic for transient errors.

        Returns:
            "SUCCESS", "RETRY" (for transient errors), or "FAILED"
        """
        commands = [
            f"Remove-DistributionGroupMember -Identity '{identity}' "
            f"-Member '{member}' -BypassSecurityGroupManagerCheck "
            "-Confirm:$false -ErrorAction Stop",
            "Write-Output 'SUCCESS'",
        ]

        result = self._run_powershell(commands, parse_json=False)
        result_str = str(result) if result else ""

        if "SUCCESS" in result_str:
            return "SUCCESS"

        # Check for transient Azure AD sync errors - retry these
        transient_indicators = [
            "transient",
            "retry a couple of minutes",
            "does not exist or one of its queried reference-property",
            "DualWrite",
        ]
        if any(indicator in result_str for indicator in transient_indicators):
            logger.warning(f"Transient error removing {member} from {identity}, will retry...")
            return "RETRY"

        return "FAILED"

    async def remove_distribution_group_member(
        self,
        identity: str,
        member: str,
    ) -> bool:
        """Remove a member from a distribution group or mail-enabled security group.

        Args:
            identity: Group name, alias, or email address
            member: Member email address to remove

        Returns:
            True if successful
        """
        result = self._remove_member_with_retry(identity, member)

        if result == "SUCCESS":
            logger.info(f"Removed {member} from {identity}")
            return True

        logger.error(f"Failed to remove {member} from {identity}")
        return False

    async def close(self) -> None:
        """Close the client (no-op for subprocess approach)."""
        pass
