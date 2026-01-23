"""Sync Aladtec members to Entra ID (Azure AD)."""

import logging

from msgraph import GraphServiceClient
from msgraph.generated.models.password_profile import PasswordProfile
from msgraph.generated.models.user import User
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from sjifire.aladtec.models import Member
from sjifire.core.graph_client import get_graph_client

logger = logging.getLogger(__name__)


class EntraSync:
    """Sync members from Aladtec to Entra ID."""

    def __init__(self, domain: str = "sjifire.org") -> None:
        """Initialize the sync.

        Args:
            domain: Email domain for generating UPNs
        """
        self.domain = domain
        self.client: GraphServiceClient = get_graph_client()

    async def get_existing_users(self) -> dict[str, User]:
        """Fetch existing users from Entra ID.

        Returns:
            Dict mapping email to User object
        """
        logger.info("Fetching existing Entra ID users")

        query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
            select=["id", "displayName", "givenName", "surname", "mail", "userPrincipalName"],
            top=999,
        )
        request_config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration(
            query_parameters=query_params,
        )

        users = await self.client.users.get(request_configuration=request_config)

        user_map = {}
        if users and users.value:
            for user in users.value:
                if user.mail:
                    user_map[user.mail.lower()] = user
                if user.user_principal_name:
                    user_map[user.user_principal_name.lower()] = user

        logger.info(f"Found {len(user_map)} existing users")
        return user_map

    def _generate_upn(self, member: Member) -> str:
        """Generate a user principal name for a member.

        Args:
            member: Aladtec member

        Returns:
            UPN in format firstname.lastname@domain
        """
        if member.email and member.email.endswith(f"@{self.domain}"):
            return member.email

        first = member.first_name.lower().replace(" ", "")
        last = member.last_name.lower().replace(" ", "")
        return f"{first}.{last}@{self.domain}"

    async def sync_members(
        self,
        members: list[Member],
        dry_run: bool = False,
    ) -> dict:
        """Sync Aladtec members to Entra ID.

        Args:
            members: List of members from Aladtec
            dry_run: If True, don't make changes, just report what would happen

        Returns:
            Dict with sync results
        """
        logger.info(f"Syncing {len(members)} members (dry_run={dry_run})")

        existing_users = await self.get_existing_users()

        results = {
            "created": [],
            "updated": [],
            "skipped": [],
            "errors": [],
        }

        for member in members:
            if not member.email:
                logger.warning(f"Skipping {member.display_name} - no email")
                results["skipped"].append({
                    "member": member.display_name,
                    "reason": "no email",
                })
                continue

            upn = self._generate_upn(member)
            email_lower = member.email.lower()

            try:
                existing = existing_users.get(email_lower) or existing_users.get(upn.lower())
                if existing:
                    # User exists - check if update needed
                    if self._needs_update(existing, member):
                        if not dry_run and existing.id:
                            await self._update_user(existing.id, member)
                        results["updated"].append({
                            "member": member.display_name,
                            "email": member.email,
                        })
                        action = "Would update" if dry_run else "Updated"
                        logger.info(f"{action}: {member.display_name}")
                    else:
                        results["skipped"].append({
                            "member": member.display_name,
                            "reason": "no changes",
                        })
                else:
                    # New user
                    if not dry_run:
                        await self._create_user(member, upn)
                    results["created"].append({
                        "member": member.display_name,
                        "email": member.email,
                        "upn": upn,
                    })
                    action = "Would create" if dry_run else "Created"
                    logger.info(f"{action}: {member.display_name}")

            except Exception as e:
                logger.error(f"Error syncing {member.display_name}: {e}")
                results["errors"].append({
                    "member": member.display_name,
                    "error": str(e),
                })

        return results

    def _needs_update(self, existing: User, member: Member) -> bool:
        """Check if an existing user needs to be updated.

        Args:
            existing: Existing Entra user
            member: Aladtec member data

        Returns:
            True if user needs updating
        """
        if existing.given_name != member.first_name:
            return True
        return existing.surname != member.last_name

    async def _create_user(self, member: Member, upn: str) -> User | None:
        """Create a new user in Entra ID.

        Args:
            member: Aladtec member
            upn: User principal name

        Returns:
            Created User object or None
        """
        password_profile = PasswordProfile(
            force_change_password_next_sign_in=True,
            password=self._generate_temp_password(),
        )

        user = User(
            account_enabled=True,
            display_name=member.display_name,
            given_name=member.first_name,
            surname=member.last_name,
            user_principal_name=upn,
            mail=member.email,
            mail_nickname=upn.split("@")[0],
            password_profile=password_profile,
        )

        return await self.client.users.post(user)

    async def _update_user(self, user_id: str, member: Member) -> None:
        """Update an existing user in Entra ID.

        Args:
            user_id: Entra user ID
            member: Aladtec member with updated data
        """
        user = User(
            display_name=member.display_name,
            given_name=member.first_name,
            surname=member.last_name,
        )

        await self.client.users.by_user_id(user_id).patch(user)

    def _generate_temp_password(self) -> str:
        """Generate a temporary password for new users.

        Returns:
            Random temporary password
        """
        import secrets
        import string

        alphabet = string.ascii_letters + string.digits + "!@#$%"
        return "".join(secrets.choice(alphabet) for _ in range(16))
