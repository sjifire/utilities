"""Backup utilities for Aladtec and Entra data."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sjifire.aladtec.models import Member
from sjifire.entra.users import EntraUser

if TYPE_CHECKING:
    from sjifire.entra.groups import EntraGroup

logger = logging.getLogger(__name__)

# Default backup directory relative to project root
DEFAULT_BACKUP_DIR = Path(__file__).parent.parent.parent.parent / "backups"


def get_backup_dir(base_dir: Path | str | None = None) -> Path:
    """Get or create the backup directory.

    Args:
        base_dir: Base directory for backups. If None, uses default.

    Returns:
        Path to backup directory
    """
    backup_path = DEFAULT_BACKUP_DIR if base_dir is None else Path(base_dir)
    backup_path.mkdir(parents=True, exist_ok=True)
    return backup_path


def backup_aladtec_members(
    members: list[Member],
    backup_dir: Path | str | None = None,
    prefix: str = "aladtec",
) -> Path:
    """Backup Aladtec members to a JSON file.

    Args:
        members: List of Member objects to backup
        backup_dir: Directory to save backup. If None, uses default.
        prefix: Prefix for the backup filename

    Returns:
        Path to the created backup file
    """
    backup_dir = get_backup_dir(backup_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_members_{timestamp}.json"
    filepath = backup_dir / filename

    # Convert members to serializable format
    data = {
        "backup_type": "aladtec_members",
        "timestamp": datetime.now().isoformat(),
        "count": len(members),
        "members": [_member_to_dict(m) for m in members],
    }

    with filepath.open("w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Backed up {len(members)} Aladtec members to {filepath}")
    return filepath


def backup_entra_users(
    users: list[EntraUser],
    backup_dir: Path | str | None = None,
    prefix: str = "entra",
) -> Path:
    """Backup Entra users to a JSON file.

    Args:
        users: List of EntraUser objects to backup
        backup_dir: Directory to save backup. If None, uses default.
        prefix: Prefix for the backup filename

    Returns:
        Path to the created backup file
    """
    backup_dir = get_backup_dir(backup_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_users_{timestamp}.json"
    filepath = backup_dir / filename

    # Convert users to serializable format
    data = {
        "backup_type": "entra_users",
        "timestamp": datetime.now().isoformat(),
        "count": len(users),
        "users": [asdict(u) for u in users],
    }

    with filepath.open("w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Backed up {len(users)} Entra users to {filepath}")
    return filepath


def _member_to_dict(member: Member) -> dict:
    """Convert a Member to a dictionary for JSON serialization.

    Args:
        member: Member object

    Returns:
        Dict representation
    """
    return {
        "id": member.id,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "display_name": member.display_name,
        "email": member.email,
        "phone": member.phone,
        "home_phone": member.home_phone,
        "employee_type": member.employee_type,
        "positions": member.positions,
        "title": member.title,
        "status": member.status,
        "is_active": member.is_active,
        "work_group": member.work_group,
        "pay_profile": member.pay_profile,
        "employee_id": member.employee_id,
        "station_assignment": member.station_assignment,
        "evip": member.evip,
        "date_hired": member.date_hired,
    }


def backup_entra_groups(
    groups: list[EntraGroup],
    memberships: dict[str, list[str]] | None = None,
    backup_dir: Path | str | None = None,
    prefix: str = "entra",
) -> Path:
    """Backup Entra groups and their memberships to a JSON file.

    Args:
        groups: List of EntraGroup objects to backup
        memberships: Optional dict mapping group_id to list of member user_ids
        backup_dir: Directory to save backup. If None, uses default.
        prefix: Prefix for the backup filename

    Returns:
        Path to the created backup file
    """
    backup_dir = get_backup_dir(backup_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_groups_{timestamp}.json"
    filepath = backup_dir / filename

    # Convert groups to serializable format
    groups_data = []
    for g in groups:
        group_dict = {
            "id": g.id,
            "display_name": g.display_name,
            "description": g.description,
            "mail": g.mail,
            "mail_enabled": g.mail_enabled,
            "security_enabled": g.security_enabled,
            "group_types": g.group_types,
            "group_type": g.group_type.value,
        }
        if memberships and g.id in memberships:
            group_dict["members"] = memberships[g.id]
        groups_data.append(group_dict)

    data = {
        "backup_type": "entra_groups",
        "timestamp": datetime.now().isoformat(),
        "count": len(groups),
        "groups": groups_data,
    }

    with filepath.open("w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Backed up {len(groups)} Entra groups to {filepath}")
    return filepath


def backup_mail_groups(
    groups: list[dict],
    backup_dir: Path | str | None = None,
    prefix: str = "mail",
) -> Path:
    """Backup mail-enabled security groups and distribution lists to a JSON file.

    Args:
        groups: List of group dicts with keys: identity, display_name, email, group_type, members
        backup_dir: Directory to save backup. If None, uses default.
        prefix: Prefix for the backup filename

    Returns:
        Path to the created backup file
    """
    backup_dir = get_backup_dir(backup_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_groups_{timestamp}.json"
    filepath = backup_dir / filename

    data = {
        "backup_type": "mail_groups",
        "timestamp": datetime.now().isoformat(),
        "count": len(groups),
        "groups": groups,
    }

    with filepath.open("w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Backed up {len(groups)} mail groups to {filepath}")
    return filepath


def list_backups(backup_dir: Path | str | None = None) -> list[Path]:
    """List all backup files in the backup directory.

    Args:
        backup_dir: Directory to search. If None, uses default.

    Returns:
        List of backup file paths, sorted by modification time (newest first)
    """
    backup_dir = get_backup_dir(backup_dir)
    backups = list(backup_dir.glob("*.json"))
    return sorted(backups, key=lambda p: p.stat().st_mtime, reverse=True)
