"""Tests for sjifire.entra.users."""

import string

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
