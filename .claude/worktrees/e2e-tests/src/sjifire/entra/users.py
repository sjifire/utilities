"""Entra ID user management operations."""

import logging
import secrets
import string
from dataclasses import dataclass
from uuid import UUID

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.models.on_premises_extension_attributes import (
    OnPremisesExtensionAttributes,
)
from msgraph.generated.models.password_profile import PasswordProfile
from msgraph.generated.models.user import User
from msgraph.generated.users.item.assign_license.assign_license_post_request_body import (
    AssignLicensePostRequestBody,
)
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from sjifire.core.config import get_domain
from sjifire.core.msgraph_client import get_graph_client
from sjifire.core.normalize import normalize_name_part

logger = logging.getLogger(__name__)


@dataclass
class EntraUser:
    """Represents an Entra ID user."""

    id: str
    display_name: str | None
    first_name: str | None
    last_name: str | None
    email: str | None
    upn: str | None
    employee_id: str | None
    account_enabled: bool = True
    job_title: str | None = None
    mobile_phone: str | None = None
    business_phones: list[str] | None = None
    office_location: str | None = None
    employee_hire_date: str | None = None
    employee_type: str | None = None
    personal_email: str | None = None
    department: str | None = None
    company_name: str | None = None
    # Extension attributes (1-15 available, we use 1-4)
    extension_attribute1: str | None = None  # Rank
    extension_attribute2: str | None = None  # EVIP
    extension_attribute3: str | None = None  # Positions (comma-delimited)
    extension_attribute4: str | None = None  # Schedules (comma-delimited)

    @property
    def is_active(self) -> bool:
        """Check if user account is enabled."""
        return self.account_enabled

    @property
    def is_employee(self) -> bool:
        """Check if user has an employee ID (is an employee vs shared mailbox/resource)."""
        return bool(self.employee_id)

    @property
    def has_phone(self) -> bool:
        """Check if user has a mobile phone number."""
        return bool(self.mobile_phone)

    @property
    def positions(self) -> set[str]:
        """Get positions from extensionAttribute3 as a set."""
        if not self.extension_attribute3:
            return set()
        return {p.strip() for p in self.extension_attribute3.split(",") if p.strip()}

    @property
    def schedules(self) -> set[str]:
        """Get schedules from extensionAttribute4 as a set."""
        if not self.extension_attribute4:
            return set()
        return {s.strip() for s in self.extension_attribute4.split(",") if s.strip()}

    @property
    def rank(self) -> str | None:
        """Get rank from extensionAttribute1."""
        return self.extension_attribute1

    @property
    def evip(self) -> str | None:
        """Get EVIP expiration date from extensionAttribute2."""
        return self.extension_attribute2

    @property
    def has_valid_evip(self) -> bool:
        """Check if user has valid (non-expired) EVIP certification.

        Returns True if extensionAttribute2 contains a date that is today or in the future.
        """
        if not self.extension_attribute2:
            return False
        from datetime import date, datetime

        try:
            # Try common date formats
            evip_str = self.extension_attribute2.strip()
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
                try:
                    evip_date = datetime.strptime(evip_str, fmt).date()
                    return evip_date >= date.today()
                except ValueError:
                    continue
            return False
        except Exception:
            return False

    @property
    def is_operational(self) -> bool:
        """Check if user has any operational positions.

        Operational positions are those that respond to emergencies
        (Firefighter, Apparatus Operator, Support, Wildland Firefighter, Marine roles).
        """
        from sjifire.core.config import get_org_config

        return bool(self.positions & get_org_config().operational_positions)

    @property
    def station_number(self) -> str | None:
        """Extract station number from office_location ('Station XX' → 'XX').

        Supports both 'Station 31' and plain '31' formats.
        """
        if not self.office_location:
            return None
        loc = self.office_location.strip()
        # Handle plain number
        if loc.isdigit():
            return loc
        # Handle "Station 31" format
        if loc.lower().startswith("station "):
            num = loc[8:].strip()
            if num.isdigit():
                return num
        return None

    @property
    def work_group(self) -> str | None:
        """Alias for employee_type (protocol compatibility with GroupMember)."""
        return self.employee_type


class EntraUserManager:
    """Manage users in Entra ID."""

    def __init__(self, domain: str | None = None) -> None:
        """Initialize the user manager.

        Args:
            domain: Email domain for generating UPNs (defaults to org config)
        """
        self.domain = domain or get_domain()
        self.client: GraphServiceClient = get_graph_client()

    async def get_users(
        self,
        include_disabled: bool = False,
        employees_only: bool = False,
    ) -> list[EntraUser]:
        """Fetch users from Entra ID.

        Args:
            include_disabled: If True, include disabled accounts
            employees_only: If True, only return users with employee IDs

        Returns:
            List of EntraUser objects
        """
        logger.info("Fetching Entra ID users")

        query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
            select=[
                "id",
                "displayName",
                "givenName",
                "surname",
                "mail",
                "userPrincipalName",
                "employeeId",
                "accountEnabled",
                "jobTitle",
                "mobilePhone",
                "businessPhones",
                "officeLocation",
                "employeeHireDate",
                "employeeType",
                "otherMails",
                "department",
                "companyName",
                "onPremisesExtensionAttributes",
            ],
        )
        config = RequestConfiguration(query_parameters=query_params)
        result = await self.client.users.get(request_configuration=config)

        users = []
        if result and result.value:
            for user in result.value:
                if not include_disabled and not user.account_enabled:
                    continue
                if employees_only and not user.employee_id:
                    continue
                users.append(self._to_entra_user(user))

        # Handle pagination
        while result and result.odata_next_link:
            result = await self.client.users.with_url(result.odata_next_link).get()
            if result and result.value:
                for user in result.value:
                    if not include_disabled and not user.account_enabled:
                        continue
                    if employees_only and not user.employee_id:
                        continue
                    users.append(self._to_entra_user(user))

        logger.info(f"Found {len(users)} users")
        return users

    async def get_employees(self, include_disabled: bool = False) -> list[EntraUser]:
        """Fetch only employees (users with employee IDs) from Entra ID.

        This is a convenience method that excludes shared mailboxes, room resources,
        and other non-employee accounts.

        Args:
            include_disabled: If True, include disabled accounts

        Returns:
            List of EntraUser objects with employee IDs
        """
        return await self.get_users(include_disabled=include_disabled, employees_only=True)

    def _to_entra_user(self, user: User) -> EntraUser:
        """Convert MS Graph User to EntraUser.

        Args:
            user: MS Graph User object

        Returns:
            EntraUser object
        """
        # Convert hire date to string if present
        hire_date_str = None
        if user.employee_hire_date:
            hire_date_str = user.employee_hire_date.isoformat()

        # Extract extension attributes
        ext_attr1 = None
        ext_attr2 = None
        ext_attr3 = None
        ext_attr4 = None
        if user.on_premises_extension_attributes:
            ext = user.on_premises_extension_attributes
            ext_attr1 = ext.extension_attribute1
            ext_attr2 = ext.extension_attribute2
            ext_attr3 = ext.extension_attribute3
            ext_attr4 = ext.extension_attribute4

        return EntraUser(
            id=user.id or "",
            display_name=user.display_name,
            first_name=user.given_name,
            last_name=user.surname,
            email=user.mail,
            upn=user.user_principal_name,
            employee_id=user.employee_id,
            account_enabled=user.account_enabled or False,
            job_title=user.job_title,
            mobile_phone=user.mobile_phone,
            business_phones=user.business_phones,
            office_location=user.office_location,
            employee_hire_date=hire_date_str,
            employee_type=user.employee_type,
            personal_email=user.other_mails[0] if user.other_mails else None,
            department=user.department,
            company_name=user.company_name,
            extension_attribute1=ext_attr1,
            extension_attribute2=ext_attr2,
            extension_attribute3=ext_attr3,
            extension_attribute4=ext_attr4,
        )

    async def get_user_by_upn(self, upn: str) -> EntraUser | None:
        """Fetch a single user by UPN.

        Args:
            upn: User principal name

        Returns:
            EntraUser or None if not found
        """
        try:
            user = await self.client.users.by_user_id(upn).get()
            if user:
                return self._to_entra_user(user)
        except Exception as e:
            logger.debug(f"User not found: {upn} - {e}")
        return None

    async def create_user(
        self,
        display_name: str,
        first_name: str,
        last_name: str,
        upn: str,
        email: str | None = None,
        employee_id: str | None = None,
        temp_password: str | None = None,
        job_title: str | None = None,
        mobile_phone: str | None = None,
        business_phones: list[str] | None = None,
        office_location: str | None = None,
        employee_hire_date: str | None = None,
        employee_type: str | None = None,
        personal_email: str | None = None,
        department: str | None = None,
        company_name: str | None = None,
        extension_attribute1: str | None = None,
        extension_attribute2: str | None = None,
        extension_attribute3: str | None = None,
        extension_attribute4: str | None = None,
    ) -> EntraUser | None:
        """Create a new user in Entra ID.

        Args:
            display_name: Full display name
            first_name: Given name
            last_name: Surname
            upn: User principal name (email@domain)
            email: Mail address (optional, defaults to UPN)
            employee_id: Employee ID (optional)
            temp_password: Temporary password (auto-generated if not provided)
            job_title: Job title (optional)
            mobile_phone: Mobile phone number (optional)
            business_phones: Business phone numbers (optional)
            office_location: Office location / station assignment (optional)
            employee_hire_date: Hire date in ISO format (optional)
            employee_type: Employee type / work group (optional)
            personal_email: Personal email address (optional)
            department: Department (optional)
            company_name: Company name (optional)
            extension_attribute1: Rank (optional)
            extension_attribute2: EVIP date (optional)
            extension_attribute3: Positions comma-delimited (optional)
            extension_attribute4: Schedules comma-delimited (optional)

        Returns:
            Created EntraUser or None on failure
        """
        if not temp_password:
            temp_password = self._generate_temp_password()

        password_profile = PasswordProfile(
            force_change_password_next_sign_in=True,
            password=temp_password,
        )

        # Parse hire date if provided
        hire_date = None
        if employee_hire_date:
            from datetime import datetime

            try:
                hire_date = datetime.fromisoformat(employee_hire_date.replace("/", "-"))
            except ValueError:
                logger.warning(f"Invalid hire date format: {employee_hire_date}")

        # Build extension attributes if any are provided
        ext_attrs = None
        has_ext_attrs = (
            extension_attribute1
            or extension_attribute2
            or extension_attribute3
            or extension_attribute4
        )
        if has_ext_attrs:
            ext_attrs = OnPremisesExtensionAttributes(
                extension_attribute1=extension_attribute1,
                extension_attribute2=extension_attribute2,
                extension_attribute3=extension_attribute3,
                extension_attribute4=extension_attribute4,
            )

        user = User(
            account_enabled=True,
            display_name=display_name,
            given_name=first_name,
            surname=last_name,
            user_principal_name=upn,
            mail=email or upn,
            mail_nickname=upn.split("@")[0],
            password_profile=password_profile,
            employee_id=employee_id,
            job_title=job_title,
            mobile_phone=mobile_phone,
            business_phones=business_phones or [],
            office_location=office_location,
            employee_hire_date=hire_date,
            employee_type=employee_type,
            other_mails=[personal_email] if personal_email else [],
            department=department,
            company_name=company_name,
            on_premises_extension_attributes=ext_attrs,
        )

        try:
            created = await self.client.users.post(user)
            if created:
                logger.info(f"Created user: {display_name} ({upn})")
                return self._to_entra_user(created)
        except Exception as e:
            logger.error(f"Failed to create user {upn}: {e}")

        return None

    async def update_user(
        self,
        user_id: str,
        display_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        employee_id: str | None = None,
        job_title: str | None = None,
        mobile_phone: str | None = None,
        business_phones: list[str] | None = None,
        office_location: str | None = None,
        employee_hire_date: str | None = None,
        employee_type: str | None = None,
        personal_email: str | None = None,
        department: str | None = None,
        company_name: str | None = None,
        extension_attribute1: str | None = None,
        extension_attribute2: str | None = None,
        extension_attribute3: str | None = None,
        extension_attribute4: str | None = None,
    ) -> bool:
        """Update an existing user in Entra ID.

        Args:
            user_id: Entra user ID
            display_name: New display name (optional)
            first_name: New given name (optional)
            last_name: New surname (optional)
            employee_id: New employee ID (optional)
            job_title: New job title (optional)
            mobile_phone: New mobile phone (optional)
            business_phones: New business phones (optional)
            office_location: New office location (optional)
            employee_hire_date: New hire date in ISO format (optional)
            employee_type: New employee type / work group (optional)
            personal_email: Personal email address (optional)
            department: Department (optional)
            company_name: Company name (optional)
            extension_attribute1: Rank (optional)
            extension_attribute2: EVIP date (optional)
            extension_attribute3: Positions comma-delimited (optional)
            extension_attribute4: Schedules comma-delimited (optional)

        Returns:
            True if successful
        """
        # Parse hire date if provided
        hire_date = None
        if employee_hire_date:
            from datetime import datetime

            try:
                hire_date = datetime.fromisoformat(employee_hire_date.replace("/", "-"))
            except ValueError:
                logger.warning(f"Invalid hire date format: {employee_hire_date}")

        # Build user with non-None values, use additional_data for fields to clear
        user = User(
            display_name=display_name,
            given_name=first_name,
            surname=last_name,
        )

        # For fields where None means "clear the field", use additional_data
        # SDK doesn't send None values, but additional_data with null does work
        fields_to_clear: dict = {}

        # Handle each optional field - set if value, clear if None
        if employee_id is not None:
            user.employee_id = employee_id
        else:
            fields_to_clear["employeeId"] = None

        if job_title is not None:
            user.job_title = job_title
        else:
            fields_to_clear["jobTitle"] = None

        if mobile_phone is not None:
            user.mobile_phone = mobile_phone
        else:
            fields_to_clear["mobilePhone"] = None

        if business_phones is not None:
            user.business_phones = business_phones

        if office_location is not None:
            user.office_location = office_location
        else:
            fields_to_clear["officeLocation"] = None

        if hire_date is not None:
            user.employee_hire_date = hire_date

        if employee_type is not None:
            user.employee_type = employee_type
        else:
            fields_to_clear["employeeType"] = None

        if personal_email is not None:
            user.other_mails = [personal_email]
        else:
            fields_to_clear["otherMails"] = []

        if department is not None:
            user.department = department
        else:
            fields_to_clear["department"] = None

        if company_name is not None:
            user.company_name = company_name
        else:
            fields_to_clear["companyName"] = None

        # Handle extension attributes
        ext_attrs = OnPremisesExtensionAttributes()
        has_ext_attrs = False
        if extension_attribute1 is not None:
            ext_attrs.extension_attribute1 = extension_attribute1
            has_ext_attrs = True
        if extension_attribute2 is not None:
            ext_attrs.extension_attribute2 = extension_attribute2
            has_ext_attrs = True
        if extension_attribute3 is not None:
            ext_attrs.extension_attribute3 = extension_attribute3
            has_ext_attrs = True
        if extension_attribute4 is not None:
            ext_attrs.extension_attribute4 = extension_attribute4
            has_ext_attrs = True
        if has_ext_attrs:
            user.on_premises_extension_attributes = ext_attrs

        # Apply fields to clear via additional_data
        if fields_to_clear:
            user.additional_data = fields_to_clear

        try:
            await self.client.users.by_user_id(user_id).patch(user)
            logger.info(f"Updated user: {user_id}")
            return True
        except Exception as e:
            error_str = str(e)
            # Check if this is a permission error (likely admin user) and we have a phone
            is_permission_error = "Authorization_RequestDenied" in error_str or "403" in error_str
            if is_permission_error and (mobile_phone or personal_email or business_phones):
                # For admin users, phone fields and otherMails require elevated privileges
                skipped = []
                if mobile_phone:
                    skipped.append("mobilePhone")
                if business_phones:
                    skipped.append("businessPhones")
                if personal_email:
                    skipped.append("otherMails")
                logger.warning(
                    f"Permission denied for {user_id}, retrying without: {', '.join(skipped)}"
                )
                # Retry without sensitive fields (mobilePhone, businessPhones, otherMails)
                # Remove those from fields_to_clear too
                retry_fields_to_clear = {
                    k: v
                    for k, v in fields_to_clear.items()
                    if k not in ("mobilePhone", "businessPhones", "otherMails")
                }
                user_retry = User(
                    display_name=display_name,
                    given_name=first_name,
                    surname=last_name,
                )
                if employee_id is not None:
                    user_retry.employee_id = employee_id
                if job_title is not None:
                    user_retry.job_title = job_title
                # Skip business_phones - requires elevated privileges for admin users
                if office_location is not None:
                    user_retry.office_location = office_location
                if hire_date is not None:
                    user_retry.employee_hire_date = hire_date
                if employee_type is not None:
                    user_retry.employee_type = employee_type
                if department is not None:
                    user_retry.department = department
                if company_name is not None:
                    user_retry.company_name = company_name
                # Include extension attributes in retry
                has_ext_attrs = (
                    extension_attribute1 is not None
                    or extension_attribute2 is not None
                    or extension_attribute3 is not None
                    or extension_attribute4 is not None
                )
                if has_ext_attrs:
                    retry_ext_attrs = OnPremisesExtensionAttributes()
                    if extension_attribute1 is not None:
                        retry_ext_attrs.extension_attribute1 = extension_attribute1
                    if extension_attribute2 is not None:
                        retry_ext_attrs.extension_attribute2 = extension_attribute2
                    if extension_attribute3 is not None:
                        retry_ext_attrs.extension_attribute3 = extension_attribute3
                    if extension_attribute4 is not None:
                        retry_ext_attrs.extension_attribute4 = extension_attribute4
                    user_retry.on_premises_extension_attributes = retry_ext_attrs
                if retry_fields_to_clear:
                    user_retry.additional_data = retry_fields_to_clear
                try:
                    await self.client.users.by_user_id(user_id).patch(user_retry)
                    logger.info(
                        f"Updated user (partial): {user_id} (skipped: {', '.join(skipped)})"
                    )
                    return True
                except Exception as retry_error:
                    logger.error(
                        f"Failed to update user {user_id} even without "
                        f"{', '.join(skipped)}: {retry_error}"
                    )
                    return False

            logger.error(f"Failed to update user {user_id}: {e}")
            return False

    async def disable_user(self, user_id: str) -> bool:
        """Disable a user account in Entra ID.

        Args:
            user_id: Entra user ID or UPN

        Returns:
            True if successful
        """
        user = User(account_enabled=False)

        try:
            await self.client.users.by_user_id(user_id).patch(user)
            logger.info(f"Disabled user: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to disable user {user_id}: {e}")
            return False

    async def enable_user(self, user_id: str) -> bool:
        """Enable a user account in Entra ID.

        Args:
            user_id: Entra user ID or UPN

        Returns:
            True if successful
        """
        user = User(account_enabled=True)

        try:
            await self.client.users.by_user_id(user_id).patch(user)
            logger.info(f"Enabled user: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to enable user {user_id}: {e}")
            return False

    async def get_user_licenses(self, user_id: str) -> list[str]:
        """Get list of license SKU IDs assigned to a user.

        Args:
            user_id: Entra user ID or UPN

        Returns:
            List of SKU ID strings (GUIDs)
        """
        try:
            result = await self.client.users.by_user_id(user_id).license_details.get()
            if result and result.value:
                return [str(lic.sku_id) for lic in result.value if lic.sku_id]
            return []
        except Exception as e:
            logger.error(f"Failed to get licenses for {user_id}: {e}")
            return []

    async def remove_all_licenses(self, user_id: str) -> bool:
        """Remove all licenses from a user.

        Args:
            user_id: Entra user ID or UPN

        Returns:
            True if successful (or user had no licenses)
        """
        # First get the user's current licenses
        license_ids = await self.get_user_licenses(user_id)

        if not license_ids:
            logger.info(f"User {user_id} has no licenses to remove")
            return True

        try:
            request_body = AssignLicensePostRequestBody(
                add_licenses=[],
                remove_licenses=[UUID(lic_id) for lic_id in license_ids],
            )
            await self.client.users.by_user_id(user_id).assign_license.post(request_body)
            logger.info(f"Removed {len(license_ids)} license(s) from user: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove licenses from {user_id}: {e}")
            return False

    async def disable_and_remove_licenses(self, user_id: str) -> tuple[bool, bool]:
        """Disable a user account and remove all their licenses.

        Args:
            user_id: Entra user ID or UPN

        Returns:
            Tuple of (disable_success, license_removal_success)
        """
        disable_success = await self.disable_user(user_id)
        license_success = await self.remove_all_licenses(user_id)
        return disable_success, license_success

    def _generate_temp_password(self) -> str:
        """Generate a temporary password for new users.

        Returns:
            Random temporary password meeting complexity requirements
        """
        # Ensure at least one of each required character type
        password = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!@#$%"),
        ]
        # Fill the rest randomly
        alphabet = string.ascii_letters + string.digits + "!@#$%"
        password.extend(secrets.choice(alphabet) for _ in range(12))
        # Shuffle to avoid predictable positions
        secrets.SystemRandom().shuffle(password)
        return "".join(password)

    def generate_upn(self, first_name: str, last_name: str) -> str:
        """Generate a user principal name.

        Args:
            first_name: Given name
            last_name: Surname

        Returns:
            UPN in format firstname.lastname@domain
        """
        first = normalize_name_part(first_name)
        last = normalize_name_part(last_name)
        return f"{first}.{last}@{self.domain}"
