"""Shared pytest fixtures."""

import os
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Credential isolation: strip ALL real service credentials before any test
# runs so tests never accidentally hit external services, even if .env is
# loaded by load_dotenv() during imports.
# ---------------------------------------------------------------------------
_CREDENTIAL_VARS = [
    # Aladtec
    "ALADTEC_URL",
    "ALADTEC_USERNAME",
    "ALADTEC_PASSWORD",
    # Microsoft Graph
    "MS_GRAPH_TENANT_ID",
    "MS_GRAPH_CLIENT_ID",
    "MS_GRAPH_CLIENT_SECRET",
    # iSpyFire
    "ISPYFIRE_URL",
    "ISPYFIRE_USERNAME",
    "ISPYFIRE_PASSWORD",
    # Cosmos DB
    "COSMOS_ENDPOINT",
    "COSMOS_KEY",
    # Entra MCP / OAuth
    "ENTRA_MCP_API_CLIENT_ID",
    "ENTRA_MCP_API_CLIENT_SECRET",
    "ENTRA_REPORT_EDITORS_GROUP_ID",
    # NERIS
    "NERIS_ENTITY_ID",
    "NERIS_CLIENT_ID",
    "NERIS_CLIENT_SECRET",
    # Anthropic
    "ANTHROPIC_API_KEY",
    # Exchange
    "EXCHANGE_CERTIFICATE_THUMBPRINT",
    "EXCHANGE_CERTIFICATE_PATH",
    "EXCHANGE_CERTIFICATE_PASSWORD",
    # ACR
    "ACR_LOGIN_SERVER",
    "ACR_USERNAME",
    "ACR_PASSWORD",
]


@pytest.fixture(autouse=True, scope="session")
def _strip_credentials():
    """Remove real credentials from env so no test hits external services."""
    saved = {}
    for var in _CREDENTIAL_VARS:
        if var in os.environ:
            saved[var] = os.environ.pop(var)
    yield
    # Restore after the entire test session
    os.environ.update(saved)


@pytest.fixture
def sample_csv_content():
    """Sample Aladtec CSV export content."""
    return """Member List
Member Filter: Active
First Name,Last Name,Email,Mobile Phone,Home Phone,Employee Type,Member Status,Work Group,Pay Profile,Employee ID,Station Assignment,EVIP,Date Hired
John,Doe,john.doe@sjifire.org,555-1234,555-5678,"Firefighter, EMT",Active,A Shift,Volunteer,EMP001,Station 1,Yes,2020-01-15
Jane,Smith,jane.smith@sjifire.org,555-2345,,"Apparatus Operator",Active,B Shift,Career,EMP002,Station 2,,2019-06-01
Bob,Johnson,,555-3456,555-6789,Chief,Active,Admin,Career,EMP003,Station 1,Yes,2015-03-20
"""


@pytest.fixture
def sample_inactive_csv_content():
    """Sample Aladtec inactive members CSV export."""
    return """Member List
Member Filter: Inactive
Member,Email,Work Group,Member Status,Pay Profile
"Doe, John",john.doe@sjifire.org,A Shift,Inactive,Volunteer
"Smith, Former",,B Shift,Inactive,Career
"""


@pytest.fixture
def temp_backup_dir(tmp_path):
    """Temporary directory for backup tests."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    return backup_dir


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set mock environment variables for testing."""
    monkeypatch.setenv("ALADTEC_URL", "https://test.aladtec.com")
    monkeypatch.setenv("ALADTEC_USERNAME", "testuser")
    monkeypatch.setenv("ALADTEC_PASSWORD", "testpass")
    monkeypatch.setenv("MS_GRAPH_TENANT_ID", "test-tenant-id")
    monkeypatch.setenv("MS_GRAPH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("MS_GRAPH_CLIENT_SECRET", "test-client-secret")


@pytest.fixture
def mock_graph_client():
    """Mock MS Graph client for testing."""
    client = MagicMock()
    return client
