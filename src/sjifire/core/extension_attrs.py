"""Extension attribute slot registry for Entra ID and Exchange Online.

Two separate systems share the same 1-15 numbering:

  **Entra ID extensionAttribute1-15** (Graph API, on-premises extension attributes)
  Stored in Azure AD. Read/written via Microsoft Graph API. Synced from
  Aladtec by ``entra-user-sync``. Available on the ``EntraUser`` dataclass
  as ``extension_attribute1`` through ``extension_attribute4``.

  **Exchange CustomAttribute1-15** (PowerShell ``Set-Mailbox``)
  Stored in Exchange Online. Read/written via Exchange Online PowerShell.
  Available in transport rule templates as ``%%CustomAttribute1%%`` etc.

These two systems are **completely independent** — writing to Entra
extensionAttribute1 does NOT affect Exchange CustomAttribute1 and vice
versa. However, Azure AD Connect *can* sync Entra extension attributes
into Exchange custom attributes with the same number, so avoid using
the same slot numbers in both systems to prevent collisions.

Slot assignments are defined below. Always update this file when
claiming a new slot.
"""


# =============================================================================
# Entra ID extensionAttribute slots (Graph API)
# Written by: entra-user-sync (from Aladtec data)
# Read by: EntraUser properties, group strategies, iSpyFire sync, ops tools
# =============================================================================


class EntraAttr:
    """Entra ID extensionAttribute slot assignments.

    These map to ``onPremisesExtensionAttributes.extensionAttributeN`` in
    the Graph API and to ``EntraUser.extension_attributeN`` in code.
    """

    RANK = 1
    """Rank/title from Aladtec (e.g., "Captain", "Battalion Chief").
    Property: ``EntraUser.rank``"""

    EVIP = 2
    """EVIP certification expiration date (ISO format).
    Property: ``EntraUser.evip``"""

    POSITIONS = 3
    """Comma-delimited scheduling positions from Aladtec.
    Property: ``EntraUser.positions`` (returns set)"""

    SCHEDULES = 4
    """Comma-delimited schedule visibility from Aladtec.
    Property: ``EntraUser.schedules`` (returns set)"""

    # 5-15: available


# =============================================================================
# Exchange Online CustomAttribute slots (PowerShell Set-Mailbox)
# Written by: signature-sync
# Read by: Exchange transport rule templates (%%CustomAttributeN%%)
# =============================================================================


class ExchangeAttr:
    """Exchange Online CustomAttribute slot assignments.

    These map to ``-CustomAttributeN`` in ``Set-Mailbox`` and to
    ``%%CustomAttributeN%%`` tokens in transport rule templates.
    """

    # 1-5: RESERVED — Azure AD Connect syncs Entra extensionAttribute1-4
    # into Exchange CustomAttribute1-4. Slot 5 is used by Exchange
    # internally (calendar folder ID). Do NOT write to these.

    SIG_TITLE_HTML = 6
    """Signature title line for HTML template.
    Contains ``"Title<br>"`` when user has a title, empty string otherwise.
    The trailing ``<br>`` avoids a blank line in the HTML when empty."""

    SIG_PHONE = 7
    """Signature phone line (shared by HTML and text templates).
    Example: ``"Office: (360) 378-5334 | Cell: (360) 555-1234"``"""

    SIG_TITLE_TEXT = 8
    """Signature title line for plain text template.
    Same as SIG_TITLE_HTML but without the ``<br>`` suffix."""

    # 9-15: available


# Convenience: PowerShell attribute names for Set-Mailbox
SIG_TITLE_HTML_PS = f"CustomAttribute{ExchangeAttr.SIG_TITLE_HTML}"
SIG_PHONE_PS = f"CustomAttribute{ExchangeAttr.SIG_PHONE}"
SIG_TITLE_TEXT_PS = f"CustomAttribute{ExchangeAttr.SIG_TITLE_TEXT}"

# Transport rule tokens
SIG_TITLE_HTML_TOKEN = f"%%{SIG_TITLE_HTML_PS}%%"
SIG_PHONE_TOKEN = f"%%{SIG_PHONE_PS}%%"
SIG_TITLE_TEXT_TOKEN = f"%%{SIG_TITLE_TEXT_PS}%%"
