"""Microsoft Graph API client wrapper."""

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient

from sjifire.core.config import get_graph_credentials


def get_graph_client() -> GraphServiceClient:
    """Create and return an authenticated MS Graph client.

    Uses client credentials flow (app-only authentication) with
    credentials from environment variables.

    Returns:
        Authenticated GraphServiceClient instance
    """
    tenant_id, client_id, client_secret = get_graph_credentials()

    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )

    scopes = ["https://graph.microsoft.com/.default"]
    return GraphServiceClient(credentials=credential, scopes=scopes)
