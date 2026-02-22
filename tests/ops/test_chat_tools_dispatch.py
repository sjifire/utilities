"""Tests for chat tool dispatch, specifically the import_from_neris path."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.auth import UserContext, set_current_user


@pytest.fixture(autouse=True)
def _editor_group_env():
    """Set the editor group ID for all tests."""
    import sjifire.ops.auth

    sjifire.ops.auth._EDITOR_GROUP_ID = None
    with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "officer-group"}):
        yield
    sjifire.ops.auth._EDITOR_GROUP_ID = None


@pytest.fixture
def officer_user():
    user = UserContext(
        email="chief@sjifire.org",
        name="Chief",
        user_id="user-2",
        groups=frozenset(["officer-group"]),
    )
    set_current_user(user)
    return user


class TestImportFromNerisDispatch:
    """Test the import_from_neris dispatch path in chat tools."""

    async def test_dispatch_with_neris_id(self, officer_user):
        """When both incident_id and neris_id are provided, pass them correctly."""
        from sjifire.ops.chat.tools import _dispatch

        fake_result = {"id": "doc-1", "neris_incident_id": "FD123|456|789"}

        with patch(
            "sjifire.ops.incidents.tools.import_from_neris",
            new_callable=AsyncMock,
            return_value=fake_result,
        ) as mock_import:
            result = await _dispatch(
                "import_from_neris",
                {"incident_id": "doc-1", "neris_id": "FD123|456|789"},
            )

        mock_import.assert_awaited_once_with("FD123|456|789", incident_id="doc-1")
        assert result == fake_result

    async def test_dispatch_resolves_neris_id_from_incident(self, officer_user):
        """When neris_id is not provided, resolve it from the incident doc."""
        from sjifire.ops.chat.tools import _dispatch

        fake_incident = {"id": "doc-1", "neris_incident_id": "FD123|456|789"}
        fake_import_result = {"id": "doc-1", "import_comparison": {}}

        with (
            patch(
                "sjifire.ops.incidents.tools.get_incident",
                new_callable=AsyncMock,
                return_value=fake_incident,
            ),
            patch(
                "sjifire.ops.incidents.tools.import_from_neris",
                new_callable=AsyncMock,
                return_value=fake_import_result,
            ) as mock_import,
        ):
            result = await _dispatch(
                "import_from_neris",
                {"incident_id": "doc-1"},
            )

        mock_import.assert_awaited_once_with("FD123|456|789", incident_id="doc-1")
        assert result == fake_import_result

    async def test_dispatch_no_neris_id_returns_error(self, officer_user):
        """When incident has no neris_incident_id, return an error."""
        from sjifire.ops.chat.tools import _dispatch

        fake_incident = {"id": "doc-1", "neris_incident_id": None}

        with patch(
            "sjifire.ops.incidents.tools.get_incident",
            new_callable=AsyncMock,
            return_value=fake_incident,
        ):
            result = await _dispatch(
                "import_from_neris",
                {"incident_id": "doc-1"},
            )

        assert "error" in result
        assert "NERIS ID is required" in result["error"]
