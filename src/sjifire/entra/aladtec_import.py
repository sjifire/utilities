"""Import Aladtec members into Entra ID."""

import asyncio
import logging
from dataclasses import dataclass, field

from sjifire.aladtec.models import Member
from sjifire.core.config import load_entra_sync_config
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

    def __init__(
        self,
        domain: str | None = None,
        company_name: str | None = None,
        license_sku: str | None = None,
    ) -> None:
        """Initialize the importer.

        Args:
            domain: Email domain for generating UPNs (loaded from config if not provided)
            company_name: Company name for Entra ID users (loaded from config if not provided)
            license_sku: License SKU ID to assign to newly created users (optional)
        """
        config = load_entra_sync_config()
        self.domain = domain or config.domain
        self.company_name = company_name or config.company_name
        self.skip_emails = {e.lower() for e in config.skip_emails}
        self.license_sku = license_sku
        self.user_manager = EntraUserManager(domain=self.domain)

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
            "Importing %d members (dry_run=%s, disable_inactive=%s)",
            len(members),
            dry_run,
            disable_inactive,
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
                logger.error("Error processing %s: %s", member.display_name, e)
                result.errors.append(
                    {
                        "member": member.display_name,
                        "error": str(e),
                    }
                )

        logger.info("Import complete: %s", result.summary())
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
        # Skip members without business domain email
        if not member.email or not member.email.endswith(f"@{self.domain}"):
            result.skipped.append(
                {
                    "member": member.display_name,
                    "reason": f"no @{self.domain} email",
                    "email": member.email,
                }
            )
            return

        # Skip emails in the skip list (test/api accounts)
        if member.email.lower() in self.skip_emails:
            result.skipped.append(
                {
                    "member": member.display_name,
                    "reason": "email in skip list",
                    "email": member.email,
                }
            )
            return

        # Find existing Entra user — try email, then UPN (from username), then display name
        email_lower = member.email.lower()

        existing = user_by_email.get(email_lower) or user_by_upn.get(email_lower)

        if not existing and member.username:
            generated_upn = f"{member.username.lower()}@{self.domain}"
            existing = user_by_upn.get(generated_upn)

        if not existing:
            existing = user_by_name.get(member.display_name.lower())

        # Warn if Aladtec email doesn't match the Entra user's email/UPN
        if existing:
            entra_email = (existing.email or existing.upn or "").lower()
            if entra_email and entra_email != email_lower:
                logger.warning(
                    "Email mismatch for %s: Aladtec email='%s' but Entra='%s'. "
                    "Update email in Aladtec.",
                    member.display_name,
                    member.email,
                    existing.email or existing.upn,
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
                if dry_run:
                    result.disabled.append(
                        {
                            "member": member.display_name,
                            "email": member.email,
                            "user_id": existing.id,
                            "action": "would disable and remove licenses",
                        }
                    )
                    logger.info("Would disable and remove licenses: %s", member.display_name)
                else:
                    disable_ok, license_ok = await self.user_manager.disable_and_remove_licenses(
                        existing.id
                    )
                    if disable_ok:
                        result.disabled.append(
                            {
                                "member": member.display_name,
                                "email": member.email,
                                "user_id": existing.id,
                                "licenses_removed": license_ok,
                            }
                        )
                        if license_ok:
                            logger.info("Disabled and removed licenses: %s", member.display_name)
                        else:
                            logger.warning(
                                "Disabled %s but failed to remove licenses", member.display_name
                            )
                    else:
                        result.errors.append(
                            {
                                "member": member.display_name,
                                "error": "Failed to disable user in Entra ID",
                            }
                        )
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
            if dry_run:
                result.updated.append(
                    {
                        "member": member.display_name,
                        "email": member.email,
                    }
                )
                logger.info("Would update: %s", member.display_name)
            else:
                # Build business phones list from home_phone if available
                business_phones = [member.home_phone] if member.home_phone else None

                # Build display name with rank prefix (e.g., "Captain Kyle Dodd")
                display_name = self._build_display_name(member)

                # Build positions and schedules as comma-delimited strings
                # Use empty string (not None) to clear the field if empty
                positions_str = ",".join(member.positions) if member.positions else ""
                schedules_str = ",".join(member.schedules) if member.schedules else ""

                success = await self.user_manager.update_user(
                    user_id=existing.id,
                    display_name=display_name,
                    first_name=member.first_name,
                    last_name=member.last_name,
                    employee_id=member.employee_id,
                    job_title=member.job_title,
                    mobile_phone=member.phone,
                    business_phones=business_phones,
                    office_location=member.office_location,
                    employee_hire_date=member.date_hired,
                    employee_type=member.work_group,
                    personal_email=member.personal_email,
                    company_name=self.company_name,
                    # Use empty string to clear if None, so Graph API clears the field
                    extension_attribute1=member.rank or "",
                    extension_attribute2=member.evip or "",
                    extension_attribute3=positions_str,
                    extension_attribute4=schedules_str,
                )
                if success:
                    result.updated.append(
                        {
                            "member": member.display_name,
                            "email": member.email,
                        }
                    )
                    logger.info("Updated: %s", member.display_name)
                else:
                    result.errors.append(
                        {
                            "member": member.display_name,
                            "error": "Failed to update user in Entra ID",
                        }
                    )
        else:
            result.skipped.append(
                {
                    "member": member.display_name,
                    "reason": "no changes needed",
                }
            )

        # Ensure license is assigned (catches failed assignments from prior runs)
        await self._ensure_license(existing, member, dry_run)

    async def _handle_new_user(
        self,
        member: Member,
        result: ImportResult,
        dry_run: bool,
    ) -> None:
        """Handle a new user (not in Entra).

        Args:
            member: Aladtec member
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

        # UPN is the email address (already validated as non-None in _process_member)
        upn = member.email
        assert upn is not None  # noqa: S101

        if dry_run:
            result.created.append(
                {
                    "member": member.display_name,
                    "email": member.email,
                    "upn": upn,
                    "license_sku": self.license_sku,
                }
            )
            if self.license_sku:
                logger.info("Would create and assign license: %s (%s)", member.display_name, upn)
            else:
                logger.info("Would create: %s (%s)", member.display_name, upn)
        else:
            # Build business phones list from home_phone if available
            business_phones = [member.home_phone] if member.home_phone else None

            # Build display name with rank prefix (e.g., "Captain Kyle Dodd")
            display_name = self._build_display_name(member)

            # Build positions and schedules as comma-delimited strings
            positions_str = ",".join(member.positions) if member.positions else None
            schedules_str = ",".join(member.schedules) if member.schedules else None

            created_user = await self.user_manager.create_user(
                display_name=display_name,
                first_name=member.first_name,
                last_name=member.last_name,
                upn=upn,
                email=member.email,
                employee_id=member.employee_id,
                job_title=member.job_title,
                mobile_phone=member.phone,
                business_phones=business_phones,
                office_location=member.office_location,
                employee_hire_date=member.date_hired,
                employee_type=member.work_group,
                personal_email=member.personal_email,
                company_name=self.company_name,
                extension_attribute1=member.rank,
                extension_attribute2=member.evip,
                extension_attribute3=positions_str,
                extension_attribute4=schedules_str,
            )
            if created_user:
                # Assign license if configured — retry with backoff since Entra
                # may not have fully replicated the new user object yet
                license_ok = True
                if self.license_sku:
                    license_ok = await self._assign_license_with_retry(
                        created_user.id, self.license_sku, member.display_name
                    )

                result.created.append(
                    {
                        "member": member.display_name,
                        "email": member.email,
                        "upn": upn,
                        "license_assigned": license_ok if self.license_sku else None,
                    }
                )
                logger.info("Created: %s (%s)", member.display_name, upn)
            else:
                result.errors.append(
                    {
                        "member": member.display_name,
                        "error": "Failed to create user in Entra ID",
                    }
                )

    def _build_display_name(self, member: Member) -> str:
        """Build display name with rank prefix if applicable.

        Args:
            member: Aladtec member

        Returns:
            Display name, e.g. "Chief Michael Hartzell" or "Kyle Dodd"
        """
        if member.display_rank:
            return f"{member.display_rank} {member.first_name} {member.last_name}"
        return member.display_name

    async def _assign_license_with_retry(
        self, user_id: str, sku_id: str, display_name: str, max_attempts: int = 3
    ) -> bool:
        """Assign a license with retry and backoff for newly created users.

        Entra ID may not have fully replicated the user object immediately
        after creation, causing license assignment to fail with a
        ValidationException.
        """
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                delay = 5 * attempt  # 10s, 15s
                logger.info(
                    "Retrying license assignment for %s (attempt %d/%d, waiting %ds)",
                    display_name,
                    attempt,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)

            ok = await self.user_manager.assign_license(user_id, sku_id)
            if ok:
                return True

        logger.warning(
            "Created %s but failed to assign license after %d attempts",
            display_name,
            max_attempts,
        )
        return False

    async def _ensure_license(
        self, existing: EntraUser, member: Member, dry_run: bool
    ) -> bool | None:
        """Check license and assign if missing. Returns None if no license_sku configured."""
        if not self.license_sku:
            return None

        licenses = await self.user_manager.get_user_licenses(existing.id)
        if licenses:
            # User has at least one license (may be the configured SKU or a
            # higher-tier plan like Business Standard) — don't touch it
            return True

        # No licenses at all on an active user
        if dry_run:
            logger.info("Would assign missing license to %s", member.display_name)
            return False

        # Ensure usageLocation is set (required for license assignment)
        await self.user_manager.set_usage_location(existing.id)

        ok = await self.user_manager.assign_license(existing.id, self.license_sku)
        if ok:
            logger.info("Assigned missing license to %s", member.display_name)
        else:
            logger.warning("Failed to assign missing license to %s", member.display_name)
        return ok

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
        # Check display name with rank prefix
        expected_display = self._build_display_name(member)
        if existing.display_name != expected_display:
            return True
        # Aladtec is authoritative - if values differ, update (even if Aladtec is blank)
        if existing.employee_id != member.employee_id:
            return True
        if existing.job_title != member.job_title:
            return True
        if existing.mobile_phone != member.phone:
            return True
        if existing.office_location != member.office_location:
            return True
        # Work group goes to employee_type
        if existing.employee_type != member.work_group:
            return True
        # Check home_phone against business_phones
        if member.home_phone:
            existing_phones = existing.business_phones or []
            if member.home_phone not in existing_phones:
                return True
        # Check hire date - only update if Aladtec date <= Entra date
        if member.date_hired:
            member_date = member.date_hired.replace("/", "-")
            if existing.employee_hire_date:
                # Only update if dates differ AND Aladtec is older or equal
                if not existing.employee_hire_date.startswith(member_date):
                    # Compare dates - Aladtec should be <= Entra
                    if member_date <= existing.employee_hire_date[:10]:
                        return True
                    else:
                        # Aladtec date is newer - flag but don't update
                        entra_date = existing.employee_hire_date[:10]
                        logger.warning(
                            "Hire date conflict for %s: Aladtec=%s is newer than Entra=%s",
                            member.display_name,
                            member_date,
                            entra_date,
                        )
            else:
                # Entra has no hire date, safe to set
                return True
        # Check personal email
        if existing.personal_email != member.personal_email:
            return True
        # Check company name
        if existing.company_name != self.company_name:
            return True
        # Check extension attributes (normalize None to "" for comparison)
        # extensionAttribute1 = rank
        if (existing.extension_attribute1 or "") != (member.rank or ""):
            return True
        # extensionAttribute2 = EVIP
        if (existing.extension_attribute2 or "") != (member.evip or ""):
            return True
        # extensionAttribute3 = positions (comma-delimited)
        positions_str = ",".join(member.positions) if member.positions else ""
        if (existing.extension_attribute3 or "") != positions_str:
            return True
        # extensionAttribute4 = schedules (comma-delimited)
        schedules_str = ",".join(member.schedules) if member.schedules else ""
        return (existing.extension_attribute4 or "") != schedules_str
