"""Tests for sjifire.entra.users."""

import string
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_is_employee_with_employee_id(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id="EMP001",
        )
        assert user.is_employee is True

    def test_is_employee_without_employee_id(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
        )
        assert user.is_employee is False

    def test_has_phone_with_mobile(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            mobile_phone="555-1234",
        )
        assert user.has_phone is True

    def test_has_phone_without_mobile(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            mobile_phone=None,
        )
        assert user.has_phone is False

    def test_positions_empty(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute3=None,
        )
        assert user.positions == set()

    def test_positions_single(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute3="Firefighter",
        )
        assert user.positions == {"Firefighter"}

    def test_positions_multiple(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute3="Firefighter, Apparatus Operator, Support",
        )
        assert user.positions == {"Firefighter", "Apparatus Operator", "Support"}

    def test_positions_strips_whitespace(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute3="  Firefighter  ,  EMT  ",
        )
        assert user.positions == {"Firefighter", "EMT"}

    def test_rank_property(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute1="Captain",
        )
        assert user.rank == "Captain"

    def test_rank_property_none(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute1=None,
        )
        assert user.rank is None

    def test_evip_property(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute2="2025-12-31",
        )
        assert user.evip == "2025-12-31"

    def test_has_valid_evip_future_date(self):
        future_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute2=future_date,
        )
        assert user.has_valid_evip is True

    def test_has_valid_evip_today(self):
        today = date.today().strftime("%Y-%m-%d")
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute2=today,
        )
        assert user.has_valid_evip is True

    def test_has_valid_evip_expired(self):
        past_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute2=past_date,
        )
        assert user.has_valid_evip is False

    def test_has_valid_evip_none(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute2=None,
        )
        assert user.has_valid_evip is False

    def test_has_valid_evip_invalid_format(self):
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute2="not-a-date",
        )
        assert user.has_valid_evip is False

    def test_has_valid_evip_alternate_format(self):
        future_date = (date.today() + timedelta(days=30)).strftime("%m/%d/%Y")
        user = EntraUser(
            id="1",
            display_name="John Doe",
            first_name="John",
            last_name="Doe",
            email=None,
            upn=None,
            employee_id=None,
            extension_attribute2=future_date,
        )
        assert user.has_valid_evip is True


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


class TestEntraUserManagerInit:
    """Tests for EntraUserManager initialization."""

    def test_init_sets_domain(self, mock_env_vars):
        with patch("sjifire.entra.users.get_graph_client") as mock_client:
            mock_client.return_value = MagicMock()
            manager = EntraUserManager(domain="example.com")
            assert manager.domain == "example.com"

    def test_init_default_domain(self, mock_env_vars):
        with patch("sjifire.entra.users.get_graph_client") as mock_client:
            mock_client.return_value = MagicMock()
            manager = EntraUserManager()
            assert manager.domain == "sjifire.org"


class TestEntraUserManagerGetUsers:
    """Tests for get_users method."""

    @pytest.fixture
    def manager(self, mock_env_vars):
        """Create a manager with mocked client."""
        mgr = EntraUserManager.__new__(EntraUserManager)
        mgr.domain = "sjifire.org"
        mgr.client = MagicMock()
        return mgr

    def _mock_graph_user(
        self,
        user_id="123",
        display_name="John Doe",
        account_enabled=True,
        employee_id="EMP001",
    ):
        """Create a mock MS Graph User object."""
        user = MagicMock()
        user.id = user_id
        user.display_name = display_name
        user.given_name = "John"
        user.surname = "Doe"
        user.mail = "john@sjifire.org"
        user.user_principal_name = "john.doe@sjifire.org"
        user.employee_id = employee_id
        user.account_enabled = account_enabled
        user.job_title = None
        user.mobile_phone = "555-1234"
        user.business_phones = []
        user.office_location = None
        user.employee_hire_date = None
        user.employee_type = None
        user.other_mails = []
        user.department = None
        user.company_name = None
        user.on_premises_extension_attributes = None
        return user

    async def test_get_users_returns_users(self, manager):
        mock_user = self._mock_graph_user()
        mock_result = MagicMock()
        mock_result.value = [mock_user]
        mock_result.odata_next_link = None

        manager.client.users.get = AsyncMock(return_value=mock_result)

        users = await manager.get_users()

        assert len(users) == 1
        assert users[0].display_name == "John Doe"

    async def test_get_users_filters_disabled_by_default(self, manager):
        enabled_user = self._mock_graph_user(user_id="1", account_enabled=True)
        disabled_user = self._mock_graph_user(user_id="2", account_enabled=False)

        mock_result = MagicMock()
        mock_result.value = [enabled_user, disabled_user]
        mock_result.odata_next_link = None

        manager.client.users.get = AsyncMock(return_value=mock_result)

        users = await manager.get_users()

        assert len(users) == 1
        assert users[0].id == "1"

    async def test_get_users_includes_disabled_when_requested(self, manager):
        enabled_user = self._mock_graph_user(user_id="1", account_enabled=True)
        disabled_user = self._mock_graph_user(user_id="2", account_enabled=False)

        mock_result = MagicMock()
        mock_result.value = [enabled_user, disabled_user]
        mock_result.odata_next_link = None

        manager.client.users.get = AsyncMock(return_value=mock_result)

        users = await manager.get_users(include_disabled=True)

        assert len(users) == 2

    async def test_get_users_filters_non_employees(self, manager):
        employee = self._mock_graph_user(user_id="1", employee_id="EMP001")
        non_employee = self._mock_graph_user(user_id="2", employee_id=None)

        mock_result = MagicMock()
        mock_result.value = [employee, non_employee]
        mock_result.odata_next_link = None

        manager.client.users.get = AsyncMock(return_value=mock_result)

        users = await manager.get_users(employees_only=True)

        assert len(users) == 1
        assert users[0].id == "1"

    async def test_get_users_handles_pagination(self, manager):
        user1 = self._mock_graph_user(user_id="1")
        user2 = self._mock_graph_user(user_id="2")

        # First page
        mock_result1 = MagicMock()
        mock_result1.value = [user1]
        mock_result1.odata_next_link = "https://graph.microsoft.com/next"

        # Second page
        mock_result2 = MagicMock()
        mock_result2.value = [user2]
        mock_result2.odata_next_link = None

        manager.client.users.get = AsyncMock(return_value=mock_result1)
        manager.client.users.with_url.return_value.get = AsyncMock(return_value=mock_result2)

        users = await manager.get_users()

        assert len(users) == 2

    async def test_get_employees_calls_get_users(self, manager):
        mock_result = MagicMock()
        mock_result.value = []
        mock_result.odata_next_link = None

        manager.client.users.get = AsyncMock(return_value=mock_result)

        await manager.get_employees()

        manager.client.users.get.assert_called_once()


class TestEntraUserManagerGetUserByUpn:
    """Tests for get_user_by_upn method."""

    @pytest.fixture
    def manager(self, mock_env_vars):
        mgr = EntraUserManager.__new__(EntraUserManager)
        mgr.domain = "sjifire.org"
        mgr.client = MagicMock()
        return mgr

    async def test_get_user_by_upn_found(self, manager):
        mock_user = MagicMock()
        mock_user.id = "123"
        mock_user.display_name = "John Doe"
        mock_user.given_name = "John"
        mock_user.surname = "Doe"
        mock_user.mail = "john@sjifire.org"
        mock_user.user_principal_name = "john.doe@sjifire.org"
        mock_user.employee_id = "EMP001"
        mock_user.account_enabled = True
        mock_user.job_title = None
        mock_user.mobile_phone = None
        mock_user.business_phones = []
        mock_user.office_location = None
        mock_user.employee_hire_date = None
        mock_user.employee_type = None
        mock_user.other_mails = []
        mock_user.department = None
        mock_user.company_name = None
        mock_user.on_premises_extension_attributes = None

        manager.client.users.by_user_id.return_value.get = AsyncMock(return_value=mock_user)

        result = await manager.get_user_by_upn("john.doe@sjifire.org")

        assert result is not None
        assert result.display_name == "John Doe"

    async def test_get_user_by_upn_not_found(self, manager):
        manager.client.users.by_user_id.return_value.get = AsyncMock(
            side_effect=Exception("User not found")
        )

        result = await manager.get_user_by_upn("nobody@sjifire.org")

        assert result is None


class TestEntraUserManagerCreateUser:
    """Tests for create_user method."""

    @pytest.fixture
    def manager(self, mock_env_vars):
        mgr = EntraUserManager.__new__(EntraUserManager)
        mgr.domain = "sjifire.org"
        mgr.client = MagicMock()
        return mgr

    async def test_create_user_success(self, manager):
        mock_created = MagicMock()
        mock_created.id = "new-123"
        mock_created.display_name = "Jane Doe"
        mock_created.given_name = "Jane"
        mock_created.surname = "Doe"
        mock_created.mail = "jane@sjifire.org"
        mock_created.user_principal_name = "jane.doe@sjifire.org"
        mock_created.employee_id = "EMP002"
        mock_created.account_enabled = True
        mock_created.job_title = None
        mock_created.mobile_phone = None
        mock_created.business_phones = []
        mock_created.office_location = None
        mock_created.employee_hire_date = None
        mock_created.employee_type = None
        mock_created.other_mails = []
        mock_created.department = None
        mock_created.company_name = None
        mock_created.on_premises_extension_attributes = None

        manager.client.users.post = AsyncMock(return_value=mock_created)

        result = await manager.create_user(
            display_name="Jane Doe",
            first_name="Jane",
            last_name="Doe",
            upn="jane.doe@sjifire.org",
        )

        assert result is not None
        assert result.id == "new-123"

    async def test_create_user_failure(self, manager):
        manager.client.users.post = AsyncMock(side_effect=Exception("API error"))

        result = await manager.create_user(
            display_name="Jane Doe",
            first_name="Jane",
            last_name="Doe",
            upn="jane.doe@sjifire.org",
        )

        assert result is None

    async def test_create_user_with_extension_attributes(self, manager):
        mock_created = MagicMock()
        mock_created.id = "new-123"
        mock_created.display_name = "Jane Doe"
        mock_created.given_name = "Jane"
        mock_created.surname = "Doe"
        mock_created.mail = "jane@sjifire.org"
        mock_created.user_principal_name = "jane.doe@sjifire.org"
        mock_created.employee_id = None
        mock_created.account_enabled = True
        mock_created.job_title = None
        mock_created.mobile_phone = None
        mock_created.business_phones = []
        mock_created.office_location = None
        mock_created.employee_hire_date = None
        mock_created.employee_type = None
        mock_created.other_mails = []
        mock_created.department = None
        mock_created.company_name = None
        mock_created.on_premises_extension_attributes = MagicMock()
        mock_created.on_premises_extension_attributes.extension_attribute1 = "Captain"
        mock_created.on_premises_extension_attributes.extension_attribute2 = None
        mock_created.on_premises_extension_attributes.extension_attribute3 = None

        manager.client.users.post = AsyncMock(return_value=mock_created)

        result = await manager.create_user(
            display_name="Jane Doe",
            first_name="Jane",
            last_name="Doe",
            upn="jane.doe@sjifire.org",
            extension_attribute1="Captain",
        )

        assert result is not None
        manager.client.users.post.assert_called_once()


class TestEntraUserManagerUpdateUser:
    """Tests for update_user method."""

    @pytest.fixture
    def manager(self, mock_env_vars):
        mgr = EntraUserManager.__new__(EntraUserManager)
        mgr.domain = "sjifire.org"
        mgr.client = MagicMock()
        return mgr

    async def test_update_user_success(self, manager):
        manager.client.users.by_user_id.return_value.patch = AsyncMock(return_value=None)

        result = await manager.update_user(
            user_id="123",
            display_name="John Updated",
        )

        assert result is True
        manager.client.users.by_user_id.assert_called_with("123")

    async def test_update_user_failure(self, manager):
        manager.client.users.by_user_id.return_value.patch = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.update_user(
            user_id="123",
            display_name="John Updated",
        )

        assert result is False

    async def test_update_user_403_retry_without_phone(self, manager):
        # First call fails with 403
        # Second call (retry without phone) succeeds
        manager.client.users.by_user_id.return_value.patch = AsyncMock(
            side_effect=[
                Exception("Authorization_RequestDenied"),
                None,
            ]
        )

        result = await manager.update_user(
            user_id="123",
            display_name="John Updated",
            mobile_phone="555-1234",
        )

        assert result is True
        assert manager.client.users.by_user_id.return_value.patch.call_count == 2

    async def test_update_user_403_retry_also_fails(self, manager):
        manager.client.users.by_user_id.return_value.patch = AsyncMock(
            side_effect=Exception("Authorization_RequestDenied")
        )

        result = await manager.update_user(
            user_id="123",
            display_name="John Updated",
            mobile_phone="555-1234",
        )

        assert result is False


class TestEntraUserManagerEnableDisable:
    """Tests for enable_user and disable_user methods."""

    @pytest.fixture
    def manager(self, mock_env_vars):
        mgr = EntraUserManager.__new__(EntraUserManager)
        mgr.domain = "sjifire.org"
        mgr.client = MagicMock()
        return mgr

    async def test_disable_user_success(self, manager):
        manager.client.users.by_user_id.return_value.patch = AsyncMock(return_value=None)

        result = await manager.disable_user("123")

        assert result is True

    async def test_disable_user_failure(self, manager):
        manager.client.users.by_user_id.return_value.patch = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.disable_user("123")

        assert result is False

    async def test_enable_user_success(self, manager):
        manager.client.users.by_user_id.return_value.patch = AsyncMock(return_value=None)

        result = await manager.enable_user("123")

        assert result is True

    async def test_enable_user_failure(self, manager):
        manager.client.users.by_user_id.return_value.patch = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await manager.enable_user("123")

        assert result is False


class TestEntraUserManagerToEntraUser:
    """Tests for _to_entra_user conversion."""

    @pytest.fixture
    def manager(self, mock_env_vars):
        mgr = EntraUserManager.__new__(EntraUserManager)
        mgr.domain = "sjifire.org"
        mgr.client = MagicMock()
        return mgr

    def test_converts_basic_user(self, manager):
        mock_user = MagicMock()
        mock_user.id = "123"
        mock_user.display_name = "John Doe"
        mock_user.given_name = "John"
        mock_user.surname = "Doe"
        mock_user.mail = "john@sjifire.org"
        mock_user.user_principal_name = "john.doe@sjifire.org"
        mock_user.employee_id = "EMP001"
        mock_user.account_enabled = True
        mock_user.job_title = "Firefighter"
        mock_user.mobile_phone = "555-1234"
        mock_user.business_phones = ["555-5678"]
        mock_user.office_location = "Station 31"
        mock_user.employee_hire_date = None
        mock_user.employee_type = "Volunteer"
        mock_user.other_mails = ["personal@gmail.com"]
        mock_user.department = "Operations"
        mock_user.company_name = "SJI Fire"
        mock_user.on_premises_extension_attributes = None

        result = manager._to_entra_user(mock_user)

        assert result.id == "123"
        assert result.display_name == "John Doe"
        assert result.job_title == "Firefighter"
        assert result.mobile_phone == "555-1234"
        assert result.personal_email == "personal@gmail.com"

    def test_converts_user_with_extension_attributes(self, manager):
        mock_user = MagicMock()
        mock_user.id = "123"
        mock_user.display_name = "John Doe"
        mock_user.given_name = "John"
        mock_user.surname = "Doe"
        mock_user.mail = None
        mock_user.user_principal_name = None
        mock_user.employee_id = None
        mock_user.account_enabled = True
        mock_user.job_title = None
        mock_user.mobile_phone = None
        mock_user.business_phones = []
        mock_user.office_location = None
        mock_user.employee_hire_date = None
        mock_user.employee_type = None
        mock_user.other_mails = []
        mock_user.department = None
        mock_user.company_name = None

        mock_ext = MagicMock()
        mock_ext.extension_attribute1 = "Captain"
        mock_ext.extension_attribute2 = "2025-12-31"
        mock_ext.extension_attribute3 = "Firefighter, EMT"
        mock_user.on_premises_extension_attributes = mock_ext

        result = manager._to_entra_user(mock_user)

        assert result.extension_attribute1 == "Captain"
        assert result.extension_attribute2 == "2025-12-31"
        assert result.extension_attribute3 == "Firefighter, EMT"

    def test_converts_hire_date_to_string(self, manager):
        from datetime import datetime

        mock_user = MagicMock()
        mock_user.id = "123"
        mock_user.display_name = "John Doe"
        mock_user.given_name = "John"
        mock_user.surname = "Doe"
        mock_user.mail = None
        mock_user.user_principal_name = None
        mock_user.employee_id = None
        mock_user.account_enabled = True
        mock_user.job_title = None
        mock_user.mobile_phone = None
        mock_user.business_phones = []
        mock_user.office_location = None
        mock_user.employee_hire_date = datetime(2020, 1, 15)
        mock_user.employee_type = None
        mock_user.other_mails = []
        mock_user.department = None
        mock_user.company_name = None
        mock_user.on_premises_extension_attributes = None

        result = manager._to_entra_user(mock_user)

        assert result.employee_hire_date == "2020-01-15T00:00:00"
