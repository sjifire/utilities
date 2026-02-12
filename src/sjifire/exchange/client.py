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

import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sjifire.core.config import get_exchange_credentials

logger = logging.getLogger(__name__)

# Retry configuration for transient Azure AD sync errors
TRANSIENT_ERROR_PATTERNS = [
    r"Resource .* does not exist",
    r"object in sync between Azure Active Directory and Exchange Online",
    r"transient",
]
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = [10, 20, 30]  # Delays between retries


def is_transient_error(error_msg: str) -> bool:
    """Check if an error message indicates a transient Azure AD sync error."""
    return any(re.search(pattern, error_msg, re.IGNORECASE) for pattern in TRANSIENT_ERROR_PATTERNS)


def extract_member_from_error(error_msg: str) -> str | None:
    """Extract member email from an error message like 'Add user@domain.com: error...'."""
    match = re.match(r"Add ([^:]+):", error_msg)
    if match:
        return match.group(1).strip()
    return None


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
        organization: str | None = None,
    ) -> None:
        """Initialize the Exchange Online client.

        Args:
            certificate_thumbprint: Thumbprint of installed certificate (Windows)
            certificate_path: Path to .pfx certificate file (cross-platform)
            certificate_password: Password for the .pfx file
            organization: The organization domain (overrides env config)
        """
        creds = get_exchange_credentials()
        self.tenant_id = creds.tenant_id
        self.client_id = creds.client_id
        self.organization = organization or creds.organization
        # Use passed params if provided, otherwise use from credentials
        self.certificate_thumbprint = certificate_thumbprint or creds.certificate_thumbprint
        self.certificate_path = certificate_path or creds.certificate_path
        self.certificate_password = certificate_password or creds.certificate_password

    def _build_connect_command(self) -> str:
        """Build the Connect-ExchangeOnline command."""
        # Suppress banner output with *>$null to prevent it from mixing with JSON output
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
                f"-Organization '{self.organization}' *>$null"
            )
        elif self.certificate_thumbprint:
            # Windows: Use installed certificate by thumbprint
            return (
                f"Connect-ExchangeOnline "
                f"-AppId '{self.client_id}' "
                f"-CertificateThumbprint '{self.certificate_thumbprint}' "
                f"-Organization '{self.organization}' *>$null"
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
            # Disconnect (suppress output)
            "Disconnect-ExchangeOnline -Confirm:$false *>$null",
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

    async def get_group_with_members(self, identity: str) -> tuple[ExchangeGroup | None, list[str]]:
        """Get a distribution group and its members in a single call.

        This is more efficient than calling get_distribution_group and
        get_distribution_group_members separately (one connection instead of two).

        Args:
            identity: Group name, alias, or email address

        Returns:
            Tuple of (ExchangeGroup or None, list of member emails)
        """
        # Build a script that outputs both group info and members as JSON
        # Note: Commands are joined with "; " so each must be a complete statement
        commands = [
            f"$group = Get-DistributionGroup -Identity '{identity}' -ErrorAction SilentlyContinue",
            (
                "if ($group) { "
                "$members = Get-DistributionGroupMember -Identity $group.Identity "
                "| Select-Object PrimarySmtpAddress; "
                "@{ Group = $group | Select-Object Identity, DisplayName, PrimarySmtpAddress, "
                "RecipientTypeDetails; Members = $members } | ConvertTo-Json -Depth 3 "
                "}"
            ),
        ]

        result = self._run_powershell(commands)
        if not result or not isinstance(result, dict):
            return None, []

        # Parse group info
        group_data = result.get("Group")
        group = None
        if group_data and isinstance(group_data, dict) and "Identity" in group_data:
            group = ExchangeGroup(
                identity=group_data.get("Identity", identity),
                display_name=group_data.get("DisplayName", ""),
                primary_smtp_address=group_data.get("PrimarySmtpAddress", ""),
                group_type=group_data.get("RecipientTypeDetails", ""),
            )

        # Parse members
        members: list[str] = []
        members_data = result.get("Members")
        if members_data:
            if isinstance(members_data, dict):
                # Single member
                if "PrimarySmtpAddress" in members_data:
                    members.append(members_data["PrimarySmtpAddress"].lower())
            elif isinstance(members_data, list):
                members.extend(
                    m["PrimarySmtpAddress"].lower()
                    for m in members_data
                    if isinstance(m, dict) and "PrimarySmtpAddress" in m
                )

        return group, members

    async def create_mail_enabled_security_group(
        self,
        name: str,
        display_name: str,
        alias: str,
        primary_smtp_address: str | None = None,
        members: list[str] | None = None,
        managed_by: str | None = None,
        notes: str | None = None,
    ) -> ExchangeGroup | None:
        """Create a new mail-enabled security group.

        Args:
            name: Internal name of the group
            display_name: Display name shown in address book
            alias: Email alias (without domain)
            primary_smtp_address: Full email address (optional)
            members: List of member email addresses to add
            managed_by: Email of the group owner/manager
            notes: Description/notes for the group

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

        if notes:
            # Escape single quotes in notes
            escaped_notes = notes.replace("'", "''")
            cmd_parts.append(f"-Notes '{escaped_notes}'")

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

    async def update_distribution_group_description(
        self,
        identity: str,
        description: str,
    ) -> bool:
        """Update the description of a distribution group.

        Args:
            identity: Group name, alias, or email address
            description: New description for the group

        Returns:
            True if successful
        """
        # Escape single quotes in description
        escaped_description = description.replace("'", "''")
        commands = [
            f"Set-DistributionGroup -Identity '{identity}' -Description '{escaped_description}'",
            "Write-Output 'SUCCESS'",
        ]

        result = self._run_powershell(commands, parse_json=False)
        if result and "SUCCESS" in str(result):
            logger.info(f"Updated description for {identity}")
            return True

        logger.error(f"Failed to update description for {identity}")
        return False

    async def update_distribution_group_managed_by(
        self,
        identity: str,
        managed_by: str,
    ) -> bool:
        """Update the ManagedBy (owner) of a distribution group.

        Args:
            identity: Group name, alias, or email address
            managed_by: Email address of the owner

        Returns:
            True if successful
        """
        commands = [
            f"Set-DistributionGroup -Identity '{identity}' -ManagedBy '{managed_by}' "
            "-BypassSecurityGroupManagerCheck",
            "Write-Output 'SUCCESS'",
        ]

        result = self._run_powershell(commands, parse_json=False)
        if result and "SUCCESS" in str(result):
            logger.info(f"Updated ManagedBy for {identity} to {managed_by}")
            return True

        logger.error(f"Failed to update ManagedBy for {identity}")
        return False

    async def set_distribution_group_aliases(
        self,
        identity: str,
        aliases: list[str],
        domain: str = "sjifire.org",
    ) -> bool:
        """Set email aliases for a distribution group.

        This sets the secondary email addresses (aliases) for the group.
        The primary SMTP address is preserved.

        Args:
            identity: Group name, alias, or email address
            aliases: List of alias names without domain (e.g., ["ff", "firefighter"])
            domain: Domain for the aliases

        Returns:
            True if successful
        """
        if not aliases:
            return True

        # Build the EmailAddresses array
        # Format: @{Add="smtp:alias1@domain","smtp:alias2@domain"}
        alias_addresses = [f"smtp:{alias}@{domain}" for alias in aliases]
        addresses_str = '","'.join(alias_addresses)

        set_cmd = (
            f"Set-DistributionGroup -Identity '{identity}' "
            f'-EmailAddresses @{{Add="{addresses_str}"}}'
        )
        commands = [set_cmd, "Write-Output 'SUCCESS'"]

        result = self._run_powershell(commands, parse_json=False)
        if result and "SUCCESS" in str(result):
            logger.info(f"Added aliases to {identity}: {', '.join(aliases)}")
            return True

        logger.error(f"Failed to add aliases to {identity}: {result}")
        return False

    async def update_group_settings(
        self,
        identity: str,
        description: str | None = None,
        managed_by: str | None = None,
    ) -> bool:
        """Update group description and/or managed_by in a single call.

        This is more efficient than calling update_distribution_group_description
        and update_distribution_group_managed_by separately.

        Args:
            identity: Group name, alias, or email address
            description: New description (optional)
            managed_by: New owner email (optional)

        Returns:
            True if all updates successful
        """
        if not description and not managed_by:
            return True

        commands = []

        if description:
            escaped_description = description.replace("'", "''")
            commands.append(
                f"Set-DistributionGroup -Identity '{identity}' -Description '{escaped_description}'"
            )

        if managed_by:
            commands.append(
                f"Set-DistributionGroup -Identity '{identity}' "
                f"-ManagedBy '{managed_by}' -BypassSecurityGroupManagerCheck"
            )

        commands.append("Write-Output 'SUCCESS'")

        result = self._run_powershell(commands, parse_json=False)
        if result and "SUCCESS" in str(result):
            if description:
                logger.info(f"Updated description for {identity}")
            if managed_by:
                logger.info(f"Updated ManagedBy for {identity} to {managed_by}")
            return True

        logger.error(f"Failed to update settings for {identity}")
        return False

    async def delete_distribution_group(self, identity: str) -> bool:
        """Delete a distribution group or mail-enabled security group.

        Args:
            identity: Group name, alias, or email address

        Returns:
            True if successful (or group didn't exist)
        """
        commands = [
            f"Remove-DistributionGroup -Identity '{identity}' "
            "-BypassSecurityGroupManagerCheck -Confirm:$false -ErrorAction Stop",
            "Write-Output 'SUCCESS'",
        ]

        result = self._run_powershell(commands, parse_json=False)
        if result and "SUCCESS" in str(result):
            logger.info(f"Deleted distribution group: {identity}")
            return True

        # Check if group just doesn't exist (not an error)
        if result is None:
            # Could be "not found" error - check if group exists
            check = await self.get_distribution_group(identity)
            if check is None:
                logger.info(f"Distribution group already deleted: {identity}")
                return True

        logger.error(f"Failed to delete distribution group: {identity}")
        return False

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
        commands = [
            f"Add-DistributionGroupMember -Identity '{identity}' "
            f"-Member '{member}' -BypassSecurityGroupManagerCheck -ErrorAction Stop",
            "Write-Output 'SUCCESS'",
        ]

        result = self._run_powershell(commands, parse_json=False)
        result_str = str(result) if result else ""

        if "SUCCESS" in result_str:
            logger.info(f"Added {member} to {identity}")
            return True

        if "already a member" in result_str.lower():
            logger.debug(f"{member} is already a member of {identity}")
            return True

        logger.error(f"Failed to add {member} to {identity}")
        return False

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
        commands = [
            f"Remove-DistributionGroupMember -Identity '{identity}' "
            f"-Member '{member}' -BypassSecurityGroupManagerCheck "
            "-Confirm:$false -ErrorAction Stop",
            "Write-Output 'SUCCESS'",
        ]

        result = self._run_powershell(commands, parse_json=False)
        if result and "SUCCESS" in str(result):
            logger.info(f"Removed {member} from {identity}")
            return True

        logger.error(f"Failed to remove {member} from {identity}")
        return False

    async def sync_group_members(
        self,
        identity: str,
        members_to_add: list[str],
        members_to_remove: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Add and remove multiple members in a single call.

        This batches all member changes into one PowerShell connection,
        significantly faster than individual add/remove calls.

        Args:
            identity: Group name, alias, or email address
            members_to_add: List of member emails to add
            members_to_remove: List of member emails to remove

        Returns:
            Tuple of (added, removed, errors) - lists of emails
        """
        if not members_to_add and not members_to_remove:
            return [], [], []

        # Build PowerShell script that tracks results
        commands = [
            "$added = @()",
            "$removed = @()",
            "$errors = @()",
        ]

        # Add members
        commands.extend(
            f"try {{ "
            f"Add-DistributionGroupMember -Identity '{identity}' "
            f"-Member '{member}' -BypassSecurityGroupManagerCheck -ErrorAction Stop; "
            f"$added += '{member}' "
            f"}} catch {{ "
            f"if ($_.Exception.Message -like '*already a member*') {{ $added += '{member}' }} "
            f"else {{ $errors += '{member}: ' + $_.Exception.Message }} "
            f"}}"
            for member in members_to_add
        )

        # Remove members
        commands.extend(
            f"try {{ "
            f"Remove-DistributionGroupMember -Identity '{identity}' "
            f"-Member '{member}' -BypassSecurityGroupManagerCheck "
            f"-Confirm:$false -ErrorAction Stop; "
            f"$removed += '{member}' "
            f"}} catch {{ "
            f"$errors += '{member}: ' + $_.Exception.Message "
            f"}}"
            for member in members_to_remove
        )

        # Output results as JSON
        commands.append(
            "@{ Added = $added; Removed = $removed; Errors = $errors } | ConvertTo-Json"
        )

        result = self._run_powershell(commands)

        added: list[str] = []
        removed: list[str] = []
        errors: list[str] = []

        if result and isinstance(result, dict):
            added_data = result.get("Added", [])
            if isinstance(added_data, str):
                added = [added_data] if added_data else []
            elif isinstance(added_data, list):
                added = [str(m) for m in added_data if m]

            removed_data = result.get("Removed", [])
            if isinstance(removed_data, str):
                removed = [removed_data] if removed_data else []
            elif isinstance(removed_data, list):
                removed = [str(m) for m in removed_data if m]

            errors_data = result.get("Errors", [])
            if isinstance(errors_data, str):
                errors = [errors_data] if errors_data else []
            elif isinstance(errors_data, list):
                errors = [str(e) for e in errors_data if e]

        # Log results
        for member in added:
            logger.info(f"Added {member} to {identity}")
        for member in removed:
            logger.info(f"Removed {member} from {identity}")
        for error in errors:
            logger.error(f"Member sync error for {identity}: {error}")

        return added, removed, errors

    async def sync_group(
        self,
        identity: str,
        description: str | None = None,
        managed_by: str | None = None,
        target_members: list[str] | None = None,
    ) -> dict:
        """Sync an entire group in a single PowerShell connection.

        This is the most efficient way to sync a group - ONE connection that:
        1. Gets current group info and members
        2. Updates description and managed_by
        3. Adds/removes members to match target

        Args:
            identity: Group email address
            description: Description to set (optional)
            managed_by: Owner email to set (optional)
            target_members: List of member emails that should be in the group

        Returns:
            Dict with keys: group, current_members, added, removed, errors
        """
        target_members = target_members or []

        # Build a comprehensive PowerShell script
        script_parts = [
            # Initialize result tracking
            (
                "$result = @{ group = $null; current_members = @(); "
                "added = @(); removed = @(); errors = @() }"
            ),
            # Get group
            f"$group = Get-DistributionGroup -Identity '{identity}' -ErrorAction SilentlyContinue",
            "if (-not $group) { $result | ConvertTo-Json -Depth 3; return }",
            # Store group info
            (
                "$result.group = $group | Select-Object Identity, DisplayName, "
                "PrimarySmtpAddress, RecipientTypeDetails"
            ),
            # Get current members
            (
                "$members = Get-DistributionGroupMember -Identity $group.Identity "
                "| Select-Object PrimarySmtpAddress"
            ),
            (
                "$result.current_members = @($members "
                "| ForEach-Object { $_.PrimarySmtpAddress.ToLower() })"
            ),
        ]

        # Update description if provided
        if description:
            escaped_desc = description.replace("'", "''")
            script_parts.append(
                f"try {{ Set-DistributionGroup -Identity '{identity}' "
                f"-Description '{escaped_desc}' }} "
                f"catch {{ $result.errors += 'Description: ' + $_.Exception.Message }}"
            )

        # Update managed_by if provided
        if managed_by:
            script_parts.append(
                f"try {{ Set-DistributionGroup -Identity '{identity}' "
                f"-ManagedBy '{managed_by}' -BypassSecurityGroupManagerCheck }} "
                f"catch {{ $result.errors += 'ManagedBy: ' + $_.Exception.Message }}"
            )

        # Build target members array in PowerShell
        if target_members:
            members_ps_array = "@('" + "', '".join(m.lower() for m in target_members) + "')"
            script_parts.append(f"$targetMembers = {members_ps_array}")
        else:
            script_parts.append("$targetMembers = @()")

        # Calculate and perform member changes
        script_parts.extend(
            [
                # Find members to add/remove
                "$toAdd = $targetMembers | Where-Object { $_ -notin $result.current_members }",
                "$toRemove = $result.current_members | Where-Object { $_ -notin $targetMembers }",
                # Add members loop
                (
                    "foreach ($member in $toAdd) { "
                    f"try {{ Add-DistributionGroupMember -Identity '{identity}' -Member $member "
                    "-BypassSecurityGroupManagerCheck -ErrorAction Stop; "
                    "$result.added += $member } "
                    "catch { if ($_.Exception.Message -like '*already a member*') "
                    "{ $result.added += $member } "
                    'else { $result.errors += "Add $member`: " + $_.Exception.Message } } }'
                ),
                # Remove members loop
                (
                    "foreach ($member in $toRemove) { "
                    f"try {{ Remove-DistributionGroupMember -Identity '{identity}' -Member $member "
                    "-BypassSecurityGroupManagerCheck -Confirm:$false -ErrorAction Stop; "
                    "$result.removed += $member } "
                    'catch { $result.errors += "Remove $member`: " + $_.Exception.Message } }'
                ),
                # Output result
                "$result | ConvertTo-Json -Depth 3",
            ]
        )

        result = self._run_powershell(script_parts)

        # Parse result
        if not result or not isinstance(result, dict):
            return {
                "group": None,
                "current_members": [],
                "added": [],
                "removed": [],
                "errors": ["Failed to execute sync script"],
            }

        # Normalize arrays (PowerShell returns single items as scalars)
        for key in ["current_members", "added", "removed", "errors"]:
            val = result.get(key, [])
            if val is None:
                result[key] = []
            elif isinstance(val, str):
                result[key] = [val] if val else []
            elif not isinstance(val, list):
                result[key] = [val]

        # Retry transient errors with exponential backoff
        errors = result.get("errors", [])
        transient_failures = []
        permanent_errors = []

        for error in errors:
            if is_transient_error(error):
                member = extract_member_from_error(error)
                if member:
                    transient_failures.append(member)
                else:
                    permanent_errors.append(error)
            else:
                permanent_errors.append(error)

        if transient_failures:
            logger.info(f"Retrying {len(transient_failures)} transient failures for {identity}")

            for attempt, delay in enumerate(RETRY_DELAYS_SECONDS[:MAX_RETRY_ATTEMPTS]):
                if not transient_failures:
                    break

                logger.info(
                    f"Retry attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS} "
                    f"after {delay}s delay for {len(transient_failures)} members"
                )
                await asyncio.sleep(delay)

                # Retry adding failed members
                still_failing = []
                for member in transient_failures:
                    add_script = [
                        f"try {{ Add-DistributionGroupMember -Identity '{identity}' "
                        f"-Member '{member}' -BypassSecurityGroupManagerCheck -ErrorAction Stop; "
                        f"'SUCCESS' }} catch {{ $_.Exception.Message }}"
                    ]
                    retry_result = self._run_powershell(add_script)
                    # Check for SUCCESS in string or raw dict output
                    if "SUCCESS" in str(retry_result):
                        logger.info(f"Retry succeeded: Added {member} to {identity}")
                        result["added"].append(member)
                    elif is_transient_error(str(retry_result)):
                        still_failing.append(member)
                    else:
                        permanent_errors.append(f"Add {member}: {retry_result}")

                transient_failures = still_failing

            # Any remaining transient failures become permanent errors
            permanent_errors.extend(
                f"Add {member}: Failed after {MAX_RETRY_ATTEMPTS} retries"
                for member in transient_failures
            )

        result["errors"] = permanent_errors
        return result

    async def set_unified_group_welcome_message(self, identity: str, enabled: bool) -> bool:
        """Enable or disable welcome messages for a unified (M365) group.

        Args:
            identity: Group email or name
            enabled: True to enable welcome messages, False to disable

        Returns:
            True if successful
        """
        enabled_str = "$true" if enabled else "$false"
        cmd = f"Set-UnifiedGroup -Identity '{identity}' "
        cmd += f"-UnifiedGroupWelcomeMessageEnabled:{enabled_str}"
        commands = [cmd]

        result = await asyncio.to_thread(self._run_powershell, commands, parse_json=False)

        if result is None:
            logger.error(f"Failed to set welcome message for {identity}")
            return False

        status = "enabled" if enabled else "disabled"
        logger.info(f"Welcome messages {status} for {identity}")
        return True

    async def set_unified_group_calendar_settings(
        self,
        identity: str,
        auto_subscribe: bool = True,
        always_subscribe_calendar: bool = True,
    ) -> bool:
        """Set calendar visibility settings for a unified (M365) group.

        Args:
            identity: Group email or name
            auto_subscribe: Auto-subscribe new members to group updates
            always_subscribe_calendar: Always subscribe members to calendar events

        Returns:
            True if successful
        """
        auto_str = "$true" if auto_subscribe else "$false"
        always_str = "$true" if always_subscribe_calendar else "$false"
        cmd = f"Set-UnifiedGroup -Identity '{identity}' "
        cmd += f"-AutoSubscribeNewMembers:{auto_str} "
        cmd += f"-AlwaysSubscribeMembersToCalendarEvents:{always_str}"
        commands = [cmd]

        result = await asyncio.to_thread(self._run_powershell, commands, parse_json=False)

        if result is None:
            logger.error(f"Failed to set calendar settings for {identity}")
            return False

        logger.info(f"Calendar auto-subscribe settings applied for {identity}")
        return True

    async def close(self) -> None:
        """Close the client (no-op for subprocess approach)."""
        pass
