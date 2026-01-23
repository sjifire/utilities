"""Configuration loading utilities."""

import os

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
