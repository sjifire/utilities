"""Backup utilities for Aladtec and Entra data."""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from sjifire.aladtec.models import Member
from sjifire.entra.users import EntraUser

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
