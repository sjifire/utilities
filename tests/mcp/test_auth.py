"""Tests for MCP Entra ID auth module."""

import os
from unittest.mock import patch

import pytest

from sjifire.mcp.auth import UserContext, get_current_user, set_current_user


class TestUserContext:
    def test_basic_properties(self):
        user = UserContext(email="chief@sjifire.org", name="Fire Chief", user_id="abc-123")
        assert user.email == "chief@sjifire.org"
        assert user.name == "Fire Chief"
        assert user.user_id == "abc-123"
        assert user.groups == frozenset()

    def test_is_officer_without_config(self):
        """Without ENTRA_MCP_OFFICER_GROUP_ID set, no one is an officer."""
        user = UserContext(
            email="chief@sjifire.org",
            name="Fire Chief",
            user_id="abc-123",
            groups=frozenset(["group-1", "group-2"]),
        )
        with patch.dict(os.environ, {}, clear=True):
            assert not user.is_officer

    def test_is_officer_with_matching_group(self):
        user = UserContext(
            email="chief@sjifire.org",
            name="Fire Chief",
            user_id="abc-123",
            groups=frozenset(["officer-group-id", "other-group"]),
        )
        with patch.dict(os.environ, {"ENTRA_MCP_OFFICER_GROUP_ID": "officer-group-id"}):
            assert user.is_officer

    def test_is_officer_without_matching_group(self):
        user = UserContext(
            email="ff@sjifire.org",
            name="Firefighter",
            user_id="abc-456",
            groups=frozenset(["some-other-group"]),
        )
        with patch.dict(os.environ, {"ENTRA_MCP_OFFICER_GROUP_ID": "officer-group-id"}):
            assert not user.is_officer

    def test_frozen(self):
        user = UserContext(email="a@b.com", name="A", user_id="1")
        with pytest.raises(AttributeError):
            user.email = "new@b.com"


class TestCurrentUserContext:
    def test_get_without_set_raises(self):
        """With no user set, always raises RuntimeError."""
        set_current_user(None)
        with pytest.raises(RuntimeError, match="No authenticated user"):
            get_current_user()

    def test_set_and_get(self):
        user = UserContext(email="test@sjifire.org", name="Test", user_id="xyz")
        set_current_user(user)
        assert get_current_user() is user
        # Clean up
        set_current_user(None)
