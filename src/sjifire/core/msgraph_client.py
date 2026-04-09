"""Microsoft Graph API client wrapper.

All GraphServiceClient instances should be created through this module
to ensure consistent retry middleware (429/503/504 with exponential backoff).
"""

from azure.core.credentials import TokenCredential
from azure.identity import ClientSecretCredential
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)
from kiota_http.middleware.options.retry_handler_option import RetryHandlerOption
from msgraph import GraphServiceClient
from msgraph.graph_request_adapter import GraphRequestAdapter
from msgraph_core import GraphClientFactory

from sjifire.core.config import get_graph_credentials

# Retry config for Graph API rate limiting (429) and transient errors (503/504).
# Kiota's RetryHandler uses exponential backoff with jitter and respects Retry-After headers.
GRAPH_MAX_RETRIES = 5
GRAPH_RETRY_DELAY = 3.0  # initial delay in seconds


def _create_retry_options() -> dict:
    """Build middleware options dict with retry configuration."""
    retry = RetryHandlerOption(
        max_retries=GRAPH_MAX_RETRIES,
        delay=GRAPH_RETRY_DELAY,
        should_retry=True,
    )
    return {RetryHandlerOption.get_key(): retry}


def create_graph_client(credential: TokenCredential) -> GraphServiceClient:
    """Create a GraphServiceClient with retry middleware for any credential.

    Use this when you need a Graph client with a custom credential
    (e.g., delegated auth via ROPC). The returned client automatically
    retries on 429/503/504 with exponential backoff.

    Args:
        credential: Any Azure TokenCredential (ClientSecret, ROPC, etc.)

    Returns:
        GraphServiceClient with retry middleware enabled
    """
    http_client = GraphClientFactory.create_with_default_middleware(
        options=_create_retry_options(),
    )
    auth_provider = AzureIdentityAuthenticationProvider(credential)
    adapter = GraphRequestAdapter(auth_provider, client=http_client)
    return GraphServiceClient(request_adapter=adapter)


def get_graph_client() -> GraphServiceClient:
    """Create and return an authenticated MS Graph client.

    Uses client credentials flow (app-only authentication) with
    credentials from environment variables. Includes retry middleware
    for 429/503/504 errors.

    Returns:
        Authenticated GraphServiceClient instance
    """
    tenant_id, client_id, client_secret = get_graph_credentials()

    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )

    return create_graph_client(credential)
