"""Tests for NERIS value set tools."""

import os
from unittest.mock import patch

import pytest

from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.neris.tools import (
    _VALUE_SETS,
    _humanize,
    get_neris_values,
    list_neris_value_sets,
)


@pytest.fixture(autouse=True)
def _dev_mode():
    """Ensure dev mode (no Entra config) so get_current_user() works."""
    with patch.dict(os.environ, {"ENTRA_MCP_API_CLIENT_ID": ""}, clear=False):
        set_current_user(None)
        yield


@pytest.fixture
def auth_user():
    user = UserContext(email="ff@sjifire.org", name="Firefighter", user_id="user-1")
    set_current_user(user)
    return user


class TestHumanize:
    def test_pipe_hierarchy(self):
        assert _humanize("FIRE||STRUCTURE_FIRE||CHIMNEY_FIRE") == (
            "Fire > Structure Fire > Chimney Fire"
        )

    def test_single_level(self):
        assert _humanize("EMERGENT") == "Emergent"

    def test_underscores_replaced(self):
        assert _humanize("MOTOR_VEHICLE_COLLISION") == "Motor Vehicle Collision"


class TestValueSetDiscovery:
    def test_discovered_enums(self):
        assert len(_VALUE_SETS) == 88

    def test_key_sets_present(self):
        for name in ("incident", "action_tactic", "location_use", "response_mode"):
            assert name in _VALUE_SETS, f"Missing value set: {name}"


class TestListNerisValueSets:
    async def test_returns_all_sets(self, auth_user):
        result = await list_neris_value_sets()

        assert result["total"] == 88
        assert len(result["value_sets"]) == 88

    async def test_set_entry_shape(self, auth_user):
        result = await list_neris_value_sets()

        entry = result["value_sets"][0]
        assert "name" in entry
        assert "count" in entry
        assert isinstance(entry["count"], int)

    async def test_sorted_by_name(self, auth_user):
        result = await list_neris_value_sets()

        names = [s["name"] for s in result["value_sets"]]
        assert names == sorted(names)

    async def test_requires_auth(self):
        set_current_user(None)
        with (
            patch.dict(os.environ, {"ENTRA_MCP_API_CLIENT_ID": "real-client-id"}),
            pytest.raises(RuntimeError, match="No authenticated user"),
        ):
            await list_neris_value_sets()


class TestGetNerisValues:
    async def test_returns_all_values(self, auth_user):
        result = await get_neris_values("incident")

        assert result["value_set"] == "incident"
        assert result["count"] == 128
        assert len(result["values"]) == 128

    async def test_value_entry_shape(self, auth_user):
        result = await get_neris_values("response_mode")

        entry = result["values"][0]
        assert "value" in entry
        assert "label" in entry

    async def test_prefix_filter(self, auth_user):
        result = await get_neris_values("incident", prefix="FIRE||STRUCTURE_FIRE||")

        assert result["count"] > 0
        for v in result["values"]:
            assert v["value"].startswith("FIRE||STRUCTURE_FIRE||")

    async def test_prefix_no_match(self, auth_user):
        result = await get_neris_values("incident", prefix="NONEXISTENT||")

        assert result["count"] == 0
        assert result["values"] == []

    async def test_search_filter(self, auth_user):
        result = await get_neris_values("incident", search="boat")

        assert result["count"] >= 1
        for v in result["values"]:
            assert "boat" in v["value"].lower()

    async def test_search_case_insensitive(self, auth_user):
        lower = await get_neris_values("incident", search="chimney")
        upper = await get_neris_values("incident", search="CHIMNEY")

        assert lower["count"] == upper["count"]
        assert lower["count"] >= 1

    async def test_prefix_and_search_combined(self, auth_user):
        result = await get_neris_values("incident", prefix="FIRE||", search="chimney")

        assert result["count"] >= 1
        for v in result["values"]:
            assert v["value"].startswith("FIRE||")
            assert "chimney" in v["value"].lower()

    async def test_unknown_value_set(self, auth_user):
        result = await get_neris_values("nonexistent")

        assert "error" in result
        assert "nonexistent" in result["error"]
        assert "available" in result
        assert isinstance(result["available"], list)

    async def test_value_set_name_case_insensitive(self, auth_user):
        result = await get_neris_values("INCIDENT")

        assert "error" not in result
        assert result["count"] == 128

    async def test_requires_auth(self):
        set_current_user(None)
        with (
            patch.dict(os.environ, {"ENTRA_MCP_API_CLIENT_ID": "real-client-id"}),
            pytest.raises(RuntimeError, match="No authenticated user"),
        ):
            await get_neris_values("incident")

    async def test_small_value_set(self, auth_user):
        """Verify a simple set returns expected values."""
        result = await get_neris_values("response_mode")

        values = {v["value"] for v in result["values"]}
        assert "EMERGENT" in values
        assert "NON_EMERGENT" in values
        assert result["count"] == 2

    async def test_noaction_values(self, auth_user):
        result = await get_neris_values("noaction")

        values = {v["value"] for v in result["values"]}
        assert "CANCELLED" in values
        assert result["count"] == 3
