"""Core utilities for SJI Fire integrations."""

from sjifire.core.config import (
    get_domain,
    get_graph_credentials,
    get_org_config,
    get_service_email,
)
from sjifire.core.tenant import (
    TenantConfig,
    TenantCredentials,
    get_tenant_config,
    get_tenant_credentials,
    list_tenants,
)

__all__ = [
    "TenantConfig",
    "TenantCredentials",
    "get_domain",
    "get_graph_credentials",
    "get_org_config",
    "get_service_email",
    "get_tenant_config",
    "get_tenant_credentials",
    "list_tenants",
]
