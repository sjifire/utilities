"""Tests for MCP incident tools with access control."""

import os
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.mcp.auth import UserContext, set_current_user
from sjifire.mcp.incidents.models import CrewAssignment, IncidentDocument
from sjifire.mcp.incidents.tools import (
    _check_edit_access,
    _check_view_access,
    create_incident,
    get_incident,
    get_neris_incident,
    list_incidents,
    list_neris_incidents,
    submit_incident,
    update_incident,
)


# Fixtures
@pytest.fixture(autouse=True)
def _officer_group_env():
    """Set the officer group ID for all tests."""
    with patch.dict(os.environ, {"ENTRA_MCP_OFFICER_GROUP_ID": "officer-group"}):
        yield


@pytest.fixture
def regular_user():
    user = UserContext(
        email="ff@sjifire.org", name="Firefighter", user_id="user-1", groups=frozenset()
    )
    set_current_user(user)
    return user


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


@pytest.fixture
def sample_doc():
    return IncidentDocument(
        id="doc-123",
        station="S31",
        incident_number="26-000944",
        incident_date=date(2026, 2, 12),
        created_by="ff@sjifire.org",
        crew=[
            CrewAssignment(name="Crew 1", email="crew1@sjifire.org", position="FF", unit="E31"),
        ],
    )


# Access control tests
class TestViewAccess:
    def test_creator_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "ff@sjifire.org", is_officer=False)

    def test_crew_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "crew1@sjifire.org", is_officer=False)

    def test_officer_can_view(self, sample_doc):
        assert _check_view_access(sample_doc, "random@sjifire.org", is_officer=True)

    def test_stranger_cannot_view(self, sample_doc):
        assert not _check_view_access(sample_doc, "stranger@sjifire.org", is_officer=False)


class TestEditAccess:
    def test_creator_can_edit(self, sample_doc):
        assert _check_edit_access(sample_doc, "ff@sjifire.org", is_officer=False)

    def test_officer_can_edit(self, sample_doc):
        assert _check_edit_access(sample_doc, "random@sjifire.org", is_officer=True)

    def test_crew_cannot_edit(self, sample_doc):
        assert not _check_edit_access(sample_doc, "crew1@sjifire.org", is_officer=False)

    def test_stranger_cannot_edit(self, sample_doc):
        assert not _check_edit_access(sample_doc, "stranger@sjifire.org", is_officer=False)


# Tool tests with mocked store
class TestCreateIncident:
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_creates_draft(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.create = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await create_incident(
            incident_number="26-000944",
            incident_date="2026-02-12",
            station="S31",
            crew=[{"name": "John", "email": "john@sjifire.org", "position": "FF", "unit": "E31"}],
        )

        assert result["station"] == "S31"
        assert result["year"] == "2026"
        assert result["incident_number"] == "26-000944"
        assert result["status"] == "draft"
        assert result["created_by"] == "ff@sjifire.org"
        assert len(result["crew"]) == 1


class TestGetIncident:
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_creator_gets_own(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_incident("doc-123")
        assert result["incident_number"] == "26-000944"

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_stranger_denied(self, mock_store_cls, sample_doc):
        stranger = UserContext(email="stranger@sjifire.org", name="X", user_id="x")
        set_current_user(stranger)

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_incident("doc-123")
        assert "error" in result
        assert "access" in result["error"].lower()

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_not_found(self, mock_store_cls, regular_user):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=None)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_incident("nonexistent")
        assert "error" in result


class TestListIncidents:
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_regular_user_sees_own(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.list_for_user = AsyncMock(return_value=[sample_doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await list_incidents()
        assert result["count"] == 1
        mock_store.list_for_user.assert_called_once_with(
            "ff@sjifire.org", status=None, exclude_status="submitted"
        )

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_officer_sees_all(self, mock_store_cls, officer_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.list_by_status = AsyncMock(return_value=[sample_doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await list_incidents()
        assert result["count"] == 1
        mock_store.list_by_status.assert_called_once_with(
            None, station=None, exclude_status="submitted"
        )

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_explicit_status_no_exclusion(self, mock_store_cls, officer_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.list_by_status = AsyncMock(return_value=[sample_doc])
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await list_incidents(status="submitted")
        assert result["count"] == 1
        mock_store.list_by_status.assert_called_once_with(
            "submitted", station=None, exclude_status=None
        )


class TestUpdateIncident:
    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_creator_can_update(self, mock_store_cls, regular_user, sample_doc):
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store.update = AsyncMock(side_effect=lambda doc: doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident("doc-123", address="200 Spring St")
        assert result["address"] == "200 Spring St"

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_crew_cannot_update(self, mock_store_cls, sample_doc):
        crew_user = UserContext(email="crew1@sjifire.org", name="Crew", user_id="c1")
        set_current_user(crew_user)

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident("doc-123", address="Hacked")
        assert "error" in result
        assert "permission" in result["error"].lower()

    @patch("sjifire.mcp.incidents.tools.IncidentStore")
    async def test_cannot_update_submitted(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "submitted"
        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=sample_doc)
        mock_store_cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await update_incident("doc-123", address="Too late")
        assert "error" in result
        assert "submitted" in result["error"].lower()


class TestSubmitIncident:
    async def test_regular_user_cannot_submit(self, regular_user):
        result = await submit_incident("doc-123")
        assert "error" in result
        assert "officer" in result["error"].lower()

    async def test_officer_gets_not_available(self, officer_user):
        result = await submit_incident("doc-123")
        assert result["status"] == "not_available"
        assert "not yet enabled" in result["message"]
        assert result["incident_id"] == "doc-123"


class TestListNerisIncidents:
    @patch("sjifire.mcp.incidents.tools._list_neris_incidents")
    async def test_officer_can_list(self, mock_list, officer_user):
        mock_list.return_value = {
            "incidents": [
                {
                    "neris_id": "FD53055879|26SJ0001|123",
                    "incident_number": "26SJ0001",
                    "call_create": "2026-01-15T10:30:00",
                    "status": "APPROVED",
                    "incident_type": "111",
                }
            ],
            "count": 1,
        }

        result = await list_neris_incidents()
        assert result["count"] == 1
        assert result["incidents"][0]["incident_number"] == "26SJ0001"
        mock_list.assert_called_once()

    async def test_regular_user_denied(self, regular_user):
        result = await list_neris_incidents()
        assert "error" in result
        assert "officer" in result["error"].lower()

    @patch("sjifire.mcp.incidents.tools._list_neris_incidents")
    async def test_handles_api_error(self, mock_list, officer_user):
        mock_list.side_effect = RuntimeError("Connection failed")

        result = await list_neris_incidents()
        assert "error" in result
        assert "Connection failed" in result["error"]


class TestGetNerisIncident:
    @patch("sjifire.mcp.incidents.tools._get_neris_incident")
    async def test_officer_can_get(self, mock_get, officer_user):
        mock_get.return_value = {
            "neris_id": "FD53055879|26SJ0001|123",
            "dispatch": {"incident_number": "26SJ0001"},
            "incident_types": [{"type": "111"}],
        }

        result = await get_neris_incident("FD53055879|26SJ0001|123")
        assert result["neris_id"] == "FD53055879|26SJ0001|123"
        mock_get.assert_called_once_with("FD53055879|26SJ0001|123")

    async def test_regular_user_denied(self, regular_user):
        result = await get_neris_incident("FD53055879|26SJ0001|123")
        assert "error" in result
        assert "officer" in result["error"].lower()

    @patch("sjifire.mcp.incidents.tools._get_neris_incident")
    async def test_not_found(self, mock_get, officer_user):
        mock_get.return_value = None

        result = await get_neris_incident("FD53055879|BOGUS|999")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.mcp.incidents.tools._get_neris_incident")
    async def test_handles_api_error(self, mock_get, officer_user):
        mock_get.side_effect = RuntimeError("Connection failed")

        result = await get_neris_incident("FD53055879|26SJ0001|123")
        assert "error" in result
        assert "Connection failed" in result["error"]
