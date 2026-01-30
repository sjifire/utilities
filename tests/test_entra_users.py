"""Tests for sjifire.entra.users."""

import string
from unittest.mock import AsyncMock, MagicMock

import pytest

from sjifire.entra.users import EntraUser, EntraUserManager


class TestEntraUser:
    """Tests for the EntraUser dataclass."""

    def test_is_active_when_enabled(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="john@sjifire.org",
            upn="john.doe@sjifire.org",
            employee_id="EMP001",
            account_enabled=True,
        )
        assert user.is_active is True

    def test_is_active_when_disabled(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email="john@sjifire.org",
            upn="john.doe@sjifire.org",
            employee_id="EMP001",
            account_enabled=False,
        )
        assert user.is_active is False

    def test_default_account_enabled(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
        )
        assert user.account_enabled is True


class TestEntraUserManagerPasswordGeneration:
    """Tests for password generation."""

    def test_password_length(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert len(password) == 16

    def test_password_has_uppercase(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert any(c in string.ascii_uppercase for c in password)

    def test_password_has_lowercase(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert any(c in string.ascii_lowercase for c in password)

    def test_password_has_digit(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert any(c in string.digits for c in password)

    def test_password_has_special_char(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert any(c in "!@#$%" for c in password)

    def test_passwords_are_unique(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        passwords = {manager._generate_temp_password() for _ in range(100)}

        # All 100 passwords should be unique
        assert len(passwords) == 100


class TestEntraUserManagerUPNGeneration:
    """Tests for UPN generation."""

    def test_basic_upn(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "sjifire.org"

        upn = manager.generate_upn("John", "Doe")

        assert upn == "john.doe@sjifire.org"

    def test_upn_lowercase(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "sjifire.org"

        upn = manager.generate_upn("JOHN", "DOE")

        assert upn == "john.doe@sjifire.org"

    def test_upn_removes_spaces(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "sjifire.org"

        upn = manager.generate_upn("Mary Jane", "Watson Smith")

        assert upn == "maryjane.watsonsmith@sjifire.org"

    def test_upn_removes_apostrophes(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "sjifire.org"

        upn = manager.generate_upn("Patrick", "O'Brien")

        assert upn == "patrick.obrien@sjifire.org"

    def test_upn_custom_domain(self, mock_env_vars):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "example.com"

        upn = manager.generate_upn("John", "Doe")

        assert upn == "john.doe@example.com"


class TestEntraUserManagerLicenseManagement:
    """Tests for license management methods."""

    @pytest.fixture
    def manager(self, mock_env_vars):
        """Create a manager with mocked client."""
        mgr = EntraUserManager.__new__(EntraUserManager)
        mgr.domain = "sjifire.org"
        mgr.client = MagicMock()
        return mgr

    async def test_get_user_licenses_returns_sku_ids(self, manager):
        """Should return list of SKU ID strings."""
        # Mock license details response - must use valid UUIDs
        mock_license1 = MagicMock()
        mock_license1.sku_id = "f30db892-07e9-47e9-837c-80727f46fd3d"
        mock_license2 = MagicMock()
        mock_license2.sku_id = "84a661c4-e949-4bd2-a560-ed7766fcaf2b"

        mock_result = MagicMock()
        mock_result.value = [mock_license1, mock_license2]

        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=mock_result
        )

        licenses = await manager.get_user_licenses("user-123")

        assert licenses == [
            "f30db892-07e9-47e9-837c-80727f46fd3d",
            "84a661c4-e949-4bd2-a560-ed7766fcaf2b",
        ]
        manager.client.users.by_user_id.assert_called_with("user-123")

    async def test_get_user_licenses_returns_empty_list_when_no_licenses(self, manager):
        """Should return empty list when user has no licenses."""
        mock_result = MagicMock()
        mock_result.value = []

        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=mock_result
        )

        licenses = await manager.get_user_licenses("user-123")

        assert licenses == []

    async def test_get_user_licenses_handles_none_result(self, manager):
        """Should return empty list when API returns None."""
        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=None
        )

        licenses = await manager.get_user_licenses("user-123")

        assert licenses == []

    async def test_get_user_licenses_handles_error(self, manager):
        """Should return empty list on API error."""
        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            side_effect=Exception("API error")
        )

        licenses = await manager.get_user_licenses("user-123")

        assert licenses == []

    async def test_remove_all_licenses_success(self, manager):
        """Should remove all licenses and return True."""
        # Mock get_user_licenses - must use valid UUID
        mock_license = MagicMock()
        mock_license.sku_id = "f30db892-07e9-47e9-837c-80727f46fd3d"
        mock_result = MagicMock()
        mock_result.value = [mock_license]

        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=mock_result
        )
        manager.client.users.by_user_id.return_value.assign_license.post = AsyncMock(
            return_value=None
        )

        result = await manager.remove_all_licenses("user-123")

        assert result is True
        manager.client.users.by_user_id.return_value.assign_license.post.assert_called_once()

    async def test_remove_all_licenses_returns_true_when_no_licenses(self, manager):
        """Should return True when user has no licenses to remove."""
        mock_result = MagicMock()
        mock_result.value = []

        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=mock_result
        )

        result = await manager.remove_all_licenses("user-123")

        assert result is True

    async def test_remove_all_licenses_returns_false_on_error(self, manager):
        """Should return False when license removal fails."""
        mock_license = MagicMock()
        mock_license.sku_id = "f30db892-07e9-47e9-837c-80727f46fd3d"
        mock_result = MagicMock()
        mock_result.value = [mock_license]

        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=mock_result
        )
        manager.client.users.by_user_id.return_value.assign_license.post = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.remove_all_licenses("user-123")

        assert result is False

    async def test_disable_and_remove_licenses_both_succeed(self, manager):
        """Should return (True, True) when both operations succeed."""
        # Mock disable_user
        manager.client.users.by_user_id.return_value.patch = AsyncMock(return_value=None)

        # Mock license removal (no licenses)
        mock_result = MagicMock()
        mock_result.value = []
        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=mock_result
        )

        disable_ok, license_ok = await manager.disable_and_remove_licenses("user-123")

        assert disable_ok is True
        assert license_ok is True

    async def test_disable_and_remove_licenses_disable_fails(self, manager):
        """Should return (False, ...) when disable fails."""
        # Mock disable_user to fail
        manager.client.users.by_user_id.return_value.patch = AsyncMock(
            side_effect=Exception("API error")
        )

        # Mock license removal (no licenses)
        mock_result = MagicMock()
        mock_result.value = []
        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=mock_result
        )

        disable_ok, license_ok = await manager.disable_and_remove_licenses("user-123")

        assert disable_ok is False
        # License removal still attempted
        assert license_ok is True

    async def test_disable_and_remove_licenses_license_removal_fails(self, manager):
        """Should return (True, False) when license removal fails."""
        # Mock disable_user to succeed
        manager.client.users.by_user_id.return_value.patch = AsyncMock(return_value=None)

        # Mock license details to return a license - must use valid UUID
        mock_license = MagicMock()
        mock_license.sku_id = "f30db892-07e9-47e9-837c-80727f46fd3d"
        mock_result = MagicMock()
        mock_result.value = [mock_license]
        manager.client.users.by_user_id.return_value.license_details.get = AsyncMock(
            return_value=mock_result
        )

        # Mock assign_license to fail
        manager.client.users.by_user_id.return_value.assign_license.post = AsyncMock(
            side_effect=Exception("API error")
        )

        disable_ok, license_ok = await manager.disable_and_remove_licenses("user-123")

        assert disable_ok is True
        assert license_ok is False
