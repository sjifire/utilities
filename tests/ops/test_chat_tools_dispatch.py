"""Tests for chat tool dispatch, specifically the import_from_neris path."""

import os
from unittest.mock import AsyncMock, patch

import pytest

import sjifire.ops.auth
from sjifire.ops.auth import UserContext, set_current_user


@pytest.fixture(autouse=True)
def _editor_group_env():
    """Set the editor group ID for all tests."""
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

        fake_result = {
            "id": "doc-1",
            "neris_incident_id": "FD123|456|789",
            "units": [],
        }

        with patch(
            "sjifire.ops.incidents.tools.import_from_neris",
            new_callable=AsyncMock,
            return_value=fake_result,
        ) as mock_import:
            result = await _dispatch(
                "import_from_neris",
                {"incident_id": "doc-1", "neris_id": "FD123|456|789"},
            )

        mock_import.assert_awaited_once_with(
            "FD123|456|789", incident_id="doc-1", incident_number=None
        )
        # Dispatch transforms the result into a summary
        assert result["status"] == "success"
        assert result["neris_incident_id"] == "FD123|456|789"
        assert "next_step" in result

    async def test_dispatch_resolves_neris_id_from_incident(self, officer_user):
        """When neris_id is not provided, resolve it from the incident doc."""
        from sjifire.ops.chat.tools import _dispatch

        fake_incident = {"id": "doc-1", "neris_incident_id": "FD123|456|789"}
        fake_import_result = {
            "id": "doc-1",
            "neris_incident_id": "FD123|456|789",
            "import_comparison": {"discrepancies": ["addr mismatch"]},
            "units": [{"unit_id": "E31", "personnel": []}],
        }

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

        mock_import.assert_awaited_once_with(
            "FD123|456|789", incident_id="doc-1", incident_number=None
        )
        # Dispatch transforms the result into a summary
        assert result["status"] == "success"
        assert result["neris_incident_id"] == "FD123|456|789"
        assert result["units"] == ["E31"]
        assert result["discrepancies"] == ["addr mismatch"]

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
