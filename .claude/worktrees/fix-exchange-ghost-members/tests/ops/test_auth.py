"""Tests for Entra ID auth module."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.auth import (
    UserContext,
    check_is_editor,
    get_current_user,
    set_current_user,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear module-level caches between tests."""
    import sjifire.ops.auth

    sjifire.ops.auth._EDITOR_GROUP_ID = None
    yield
    sjifire.ops.auth._EDITOR_GROUP_ID = None


class TestUserContext:
    def test_basic_properties(self):
        user = UserContext(email="chief@sjifire.org", name="Fire Chief", user_id="abc-123")
        assert user.email == "chief@sjifire.org"
        assert user.name == "Fire Chief"
        assert user.user_id == "abc-123"
        assert user.groups == frozenset()

    def test_is_editor_without_config(self):
        """Without ENTRA_REPORT_EDITORS_GROUP_ID set, no one is an editor."""
        user = UserContext(
            email="chief@sjifire.org",
            name="Fire Chief",
            user_id="abc-123",
            groups=frozenset(["group-1", "group-2"]),
        )
        with patch.dict(os.environ, {}, clear=True):
            assert not user.is_editor

    def test_is_editor_with_matching_group(self):
        user = UserContext(
            email="chief@sjifire.org",
            name="Fire Chief",
            user_id="abc-123",
            groups=frozenset(["editor-group-id", "other-group"]),
        )
        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "editor-group-id"}):
            assert user.is_editor

    def test_is_editor_without_matching_group(self):
        user = UserContext(
            email="ff@sjifire.org",
            name="Firefighter",
            user_id="abc-456",
            groups=frozenset(["some-other-group"]),
        )
        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "editor-group-id"}):
            assert not user.is_editor

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


class TestCheckIsEditor:
    """Tests for the live Graph API group membership check."""

    def setup_method(self):
        """Clear the editor cache between tests."""
        import sjifire.ops.auth

        sjifire.ops.auth._editor_cache.clear()

    async def test_returns_false_when_no_group_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await check_is_editor("user-1", fallback=True)
            assert result is False  # No group ID → False

    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_calls_graph_api(self, mock_check):
        mock_check.return_value = True

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            result = await check_is_editor("user-1")

        assert result is True
        mock_check.assert_called_once_with("user-1", "grp-1")

    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_caches_result_for_same_user(self, mock_check):
        """Result is cached — second call for same user skips Graph API."""
        mock_check.return_value = True

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            await check_is_editor("user-cache-1")
            await check_is_editor("user-cache-1")

        assert mock_check.call_count == 1

    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_falls_back_on_error(self, mock_check):
        mock_check.side_effect = RuntimeError("Graph API down")

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            result = await check_is_editor("user-1", fallback=True)

        assert result is True  # Uses fallback

    @patch("sjifire.ops.auth._check_member_groups", new_callable=AsyncMock)
    async def test_different_users_checked_independently(self, mock_check):
        mock_check.side_effect = [True, False]

        with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "grp-1"}):
            r1 = await check_is_editor("user-1")
            r2 = await check_is_editor("user-2")

        assert r1 is True
        assert r2 is False
        assert mock_check.call_count == 2
