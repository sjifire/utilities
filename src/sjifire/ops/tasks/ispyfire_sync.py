"""iSpyFire user sync task.

Compares operational users in Entra ID with iSpyFire people and
applies changes: add new users, update changed fields, reactivate
matched-but-inactive users, and deactivate removed users.

Requires: MS_GRAPH_*, ISPYFIRE_* env vars.
"""

import asyncio
import logging

from sjifire.ops.tasks.registry import register

logger = logging.getLogger(__name__)


@register("ispyfire-sync")
async def ispyfire_sync() -> int:
    """Sync Entra ID users to iSpyFire.

    Returns:
        Total number of changes applied (adds + updates + reactivations + deactivations)
    """
    from sjifire.entra.users import EntraUserManager
    from sjifire.ispyfire.client import ISpyFireClient
    from sjifire.ispyfire.sync import (
        compare_entra_to_ispyfire,
        entra_user_to_ispyfire_person,
        get_responder_types,
    )

    # Fetch from both systems
    user_manager = EntraUserManager()
    entra_users = await user_manager.get_employees()
    logger.info("Fetched %d employees from Entra ID", len(entra_users))

    ispyfire_people = await asyncio.to_thread(_fetch_ispyfire_people)
    logger.info("Fetched %d people from iSpyFire", len(ispyfire_people))

    # Compare
    comparison = compare_entra_to_ispyfire(entra_users, ispyfire_people)
    changes = 0

    if not comparison.to_add and not comparison.to_update and not comparison.to_remove:
        # Check for reactivations among matched/updated
        to_reactivate = [
            person
            for _user, person in comparison.matched + comparison.to_update
            if not person.is_active
        ]
        if not to_reactivate:
            logger.info("iSpyFire sync: no changes needed")
            return 0

    # Apply changes
    def _apply():
        nonlocal changes
        with ISpyFireClient() as client:
            # Add new people
            for user in comparison.to_add:
                existing = client.get_person_by_email(user.email) if user.email else None
                if existing:
                    if not existing.is_active and client.reactivate_person(
                        existing.id, email=existing.email
                    ):
                        logger.info("Reactivated: %s", existing.display_name)
                        changes += 1
                    continue

                person = entra_user_to_ispyfire_person(user)
                result = client.create_and_invite(person)
                if result:
                    logger.info("Created: %s (ID: %s)", person.display_name, result.id)
                    changes += 1
                else:
                    logger.error("Failed to create: %s", person.display_name)

            # Update existing people
            for user, person in comparison.to_update:
                if user.first_name:
                    person.first_name = user.first_name
                if user.last_name:
                    person.last_name = user.last_name
                if user.mobile_phone:
                    person.cell_phone = user.mobile_phone
                if user.extension_attribute1:
                    person.title = user.extension_attribute1
                person.responder_types = get_responder_types(user)

                if client.update_person(person):
                    logger.info("Updated: %s", person.display_name)
                    changes += 1
                else:
                    logger.error("Failed to update: %s", person.display_name)

            # Reactivate matched-but-inactive users
            to_reactivate = [
                person
                for _user, person in comparison.matched + comparison.to_update
                if not person.is_active
            ]
            for person in to_reactivate:
                if client.reactivate_person(person.id, email=person.email):
                    logger.info("Reactivated: %s", person.display_name)
                    changes += 1
                else:
                    logger.error("Failed to reactivate: %s", person.display_name)

            # Deactivate removed people
            for person in comparison.to_remove:
                if client.deactivate_person(person.id, email=person.email):
                    logger.info("Deactivated: %s", person.display_name)
                    changes += 1
                else:
                    logger.error("Failed to deactivate: %s", person.display_name)

    await asyncio.to_thread(_apply)

    logger.info(
        "iSpyFire sync complete: %d adds, %d updates, %d removals, %d total changes",
        len(comparison.to_add),
        len(comparison.to_update),
        len(comparison.to_remove),
        changes,
    )
    return changes


def _fetch_ispyfire_people():
    """Fetch all iSpyFire people including inactive (blocking)."""
    from sjifire.ispyfire.client import ISpyFireClient

    with ISpyFireClient() as client:
        return client.get_people(include_inactive=True, include_deleted=True)
