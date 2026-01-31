"""Sync logic for Entra ID to iSpyFire."""

import logging
from dataclasses import dataclass, field

from sjifire.aladtec.models import OPERATIONAL_POSITIONS
from sjifire.entra.users import EntraUser
from sjifire.ispyfire.models import ISpyFirePerson

logger = logging.getLogger(__name__)


@dataclass
class SyncComparison:
    """Results of comparing Entra users with iSpyFire people."""

    # People in iSpyFire
    ispyfire_people: list[ISpyFirePerson] = field(default_factory=list)

    # Entra users that should be in iSpyFire (operational)
    entra_operational: list[EntraUser] = field(default_factory=list)

    # Actions needed
    to_add: list[EntraUser] = field(default_factory=list)  # In Entra, not in iSpyFire
    to_remove: list[ISpyFirePerson] = field(default_factory=list)  # In iSpyFire, not in Entra
    to_update: list[tuple[EntraUser, ISpyFirePerson]] = field(
        default_factory=list
    )  # Need field updates
    matched: list[tuple[EntraUser, ISpyFirePerson]] = field(default_factory=list)  # Already in sync

    # Skipped users
    skipped_no_operational: list[EntraUser] = field(default_factory=list)  # No operational position
    skipped_no_phone: list[EntraUser] = field(default_factory=list)  # No cell phone


def get_user_positions(user: EntraUser) -> set[str]:
    """Extract positions from Entra user's extensionAttribute3.

    Args:
        user: Entra user object

    Returns:
        Set of position strings
    """
    positions_str = user.extension_attribute3
    if not positions_str:
        return set()

    return {p.strip() for p in positions_str.split(",") if p.strip()}


def is_operational(user: EntraUser) -> bool:
    """Check if user has any operational positions.

    Args:
        user: Entra user object

    Returns:
        True if user has at least one operational position
    """
    positions = get_user_positions(user)
    return bool(positions & OPERATIONAL_POSITIONS)


def normalize_phone(phone: str | None) -> str | None:
    """Normalize phone number for comparison.

    Args:
        phone: Phone number string

    Returns:
        Normalized phone (digits only) or None
    """
    if not phone:
        return None
    # Keep only digits
    digits = "".join(c for c in phone if c.isdigit())
    return digits if digits else None


def normalize_email(email: str | None) -> str | None:
    """Normalize email for comparison.

    Args:
        email: Email address

    Returns:
        Lowercase email or None
    """
    if not email:
        return None
    return email.lower().strip()


def fields_need_update(user: EntraUser, person: ISpyFirePerson) -> list[str]:
    """Check which fields differ between Entra user and iSpyFire person.

    Args:
        user: Entra user
        person: iSpyFire person

    Returns:
        List of field names that differ
    """
    differences = []

    # First name
    if user.first_name and user.first_name != person.first_name:
        differences.append("firstName")

    # Last name
    if user.last_name and user.last_name != person.last_name:
        differences.append("lastName")

    # Cell phone
    entra_phone = normalize_phone(user.mobile_phone)
    ispy_phone = normalize_phone(person.cell_phone)
    if entra_phone and entra_phone != ispy_phone:
        differences.append("cellPhone")

    # Title/Rank (from extensionAttribute1)
    entra_rank = user.extension_attribute1
    if entra_rank and entra_rank != person.title:
        differences.append("title")

    return differences


def is_managed_email(email: str | None, domain: str = "sjifire.org") -> bool:
    """Check if email belongs to the managed domain.

    Args:
        email: Email address to check
        domain: Domain to match (default: sjifire.org)

    Returns:
        True if email ends with the managed domain
    """
    if not email:
        return False
    return email.lower().strip().endswith(f"@{domain}")


def normalize_name(first: str | None, last: str | None) -> str:
    """Normalize a name for comparison.

    Args:
        first: First name
        last: Last name

    Returns:
        Normalized "first last" string, lowercase and stripped
    """
    first_clean = (first or "").lower().strip()
    last_clean = (last or "").lower().strip()
    return f"{first_clean} {last_clean}"


def compare_entra_to_ispyfire(
    entra_users: list[EntraUser],
    ispyfire_people: list[ISpyFirePerson],
    managed_domain: str = "sjifire.org",
) -> SyncComparison:
    """Compare Entra users with iSpyFire people to determine sync actions.

    Only syncs users with emails in the managed domain (default: sjifire.org).
    Users with other email domains (e.g., sanjuanems.org, apparatus accounts)
    are ignored and will not be added, updated, or removed.

    Users without a cell phone are skipped for addition.
    Duplicate detection uses both email and name matching.

    Args:
        entra_users: List of all Entra users
        ispyfire_people: List of all iSpyFire people
        managed_domain: Only sync users with this email domain

    Returns:
        SyncComparison with categorized results
    """
    comparison = SyncComparison(ispyfire_people=ispyfire_people)

    # Filter to managed domain users first
    managed_users = [u for u in entra_users if is_managed_email(u.email, managed_domain)]

    # Track users skipped due to no operational position
    for user in managed_users:
        if not is_operational(user):
            comparison.skipped_no_operational.append(user)

    # Filter to operational Entra users with managed domain email only
    operational_users = [u for u in managed_users if is_operational(u)]
    comparison.entra_operational = operational_users

    logger.info(f"Found {len(operational_users)} operational @{managed_domain} users in Entra")
    if comparison.skipped_no_operational:
        logger.info(
            f"Skipped {len(comparison.skipped_no_operational)} users without operational positions"
        )
    logger.info(f"Found {len(ispyfire_people)} people in iSpyFire")

    # Build lookup by email for iSpyFire people (only managed domain)
    ispyfire_by_email: dict[str, ISpyFirePerson] = {}
    for person in ispyfire_people:
        if person.email and is_managed_email(person.email, managed_domain):
            ispyfire_by_email[normalize_email(person.email)] = person

    # Build lookup by name for ALL iSpyFire people (to detect duplicates with different emails)
    ispyfire_by_name: dict[str, ISpyFirePerson] = {}
    for person in ispyfire_people:
        name_key = normalize_name(person.first_name, person.last_name)
        if name_key.strip():
            ispyfire_by_name[name_key] = person

    # Track which iSpyFire people are matched
    matched_ispyfire_ids: set[str] = set()

    # Check each operational Entra user
    for user in operational_users:
        user_email = normalize_email(user.email)
        if not user_email:
            logger.warning(f"Skipping user without email: {user.display_name}")
            continue

        # Find matching iSpyFire person by email (managed domain only)
        person = ispyfire_by_email.get(user_email)

        if person is None:
            # Check if person exists with different email (by name)
            user_name_key = normalize_name(user.first_name, user.last_name)
            person_by_name = ispyfire_by_name.get(user_name_key)

            if person_by_name is not None:
                # Duplicate exists with different email - skip adding
                logger.debug(
                    f"Skipping {user.display_name}: duplicate exists as {person_by_name.email}"
                )
                continue

            # Not in iSpyFire - check if they have a cell phone before adding
            if not user.mobile_phone:
                comparison.skipped_no_phone.append(user)
                continue

            comparison.to_add.append(user)
        else:
            matched_ispyfire_ids.add(person.id)

            # Check if fields need updating
            diff_fields = fields_need_update(user, person)
            if diff_fields:
                comparison.to_update.append((user, person))
            else:
                comparison.matched.append((user, person))

    # Find iSpyFire people not matched to any operational Entra user
    # Only consider people with managed domain emails for removal
    # Exclude utility accounts (marked in iSpyFire) from removal
    for person in ispyfire_people:
        if (
            person.id not in matched_ispyfire_ids
            and person.is_active
            and is_managed_email(person.email, managed_domain)
            and not person.is_utility
        ):
            comparison.to_remove.append(person)

    logger.info("Comparison complete:")
    logger.info(f"  - Matched: {len(comparison.matched)}")
    logger.info(f"  - To add: {len(comparison.to_add)}")
    logger.info(f"  - To update: {len(comparison.to_update)}")
    logger.info(f"  - To remove: {len(comparison.to_remove)}")
    if comparison.skipped_no_phone:
        logger.info(f"  - Skipped (no phone): {len(comparison.skipped_no_phone)}")

    return comparison


def entra_user_to_ispyfire_person(user: EntraUser) -> ISpyFirePerson:
    """Convert an Entra user to an iSpyFire person.

    Args:
        user: Entra user

    Returns:
        ISpyFirePerson ready for creation
    """
    return ISpyFirePerson(
        id="",  # Will be assigned by iSpyFire
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        email=user.email,
        cell_phone=user.mobile_phone,
        title=user.extension_attribute1,
        is_active=True,
        is_login_active=True,  # Allow login immediately
        message_email=True,  # Default to email notifications
        message_cell=True,  # Default to SMS notifications
    )
