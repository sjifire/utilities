"""Entra ID user management operations."""

import logging
import secrets
import string
from dataclasses import dataclass

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
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
        return EntraUser(
            id=user.id or "",
            display_name=user.display_name,
            first_name=user.given_name,
            last_name=user.surname,
            email=user.mail,
            upn=user.user_principal_name,
            employee_id=user.employee_id,
            account_enabled=user.account_enabled or False,
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

        Returns:
            Created EntraUser or None on failure
        """
        if not temp_password:
            temp_password = self._generate_temp_password()

        password_profile = PasswordProfile(
            force_change_password_next_sign_in=True,
            password=temp_password,
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
    ) -> bool:
        """Update an existing user in Entra ID.

        Args:
            user_id: Entra user ID
            display_name: New display name (optional)
            first_name: New given name (optional)
            last_name: New surname (optional)
            employee_id: New employee ID (optional)

        Returns:
            True if successful
        """
        user = User(
            display_name=display_name,
            given_name=first_name,
            surname=last_name,
            employee_id=employee_id,
        )

        try:
            await self.client.users.by_user_id(user_id).patch(user)
            logger.info(f"Updated user: {user_id}")
            return True
        except Exception as e:
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
