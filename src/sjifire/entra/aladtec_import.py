"""Import Aladtec members into Entra ID."""

import logging
from dataclasses import dataclass, field

from sjifire.aladtec.models import Member
from sjifire.entra.users import EntraUser, EntraUserManager

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    """Results from an Aladtec to Entra import operation."""

    created: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    disabled: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        """Total number of members processed."""
        return (
            len(self.created)
            + len(self.updated)
            + len(self.disabled)
            + len(self.skipped)
            + len(self.errors)
        )

    def summary(self) -> str:
        """Return a summary string."""
        return (
            f"Created: {len(self.created)}, "
            f"Updated: {len(self.updated)}, "
            f"Disabled: {len(self.disabled)}, "
            f"Skipped: {len(self.skipped)}, "
            f"Errors: {len(self.errors)}"
        )


class AladtecImporter:
    """Import Aladtec members into Entra ID."""

    def __init__(self, domain: str = "sjifire.org") -> None:
        """Initialize the importer.

        Args:
            domain: Email domain for generating UPNs
        """
        self.domain = domain
        self.user_manager = EntraUserManager(domain=domain)

    async def import_members(
        self,
        members: list[Member],
        dry_run: bool = False,
        disable_inactive: bool = False,
    ) -> ImportResult:
        """Import Aladtec members to Entra ID.

        Args:
            members: List of members from Aladtec
            dry_run: If True, don't make changes, just report what would happen
            disable_inactive: If True, disable Entra accounts for inactive members

        Returns:
            ImportResult with details of the operation
        """
        logger.info(
            f"Importing {len(members)} members (dry_run={dry_run}, "
            f"disable_inactive={disable_inactive})"
        )

        # Build lookup of existing Entra users
        existing_users = await self.user_manager.get_users(include_disabled=True)
        user_by_email = {u.email.lower(): u for u in existing_users if u.email}
        user_by_upn = {u.upn.lower(): u for u in existing_users if u.upn}
        user_by_name = {
            f"{u.first_name} {u.last_name}".lower(): u
            for u in existing_users
            if u.first_name and u.last_name
        }

        result = ImportResult()

        for member in members:
            try:
                await self._process_member(
                    member=member,
                    user_by_email=user_by_email,
                    user_by_upn=user_by_upn,
                    user_by_name=user_by_name,
                    result=result,
                    dry_run=dry_run,
                    disable_inactive=disable_inactive,
                )
            except Exception as e:
                logger.error(f"Error processing {member.display_name}: {e}")
                result.errors.append(
                    {
                        "member": member.display_name,
                        "error": str(e),
                    }
                )

        logger.info(f"Import complete: {result.summary()}")
        return result

    async def _process_member(
        self,
        member: Member,
        user_by_email: dict[str, EntraUser],
        user_by_upn: dict[str, EntraUser],
        user_by_name: dict[str, EntraUser],
        result: ImportResult,
        dry_run: bool,
        disable_inactive: bool,
    ) -> None:
        """Process a single member for import.

        Args:
            member: Aladtec member
            user_by_email: Lookup dict of Entra users by email
            user_by_upn: Lookup dict of Entra users by UPN
            user_by_name: Lookup dict of Entra users by name
            result: ImportResult to update
            dry_run: If True, don't make changes
            disable_inactive: If True, disable inactive member accounts
        """
        # Skip members without sjifire.org email
        if not member.email or not member.email.endswith(f"@{self.domain}"):
            result.skipped.append(
                {
                    "member": member.display_name,
                    "reason": f"no @{self.domain} email",
                    "email": member.email,
                }
            )
            return

        # Find existing Entra user
        email_lower = member.email.lower()
        upn = self.user_manager.generate_upn(member.first_name, member.last_name)
        upn_lower = upn.lower()

        existing = (
            user_by_email.get(email_lower)
            or user_by_upn.get(email_lower)
            or user_by_upn.get(upn_lower)
            or user_by_name.get(member.display_name.lower())
        )

        if existing:
            await self._handle_existing_user(
                member=member,
                existing=existing,
                result=result,
                dry_run=dry_run,
                disable_inactive=disable_inactive,
            )
        else:
            await self._handle_new_user(
                member=member,
                upn=upn,
                result=result,
                dry_run=dry_run,
            )

    async def _handle_existing_user(
        self,
        member: Member,
        existing: EntraUser,
        result: ImportResult,
        dry_run: bool,
        disable_inactive: bool,
    ) -> None:
        """Handle an existing Entra user.

        Args:
            member: Aladtec member
            existing: Existing Entra user
            result: ImportResult to update
            dry_run: If True, don't make changes
            disable_inactive: If True, disable inactive member accounts
        """
        # Check if member is inactive and should be disabled
        if not member.is_active and disable_inactive:
            if existing.account_enabled:
                action = "Would disable" if dry_run else "Disabled"
                if not dry_run:
                    await self.user_manager.disable_user(existing.id)
                result.disabled.append(
                    {
                        "member": member.display_name,
                        "email": member.email,
                        "user_id": existing.id,
                    }
                )
                logger.info(f"{action}: {member.display_name}")
            else:
                result.skipped.append(
                    {
                        "member": member.display_name,
                        "reason": "already disabled",
                    }
                )
            return

        # Check if update needed
        if self._needs_update(existing, member):
            action = "Would update" if dry_run else "Updated"
            if not dry_run:
                await self.user_manager.update_user(
                    user_id=existing.id,
                    display_name=member.display_name,
                    first_name=member.first_name,
                    last_name=member.last_name,
                    employee_id=member.employee_id,
                )
            result.updated.append(
                {
                    "member": member.display_name,
                    "email": member.email,
                }
            )
            logger.info(f"{action}: {member.display_name}")
        else:
            result.skipped.append(
                {
                    "member": member.display_name,
                    "reason": "no changes needed",
                }
            )

    async def _handle_new_user(
        self,
        member: Member,
        upn: str,
        result: ImportResult,
        dry_run: bool,
    ) -> None:
        """Handle a new user (not in Entra).

        Args:
            member: Aladtec member
            upn: Generated UPN for the user
            result: ImportResult to update
            dry_run: If True, don't make changes
        """
        # Don't create accounts for inactive members
        if not member.is_active:
            result.skipped.append(
                {
                    "member": member.display_name,
                    "reason": "inactive member, not creating account",
                }
            )
            return

        action = "Would create" if dry_run else "Created"
        if not dry_run:
            await self.user_manager.create_user(
                display_name=member.display_name,
                first_name=member.first_name,
                last_name=member.last_name,
                upn=upn,
                email=member.email,
                employee_id=member.employee_id,
            )
        result.created.append(
            {
                "member": member.display_name,
                "email": member.email,
                "upn": upn,
            }
        )
        logger.info(f"{action}: {member.display_name} ({upn})")

    def _needs_update(self, existing: EntraUser, member: Member) -> bool:
        """Check if an existing user needs to be updated.

        Args:
            existing: Existing Entra user
            member: Aladtec member data

        Returns:
            True if user needs updating
        """
        if existing.first_name != member.first_name:
            return True
        if existing.last_name != member.last_name:
            return True
        if existing.display_name != member.display_name:
            return True
        return bool(member.employee_id and existing.employee_id != member.employee_id)
