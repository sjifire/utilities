"""Entra ID user management operations."""

import logging
import secrets
import string
from dataclasses import dataclass

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.models.on_premises_extension_attributes import (
    OnPremisesExtensionAttributes,
)
from msgraph.generated.models.password_profile import PasswordProfile
from msgraph.generated.models.user import User
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from sjifire.core.msgraph_client import get_graph_client

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
    # Extension attributes (1-15 available, we use 1-3)
    extension_attribute1: str | None = None  # Rank
    extension_attribute2: str | None = None  # EVIP
    extension_attribute3: str | None = None  # Positions (comma-delimited)

    @property
    def is_active(self) -> bool:
        """Check if user account is enabled."""
        return self.account_enabled


class EntraUserManager:
    """Manage users in Entra ID."""

    def __init__(self, domain: str = "sjifire.org") -> None:
        """Initialize the user manager.

        Args:
            domain: Email domain for generating UPNs
        """
        self.domain = domain
        self.client: GraphServiceClient = get_graph_client()

    async def get_users(
        self,
        include_disabled: bool = False,
    ) -> list[EntraUser]:
        """Fetch users from Entra ID.

        Args:
            include_disabled: If True, include disabled accounts

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
                users.append(self._to_entra_user(user))

        # Handle pagination
        while result and result.odata_next_link:
            result = await self.client.users.with_url(result.odata_next_link).get()
            if result and result.value:
                for user in result.value:
                    if not include_disabled and not user.account_enabled:
                        continue
                    users.append(self._to_entra_user(user))

        logger.info(f"Found {len(users)} users")
        return users

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
        if user.on_premises_extension_attributes:
            ext = user.on_premises_extension_attributes
            ext_attr1 = ext.extension_attribute1
            ext_attr2 = ext.extension_attribute2
            ext_attr3 = ext.extension_attribute3

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
        if extension_attribute1 or extension_attribute2 or extension_attribute3:
            ext_attrs = OnPremisesExtensionAttributes(
                extension_attribute1=extension_attribute1,
                extension_attribute2=extension_attribute2,
                extension_attribute3=extension_attribute3,
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
                )
                if has_ext_attrs:
                    retry_ext_attrs = OnPremisesExtensionAttributes()
                    if extension_attribute1 is not None:
                        retry_ext_attrs.extension_attribute1 = extension_attribute1
                    if extension_attribute2 is not None:
                        retry_ext_attrs.extension_attribute2 = extension_attribute2
                    if extension_attribute3 is not None:
                        retry_ext_attrs.extension_attribute3 = extension_attribute3
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
        first = first_name.lower().replace(" ", "").replace("'", "")
        last = last_name.lower().replace(" ", "").replace("'", "")
        return f"{first}.{last}@{self.domain}"
