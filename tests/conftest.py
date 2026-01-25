"""Shared pytest fixtures."""

from unittest.mock import MagicMock

import pytest


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
