"""Multi-tenant configuration and credential management.

Tenants represent individual fire departments. Each tenant has:
- A UUID ``tenant_id`` for use in data stores and auth tokens.
- A human-readable ``slug`` for config file paths, secret naming, and logs.

Tenant config files live at ``config/tenants/{slug}.json``.
Tenant credentials are loaded from environment variables with an optional
slug prefix (e.g. ``SJIFIRE_ALADTEC_URL``), falling back to the unprefixed
variable (e.g. ``ALADTEC_URL``) for single-tenant backwards compatibility.
"""

import json
import logging
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import uuid_utils

from sjifire.core.config import OrgConfig, get_project_root

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tenant configuration
# ---------------------------------------------------------------------------

DEFAULT_TENANT_SLUG = "sjifire"


def generate_tenant_id() -> str:
    """Generate a UUIDv7 tenant identifier (RFC 9562).

    UUIDv7 is time-ordered (48-bit ms timestamp) with 74 random bits,
    giving sequential index inserts in B-tree databases (Postgres,
    Firestore) while remaining non-guessable.
    """
    return str(uuid_utils.uuid7())


@dataclass
class TenantConfig(OrgConfig):
    """Configuration for a single tenant (fire department).

    Extends :class:`OrgConfig` with tenant identity fields.  All existing
    code that expects ``OrgConfig`` works unchanged since ``TenantConfig``
    is a subclass.
    """

    tenant_id: str = ""
    slug: str = ""


def _get_tenants_dir() -> Path:
    """Get the tenants config directory."""
    return get_project_root() / "config" / "tenants"


def load_tenant_config(slug: str) -> TenantConfig:
    """Load tenant configuration from ``config/tenants/{slug}.json``."""
    config_path = _get_tenants_dir() / f"{slug}.json"

    if not config_path.exists():
        raise FileNotFoundError(f"Tenant config not found: {config_path}")

    with config_path.open() as f:
        data = json.load(f)

    return TenantConfig(
        tenant_id=data["tenant_id"],
        slug=data["slug"],
        company_name=data["company_name"],
        domain=data["domain"],
        service_email=data["service_email"],
        timezone=data.get("timezone", ""),
        cosmos_database=data.get("cosmos_database", ""),
        rank_hierarchy=tuple(data.get("rank_hierarchy", ())),
        officer_positions=tuple(data.get("officer_positions", ())),
        operational_positions=frozenset(data.get("operational_positions", ())),
        marine_positions=frozenset(data.get("marine_positions", ())),
        chief_unit_prefixes=frozenset(data.get("chief_unit_prefixes", ())),
        neris_entity_id=data.get("neris_entity_id", ""),
        default_city=data.get("default_city", ""),
        default_state=data.get("default_state", ""),
        editor_group_name=data.get("editor_group_name", ""),
        position_order=tuple(data.get("position_order", ())),
        schedule_excluded_sections=frozenset(
            s.lower() for s in data.get("schedule_excluded_sections", ())
        ),
        schedule_section_order=tuple(data.get("schedule_section_order", ())),
        schedule_section_labels=data.get("schedule_section_labels", {}),
        duty_event_subject=data.get("duty_event_subject", ""),
        calendar_category=data.get("calendar_category", ""),
        skip_emails=data.get("skip_emails", []),
    )


def list_tenants() -> list[str]:
    """List available tenant slugs from ``config/tenants/``."""
    tenants_dir = _get_tenants_dir()
    if not tenants_dir.exists():
        return []
    return sorted(p.stem for p in tenants_dir.glob("*.json"))


def get_default_tenant_slug() -> str:
    """Get the default tenant slug from ``DEFAULT_TENANT_SLUG`` env var."""
    return os.getenv("DEFAULT_TENANT_SLUG", DEFAULT_TENANT_SLUG)


# Per-tenant config cache
_tenant_configs: dict[str, TenantConfig] = {}


def get_tenant_config(slug: str | None = None) -> TenantConfig:
    """Get cached tenant config.  Defaults to the default tenant."""
    if slug is None:
        slug = get_default_tenant_slug()
    if slug not in _tenant_configs:
        _tenant_configs[slug] = load_tenant_config(slug)
    return _tenant_configs[slug]


# ---------------------------------------------------------------------------
# Tenant credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AladtecCredentials:
    """Credentials for Aladtec workforce scheduling."""

    url: str
    username: str
    password: str


@dataclass(frozen=True)
class GraphCredentials:
    """Credentials for Microsoft Graph API (per-tenant M365)."""

    tenant_id: str
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class ISpyFireCredentials:
    """Credentials for iSpyFire dispatch/paging API."""

    url: str
    username: str
    password: str


@dataclass(frozen=True)
class NerisCredentials:
    """Credentials for NERIS federal reporting API."""

    client_id: str
    client_secret: str


class TenantCredentials:
    """Load credentials for a specific tenant.

    Current implementation: env vars with optional tenant-slug prefix.
    Future: GCP Secret Manager at ``tenants/{slug}/{secret-name}``.

    Lookup order for each key::

        1. {SLUG}_KEY  (e.g. SJIFIRE_ALADTEC_URL)
        2. KEY          (e.g. ALADTEC_URL) — single-tenant fallback
    """

    def __init__(self, slug: str) -> None:
        """Initialize credentials loader for the given tenant slug."""
        self.slug = slug
        self._prefix = slug.upper().replace("-", "_")

    def get(self, key: str) -> str | None:
        """Get a credential value with tenant-prefix fallback."""
        return os.getenv(f"{self._prefix}_{key}") or os.getenv(key) or None

    @cached_property
    def aladtec(self) -> AladtecCredentials | None:
        """Aladtec credentials, or ``None`` if not configured."""
        url = self.get("ALADTEC_URL")
        username = self.get("ALADTEC_USERNAME")
        password = self.get("ALADTEC_PASSWORD")
        if not url or not username or not password:
            return None
        return AladtecCredentials(url=url, username=username, password=password)

    @cached_property
    def graph(self) -> GraphCredentials | None:
        """Microsoft Graph API credentials, or ``None`` if not configured."""
        tid = self.get("MS_GRAPH_TENANT_ID")
        cid = self.get("MS_GRAPH_CLIENT_ID")
        secret = self.get("MS_GRAPH_CLIENT_SECRET")
        if not tid or not cid or not secret:
            return None
        return GraphCredentials(tenant_id=tid, client_id=cid, client_secret=secret)

    @cached_property
    def ispyfire(self) -> ISpyFireCredentials | None:
        """ISpyFire credentials, or ``None`` if not configured."""
        url = self.get("ISPYFIRE_URL")
        username = self.get("ISPYFIRE_USERNAME")
        password = self.get("ISPYFIRE_PASSWORD")
        if not url or not username or not password:
            return None
        return ISpyFireCredentials(url=url, username=username, password=password)

    @cached_property
    def neris(self) -> NerisCredentials | None:
        """NERIS API credentials, or ``None`` if not configured."""
        cid = self.get("NERIS_CLIENT_ID")
        secret = self.get("NERIS_CLIENT_SECRET")
        if not cid or not secret:
            return None
        return NerisCredentials(client_id=cid, client_secret=secret)

    @cached_property
    def available_connectors(self) -> list[str]:
        """List connector names whose credentials are configured."""
        connectors: list[str] = []
        if self.aladtec is not None:
            connectors.append("aladtec")
        if self.graph is not None:
            connectors.append("graph")
        if self.ispyfire is not None:
            connectors.append("ispyfire")
        if self.neris is not None:
            connectors.append("neris")
        return connectors


# Cached credentials per tenant
_tenant_credentials: dict[str, TenantCredentials] = {}


def get_tenant_credentials(slug: str | None = None) -> TenantCredentials:
    """Get cached tenant credentials.  Defaults to the default tenant."""
    if slug is None:
        slug = get_default_tenant_slug()
    if slug not in _tenant_credentials:
        _tenant_credentials[slug] = TenantCredentials(slug)
    return _tenant_credentials[slug]
