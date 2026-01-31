"""Configuration loading utilities."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def get_aladtec_credentials() -> tuple[str, str, str]:
    """Get Aladtec credentials from environment.

    Returns:
        Tuple of (url, username, password)

    Raises:
        ValueError: If any required credential is not set
    """
    load_dotenv()

    url = os.getenv("ALADTEC_URL")
    username = os.getenv("ALADTEC_USERNAME")
    password = os.getenv("ALADTEC_PASSWORD")

    if not url or not username or not password:
        raise ValueError(
            "Aladtec credentials not set. Required: ALADTEC_URL, ALADTEC_USERNAME, ALADTEC_PASSWORD"
        )

    return url, username, password


def get_graph_credentials() -> tuple[str, str, str]:
    """Get MS Graph API credentials from environment.

    Returns:
        Tuple of (tenant_id, client_id, client_secret)

    Raises:
        ValueError: If any required credential is not set
    """
    load_dotenv()

    tenant_id = os.getenv("MS_GRAPH_TENANT_ID")
    client_id = os.getenv("MS_GRAPH_CLIENT_ID")
    client_secret = os.getenv("MS_GRAPH_CLIENT_SECRET")

    if not tenant_id or not client_id or not client_secret:
        raise ValueError(
            "MS Graph credentials not set. Required: "
            "MS_GRAPH_TENANT_ID, MS_GRAPH_CLIENT_ID, MS_GRAPH_CLIENT_SECRET"
        )

    return tenant_id, client_id, client_secret


@dataclass
class DispatchConfig:
    """Configuration for email dispatch processing."""

    dispatch_email: str
    allowed_senders: list[str]
    archive_folder: str
    retention_days: int
    mailbox_user_id: str


@dataclass
class EntraSyncConfig:
    """Configuration for Aladtec to Entra ID sync."""

    company_name: str
    domain: str
    skip_emails: list[str] = field(default_factory=list)


def get_project_root() -> Path:
    """Get the project root directory."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not find project root (no pyproject.toml found)")


def load_dispatch_config(require_mailbox: bool = True) -> DispatchConfig:
    """Load dispatch configuration from config file and environment.

    Args:
        require_mailbox: If True, raise error if DISPATCH_MAILBOX_USER_ID not set
    """
    load_dotenv()

    project_root = get_project_root()
    config_path = project_root / "config" / "email_dispatch.json"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open() as f:
        config_data = json.load(f)

    mailbox_user_id = os.getenv("DISPATCH_MAILBOX_USER_ID")
    if require_mailbox and not mailbox_user_id:
        raise ValueError("DISPATCH_MAILBOX_USER_ID environment variable not set")

    return DispatchConfig(
        dispatch_email=config_data["dispatch_email"],
        allowed_senders=config_data["allowed_senders"],
        archive_folder=config_data["archive_folder"],
        retention_days=config_data["retention_days"],
        mailbox_user_id=mailbox_user_id or "",
    )


def get_ispyfire_credentials() -> tuple[str, str, str]:
    """Get iSpyFire credentials from environment.

    Returns:
        Tuple of (base_url, username, password)

    Raises:
        ValueError: If any required credential is not set
    """
    load_dotenv()

    url = os.getenv("ISPYFIRE_URL")
    username = os.getenv("ISPYFIRE_USERNAME")
    password = os.getenv("ISPYFIRE_PASSWORD")

    if not url or not username or not password:
        raise ValueError(
            "iSpyFire credentials not set. Required: ISPYFIRE_URL, ISPYFIRE_USERNAME, ISPYFIRE_PASSWORD"
        )

    return url, username, password


def load_entra_sync_config() -> EntraSyncConfig:
    """Load Entra sync configuration from config file.

    Returns:
        EntraSyncConfig with company_name and domain
    """
    project_root = get_project_root()
    config_path = project_root / "config" / "entra_sync.json"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open() as f:
        config_data = json.load(f)

    return EntraSyncConfig(
        company_name=config_data["company_name"],
        domain=config_data.get("domain", "sjifire.org"),
        skip_emails=config_data.get("skip_emails", []),
    )
