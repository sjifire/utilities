"""Sync logic for Entra ID to iSpyFire."""

import logging
from dataclasses import dataclass, field

from sjifire.core.config import get_domain, get_org_config
from sjifire.core.normalize import normalize_email, normalize_name, normalize_phone
from sjifire.entra.users import EntraUser
from sjifire.ispyfire.models import ISpyFirePerson

logger = logging.getLogger(__name__)

# Schedules that qualify a user for iSpyFire access, independent of operational positions.
# This allows administrative staff on operational schedules to receive incident notifications.
ISPYFIRE_QUALIFYING_SCHEDULES: set[str] = {"Operations"}

# Mapping from Entra positions to iSpyFire responder types
POSITION_TO_RESPONDER_TYPE: dict[str, str] = {
    "Firefighter": "FF",
    "Wildland Firefighter": "WFF",
    "Support": "Support",
}


def get_responder_types(user: EntraUser) -> list[str]:
    """Compute iSpyFire responder types from Entra positions.

    Mapping:
    - Firefighter → FF
    - Wildland Firefighter → WFF
    - Support → Support
    - Apparatus Operator (without FF or WFF) → Tender Ops
    - Any Marine position (Mate, Pilot, Deckhand) → Marine

    Args:
        user: Entra user object

    Returns:
        List of responder type strings (e.g., ["FF", "WFF"])
    """
    positions = get_user_positions(user)
    responder_types: list[str] = []

    # Map direct positions
    for position, responder_type in POSITION_TO_RESPONDER_TYPE.items():
        if position in positions:
            responder_types.append(responder_type)

    # Tender Ops: Apparatus Operator without FF or WFF
    if (
        "Apparatus Operator" in positions
        and "Firefighter" not in positions
        and "Wildland Firefighter" not in positions
    ):
        responder_types.append("Tender Ops")

    # Marine: any marine position
    if positions & get_org_config().marine_positions:
        responder_types.append("Marine")

    return sorted(responder_types)


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
    """Check if user qualifies for iSpyFire access.

    A user qualifies if they have:
    - At least one operational position (Firefighter, Apparatus Operator, etc.), OR
    - At least one qualifying schedule (Operations)

    Args:
        user: Entra user object

    Returns:
        True if user qualifies for iSpyFire access
    """
    positions = get_user_positions(user)
    if positions & get_org_config().operational_positions:
        return True

    # Also check qualifying schedules for non-operational staff
    schedules = user.schedules
    return bool(schedules & ISPYFIRE_QUALIFYING_SCHEDULES)


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

    # Responder types (computed from positions)
    expected_types = get_responder_types(user)
    current_types = sorted(person.responder_types) if person.responder_types else []
    if expected_types != current_types:
        differences.append("responderTypes")

    return differences


def is_managed_email(email: str | None, domain: str | None = None) -> bool:
    """Check if email belongs to the managed domain.

    Args:
        email: Email address to check
        domain: Domain to match (default: from organization config)

    Returns:
        True if email ends with the managed domain
    """
    if not email:
        return False
    if domain is None:
        domain = get_domain()
    return email.lower().strip().endswith(f"@{domain}")


def compare_entra_to_ispyfire(
    entra_users: list[EntraUser],
    ispyfire_people: list[ISpyFirePerson],
    managed_domain: str | None = None,
) -> SyncComparison:
    """Compare Entra users with iSpyFire people to determine sync actions.

    Only syncs users with emails in the managed domain (from organization config).
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

    # Use org domain if not specified
    domain = managed_domain or get_domain()

    # Filter to managed domain users first
    managed_users = [u for u in entra_users if is_managed_email(u.email, domain)]

    # Track users skipped due to no operational position
    for user in managed_users:
        if not is_operational(user):
            comparison.skipped_no_operational.append(user)

    # Filter to operational Entra users with managed domain email only
    operational_users = [u for u in managed_users if is_operational(u)]
    comparison.entra_operational = operational_users

    logger.info(f"Found {len(operational_users)} operational @{domain} users in Entra")
    if comparison.skipped_no_operational:
        logger.info(
            f"Skipped {len(comparison.skipped_no_operational)} users without operational positions"
        )
    logger.info(f"Found {len(ispyfire_people)} people in iSpyFire")

    # Build lookup by email for iSpyFire people (only managed domain)
    ispyfire_by_email: dict[str, ISpyFirePerson] = {}
    for person in ispyfire_people:
        if person.email and is_managed_email(person.email, domain):
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
            and is_managed_email(person.email, domain)
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
        responder_types=get_responder_types(user),
        message_email=True,  # Default to email notifications
        message_cell=True,  # Default to SMS notifications
    )
