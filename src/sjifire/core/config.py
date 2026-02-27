"""Configuration loading utilities."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def get_graph_credentials() -> tuple[str, str, str]:
    """Get MS Graph API credentials from environment.

    Returns:
        Tuple of (tenant_id, client_id, client_secret)

    Raises:
        ValueError: If any required credential is not set
    """
    tenant_id = os.getenv("MS_GRAPH_TENANT_ID")
    client_id = os.getenv("MS_GRAPH_CLIENT_ID")
    client_secret = os.getenv("MS_GRAPH_CLIENT_SECRET")

    if not tenant_id or not client_id or not client_secret:
        raise ValueError(
            "MS Graph credentials not set. Required: "
            "MS_GRAPH_TENANT_ID, MS_GRAPH_CLIENT_ID, MS_GRAPH_CLIENT_SECRET"
        )

    return tenant_id, client_id, client_secret


def get_service_account_credentials() -> tuple[str, str]:
    """Get service account credentials for delegated auth.

    The service account email is configured in organization.json (service_email field).
    Required for M365 group calendar operations because application
    permissions don't support group calendar writes.

    Returns:
        Tuple of (email, password)

    Raises:
        ValueError: If credentials are not set
    """
    email = os.getenv("SERVICE_EMAIL")
    password = os.getenv("SERVICE_PASSWORD")

    if not email or not password:
        raise ValueError(
            "Service account credentials not set. Required: SERVICE_EMAIL, SERVICE_PASSWORD"
        )

    return email, password


@dataclass
class ExchangeCredentials:
    """Credentials for Exchange Online PowerShell authentication."""

    tenant_id: str
    client_id: str
    organization: str
    certificate_thumbprint: str | None = None
    certificate_path: str | None = None
    certificate_password: str | None = None


def get_exchange_credentials() -> ExchangeCredentials:
    """Get Exchange Online credentials from environment.

    Uses certificate-based authentication for app-only access.
    Either certificate_thumbprint (Windows) or certificate_path + password
    (cross-platform) must be provided.

    Environment variables:
        EXCHANGE_TENANT_ID: Tenant ID (falls back to MS_GRAPH_TENANT_ID)
        EXCHANGE_CLIENT_ID: App client ID (falls back to MS_GRAPH_CLIENT_ID)
        EXCHANGE_ORGANIZATION: Organization domain (falls back to org config)
        EXCHANGE_CERTIFICATE_THUMBPRINT: Certificate thumbprint (Windows)
        EXCHANGE_CERTIFICATE_PATH: Path to .pfx certificate file
        EXCHANGE_CERTIFICATE_PASSWORD: Password for .pfx file

    Returns:
        ExchangeCredentials with certificate configuration

    Raises:
        ValueError: If neither certificate method is configured
    """
    # Get tenant/client, with fallback to Graph credentials
    tenant_id = os.getenv("EXCHANGE_TENANT_ID") or os.getenv("MS_GRAPH_TENANT_ID")
    client_id = os.getenv("EXCHANGE_CLIENT_ID") or os.getenv("MS_GRAPH_CLIENT_ID")

    if not tenant_id or not client_id:
        raise ValueError(
            "Exchange credentials not set. Required: "
            "EXCHANGE_TENANT_ID/MS_GRAPH_TENANT_ID and EXCHANGE_CLIENT_ID/MS_GRAPH_CLIENT_ID"
        )

    # Default to org domain from config if not set in environment
    organization = os.getenv("EXCHANGE_ORGANIZATION")
    if not organization:
        organization = load_org_config().domain
    thumbprint = os.getenv("EXCHANGE_CERTIFICATE_THUMBPRINT")
    cert_path = os.getenv("EXCHANGE_CERTIFICATE_PATH")
    cert_password = os.getenv("EXCHANGE_CERTIFICATE_PASSWORD")

    if not thumbprint and not cert_path:
        raise ValueError(
            "Exchange credentials not set. Required: "
            "EXCHANGE_CERTIFICATE_THUMBPRINT (Windows) or "
            "EXCHANGE_CERTIFICATE_PATH + EXCHANGE_CERTIFICATE_PASSWORD (cross-platform)"
        )

    if cert_path and cert_password is None:
        raise ValueError(
            "EXCHANGE_CERTIFICATE_PASSWORD is required when using EXCHANGE_CERTIFICATE_PATH "
            "(can be empty string for Key Vault generated certs)"
        )

    return ExchangeCredentials(
        tenant_id=tenant_id,
        client_id=client_id,
        organization=organization,
        certificate_thumbprint=thumbprint,
        certificate_path=cert_path,
        certificate_password=cert_password,
    )


@dataclass
class DispatchConfig:
    """Configuration for email dispatch processing."""

    dispatch_email: str
    allowed_senders: list[str]
    archive_folder: str
    retention_days: int
    mailbox_user_id: str


@dataclass
class OrgConfig:
    """Organization configuration loaded from config/organization.json.

    All org-specific data lives here rather than in code, so
    customization requires only editing the JSON file.
    """

    company_name: str
    domain: str
    service_email: str
    timezone: str = ""
    cosmos_database: str = ""
    rank_hierarchy: tuple[str, ...] = ()
    officer_positions: tuple[str, ...] = ()
    operational_positions: frozenset[str] = field(default_factory=frozenset)
    marine_positions: frozenset[str] = field(default_factory=frozenset)
    chief_unit_prefixes: frozenset[str] = field(default_factory=frozenset)
    neris_entity_id: str = ""
    dispatch_agency_code: str = ""
    default_city: str = ""
    default_state: str = ""
    editor_group_name: str = ""
    position_order: tuple[str, ...] = ()
    schedule_excluded_sections: frozenset[str] = field(default_factory=frozenset)
    schedule_section_order: tuple[str, ...] = ()
    schedule_section_labels: dict[str, str] = field(default_factory=dict)
    duty_event_subject: str = ""
    calendar_category: str = ""
    skip_emails: list[str] = field(default_factory=list)


# Alias for backwards compatibility
EntraSyncConfig = OrgConfig


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


def load_org_config() -> OrgConfig:
    """Load organization configuration from config file.

    Returns:
        OrgConfig with company_name, domain, and service_email
    """
    project_root = get_project_root()
    config_path = project_root / "config" / "organization.json"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open() as f:
        config_data = json.load(f)

    return OrgConfig(
        company_name=config_data["company_name"],
        domain=config_data["domain"],
        service_email=config_data["service_email"],
        timezone=config_data.get("timezone", ""),
        cosmos_database=config_data.get("cosmos_database", ""),
        rank_hierarchy=tuple(config_data.get("rank_hierarchy", ())),
        officer_positions=tuple(config_data.get("officer_positions", ())),
        operational_positions=frozenset(config_data.get("operational_positions", ())),
        marine_positions=frozenset(config_data.get("marine_positions", ())),
        chief_unit_prefixes=frozenset(config_data.get("chief_unit_prefixes", ())),
        neris_entity_id=config_data.get("neris_entity_id", ""),
        dispatch_agency_code=config_data.get("dispatch_agency_code", ""),
        default_city=config_data.get("default_city", ""),
        default_state=config_data.get("default_state", ""),
        editor_group_name=config_data.get("editor_group_name", ""),
        position_order=tuple(config_data.get("position_order", ())),
        schedule_excluded_sections=frozenset(
            s.lower() for s in config_data.get("schedule_excluded_sections", ())
        ),
        schedule_section_order=tuple(config_data.get("schedule_section_order", ())),
        schedule_section_labels=config_data.get("schedule_section_labels", {}),
        duty_event_subject=config_data.get("duty_event_subject", ""),
        calendar_category=config_data.get("calendar_category", ""),
        skip_emails=config_data.get("skip_emails", []),
    )


# Alias for backwards compatibility
load_entra_sync_config = load_org_config


# Cached config instance
_org_config: OrgConfig | None = None


def get_org_config() -> OrgConfig:
    """Get cached organization config.

    Loads config once and caches it for subsequent calls.
    """
    global _org_config
    if _org_config is None:
        _org_config = load_org_config()
    return _org_config


def get_domain() -> str:
    """Get organization domain from config."""
    return get_org_config().domain


def get_service_email() -> str:
    """Get service account email from config."""
    return get_org_config().service_email


def get_cosmos_database() -> str:
    """Get Cosmos DB database name.

    Reads from ``COSMOS_DATABASE`` env var first (for Container Apps),
    falls back to ``organization.json``.
    """
    return os.getenv("COSMOS_DATABASE") or get_org_config().cosmos_database


# ---------------------------------------------------------------------------
# Shared Cosmos DB client (connection pool)
# ---------------------------------------------------------------------------

_cosmos_client = None
_cosmos_db = None
_cosmos_lock: asyncio.Lock | None = None
_cosmos_in_memory: bool | None = None


def _get_cosmos_lock() -> asyncio.Lock:
    """Get or create the Cosmos init lock (must be called in an event loop)."""
    global _cosmos_lock
    if _cosmos_lock is None:
        _cosmos_lock = asyncio.Lock()
    return _cosmos_lock


async def get_cosmos_container(container_name: str):
    """Get a Cosmos DB container client from the shared connection pool.

    All stores should use this instead of creating their own CosmosClient.
    Returns None when Cosmos DB is not configured (in-memory fallback).
    """
    global _cosmos_client, _cosmos_db, _cosmos_in_memory

    if _cosmos_in_memory is True:
        return None
    if _cosmos_db is not None:
        return _cosmos_db.get_container_client(container_name)

    async with _get_cosmos_lock():
        # Re-check after acquiring lock
        if _cosmos_in_memory is True:
            return None
        if _cosmos_db is not None:
            return _cosmos_db.get_container_client(container_name)

        endpoint = os.getenv("COSMOS_ENDPOINT")
        key = os.getenv("COSMOS_KEY")

        if key:
            from azure.cosmos.aio import CosmosClient

            _cosmos_client = CosmosClient(endpoint, credential=key)
        elif endpoint:
            from azure.cosmos.aio import CosmosClient
            from azure.identity.aio import DefaultAzureCredential

            _cosmos_client = CosmosClient(endpoint, credential=DefaultAzureCredential())
        else:
            logger.info("No COSMOS_ENDPOINT — stores will use in-memory mode")
            _cosmos_in_memory = True
            return None

        db_name = get_cosmos_database()
        _cosmos_db = _cosmos_client.get_database_client(db_name)
        logger.info("Cosmos DB connection pool ready: %s", db_name)
        return _cosmos_db.get_container_client(container_name)


def get_timezone() -> ZoneInfo:
    """Get organization timezone as a ZoneInfo object."""
    return ZoneInfo(get_org_config().timezone)


def get_timezone_name() -> str:
    """Get organization timezone name string (e.g. 'America/Los_Angeles')."""
    return get_org_config().timezone


def local_now() -> datetime:
    """Get the current datetime in the organization's configured timezone.

    Use this instead of ``datetime.now()`` or ``date.today()`` in server
    code — the container runs in UTC, so bare calls return the wrong
    date/time after 4 PM Pacific.
    """
    return datetime.now(get_timezone())


def to_utc_iso(val: str) -> str:
    """Convert a timestamp string to UTC ISO format.

    iSpyFire dispatch timestamps are naive strings in the org's local
    timezone (e.g. ``"2026-02-07T13:46:01"`` for 1:46 PM Pacific).
    NERIS and other external systems interpret bare timestamps as UTC,
    so we must convert before sending.

    - Naive timestamps → treated as local timezone → converted to UTC
    - Already-aware timestamps → converted to UTC
    - Empty or unparseable strings → returned as-is
    """
    if not val:
        return val
    try:
        dt = datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return val
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_timezone())
    return dt.astimezone(UTC).isoformat()
