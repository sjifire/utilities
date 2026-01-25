"""Tests for sjifire.entra.groups."""

from sjifire.entra.groups import EntraGroup, GroupType


class TestEntraGroup:
    """Tests for the EntraGroup dataclass."""

    def test_microsoft_365_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Test Group",
            description=None,
            mail="test@sjifire.org",
            mail_enabled=True,
            security_enabled=False,
            group_types=["Unified"],
        )
        assert group.group_type == GroupType.MICROSOFT_365

    def test_security_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Test Security Group",
            description=None,
            mail=None,
            mail_enabled=False,
            security_enabled=True,
            group_types=[],
        )
        assert group.group_type == GroupType.SECURITY

    def test_distribution_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Test Distribution List",
            description=None,
            mail="dist@sjifire.org",
            mail_enabled=True,
            security_enabled=False,
            group_types=[],
        )
        assert group.group_type == GroupType.DISTRIBUTION

    def test_mail_enabled_security_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Mail Enabled Security",
            description=None,
            mail="security@sjifire.org",
            mail_enabled=True,
            security_enabled=True,
            group_types=[],
        )
        assert group.group_type == GroupType.MAIL_ENABLED_SECURITY

    def test_unknown_group_type(self):
        group = EntraGroup(
            id="group-1",
            display_name="Unknown Group",
            description=None,
            mail=None,
            mail_enabled=False,
            security_enabled=False,
            group_types=[],
        )
        assert group.group_type == GroupType.UNKNOWN

    def test_m365_takes_precedence(self):
        # Even with security_enabled=True, Unified groups are M365
        group = EntraGroup(
            id="group-1",
            display_name="M365 Group",
            description=None,
            mail="m365@sjifire.org",
            mail_enabled=True,
            security_enabled=True,
            group_types=["Unified"],
        )
        assert group.group_type == GroupType.MICROSOFT_365


class TestGroupType:
    """Tests for GroupType enum."""

    def test_enum_values(self):
        assert GroupType.SECURITY.value == "security"
        assert GroupType.MICROSOFT_365.value == "microsoft365"
        assert GroupType.DISTRIBUTION.value == "distribution"
        assert GroupType.MAIL_ENABLED_SECURITY.value == "mail_enabled_security"
        assert GroupType.UNKNOWN.value == "unknown"
