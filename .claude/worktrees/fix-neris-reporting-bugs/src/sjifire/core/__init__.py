"""Core utilities for SJI Fire integrations."""

from sjifire.core.config import (
    get_domain,
    get_graph_credentials,
    get_org_config,
    get_service_email,
)

__all__ = ["get_domain", "get_graph_credentials", "get_org_config", "get_service_email"]
