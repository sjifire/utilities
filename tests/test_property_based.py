"""Property-based tests using hypothesis."""

import string

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from sjifire.aladtec.models import Member
from sjifire.aladtec.scraper import AladtecMemberScraper
from sjifire.entra.users import EntraUserManager

# Custom strategies
name_chars = st.sampled_from(string.ascii_letters + " '-")
valid_name = st.text(alphabet=name_chars, min_size=1, max_size=30).map(str.strip).filter(bool)
email_local = st.text(
    alphabet=string.ascii_lowercase + string.digits + "._-", min_size=1, max_size=20
)
email_domain = st.sampled_from(["sjifire.org", "example.com", "test.org"])
valid_email = st.builds(lambda local, domain: f"{local}@{domain}", email_local, email_domain)


class TestMemberProperties:
    """Property-based tests for Member model."""

    @given(first=valid_name, last=valid_name)
    def test_display_name_contains_both_names(self, first, last):
        member = Member(id="1", first_name=first, last_name=last)
        assert first in member.display_name
        assert last in member.display_name

    @given(first=valid_name, last=valid_name)
    def test_display_name_format(self, first, last):
        member = Member(id="1", first_name=first, last_name=last)
        assert member.display_name == f"{first} {last}"

    @given(email=valid_email)
    def test_user_principal_name_matches_email(self, email):
        member = Member(id="1", first_name="Test", last_name="User", email=email)
        assert member.user_principal_name == email

    @given(status=st.sampled_from(["Active", "active", "ACTIVE", None]))
    def test_is_active_for_active_statuses(self, status):
        member = Member(id="1", first_name="Test", last_name="User", status=status)
        assert member.is_active is True

    @given(status=st.text(min_size=1).filter(lambda s: s.lower() != "active"))
    def test_is_inactive_for_other_statuses(self, status):
        member = Member(id="1", first_name="Test", last_name="User", status=status)
        assert member.is_active is False


class TestCSVRowParsingProperties:
    """Property-based tests for CSV row parsing."""

    @given(first=valid_name, last=valid_name, email=valid_email)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_parse_csv_row_extracts_names(self, mock_env_vars, first, last, email):
        scraper = AladtecMemberScraper()
        row = {"first name": first, "last name": last, "email": email}
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == first
        assert member.last_name == last

    @given(first=valid_name, last=valid_name)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_parse_csv_row_handles_name_column_comma_format(self, mock_env_vars, first, last):
        assume(first.strip() and last.strip())  # Non-empty after strip
        scraper = AladtecMemberScraper()
        row = {"name": f"{last}, {first}"}
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == first
        assert member.last_name == last

    @given(first=valid_name, last=valid_name)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_parse_csv_row_handles_name_column_space_format(self, mock_env_vars, first, last):
        assume(" " not in first and " " not in last)  # Single-word names for this test
        assume(first.strip() and last.strip())
        scraper = AladtecMemberScraper()
        row = {"name": f"{first} {last}"}
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == first
        assert member.last_name == last

    @given(
        first=valid_name,
        last=valid_name,
        col_name=st.sampled_from(["First Name", "first name", "FIRST NAME", " first name "]),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_parse_csv_row_normalizes_column_names(self, mock_env_vars, first, last, col_name):
        scraper = AladtecMemberScraper()
        row = {col_name: first, "last name": last}
        member = scraper._parse_csv_row(row)

        assert member is not None
        assert member.first_name == first


class TestUPNGenerationProperties:
    """Property-based tests for UPN generation."""

    @given(first=valid_name, last=valid_name)
    def test_upn_is_valid_email_format(self, first, last):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "sjifire.org"

        upn = manager.generate_upn(first, last)

        # Should be valid email format
        assert "@" in upn
        assert upn.endswith("@sjifire.org")

    @given(first=valid_name, last=valid_name)
    def test_upn_is_lowercase(self, first, last):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "sjifire.org"

        upn = manager.generate_upn(first, last)

        # Local part should be lowercase
        local_part = upn.split("@")[0]
        assert local_part == local_part.lower()

    @given(first=valid_name, last=valid_name)
    def test_upn_has_no_spaces(self, first, last):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "sjifire.org"

        upn = manager.generate_upn(first, last)

        assert " " not in upn

    @given(first=valid_name, last=valid_name)
    def test_upn_has_no_apostrophes(self, first, last):
        manager = EntraUserManager.__new__(EntraUserManager)
        manager.domain = "sjifire.org"

        upn = manager.generate_upn(first, last)

        assert "'" not in upn


class TestPasswordGenerationProperties:
    """Property-based tests for password generation."""

    @given(iteration=st.integers(min_value=1, max_value=100))
    def test_password_always_meets_length_requirement(self, iteration):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert len(password) >= 16

    @given(iteration=st.integers(min_value=1, max_value=100))
    def test_password_always_has_uppercase(self, iteration):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert any(c in string.ascii_uppercase for c in password)

    @given(iteration=st.integers(min_value=1, max_value=100))
    def test_password_always_has_lowercase(self, iteration):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert any(c in string.ascii_lowercase for c in password)

    @given(iteration=st.integers(min_value=1, max_value=100))
    def test_password_always_has_digit(self, iteration):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert any(c in string.digits for c in password)

    @given(iteration=st.integers(min_value=1, max_value=100))
    def test_password_always_has_special_char(self, iteration):
        manager = EntraUserManager.__new__(EntraUserManager)
        password = manager._generate_temp_password()

        assert any(c in "!@#$%" for c in password)

    @given(count=st.integers(min_value=2, max_value=20))
    @settings(max_examples=20)
    def test_passwords_are_random(self, count):
        manager = EntraUserManager.__new__(EntraUserManager)
        passwords = [manager._generate_temp_password() for _ in range(count)]

        # All passwords should be unique
        assert len(set(passwords)) == len(passwords)
