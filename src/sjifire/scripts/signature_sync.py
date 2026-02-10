"""Sync email signatures for all users.

Sets OWA (Outlook on the web) signatures for all users based on their
Entra ID profile data (name, rank/title, job title).

Signature format:
- Users with rank AND job title: Name / Rank - Job Title / Company
- Users with rank only: Name / Rank / Company
- Users with job title only: Name / Job Title / Company
- Users with neither: Name / Company

The footer (logo, address, phone, disclaimer) is added via a separate
mail flow rule and is not part of the signature.
"""

import argparse
import asyncio
import logging
import sys

from sjifire.entra.users import EntraUser, EntraUserManager
from sjifire.exchange.client import ExchangeOnlineClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Silence verbose HTTP logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

# =============================================================================
# SIGNATURE TEMPLATES
# =============================================================================
# Edit these templates to change the signature format for all users.
# Available placeholders: {name}, {title}, {company}, {phones}

COMPANY_NAME = "San Juan Island Fire & Rescue"
OFFICE_PHONE = "(360) 378-5334"

# HTML signature for users WITH a title (rank or job title)
HTML_TEMPLATE_WITH_TITLE = """\
<p style="margin: 0; font-size: 14px;">
<strong style="color: #333;">{name}</strong><br>
<span style="color: #666;">{title}<br>
{company}<br>
{phones}</span>
</p>"""

# HTML signature for users WITHOUT a title
HTML_TEMPLATE_NO_TITLE = """\
<p style="margin: 0; font-size: 14px;">
<strong style="color: #333;">{name}</strong><br>
<span style="color: #666;">{company}<br>
{phones}</span>
</p>"""

# Plain text signature for users WITH a title
TEXT_TEMPLATE_WITH_TITLE = """\
{name}
{title}
{company}
{phones}"""

# Plain text signature for users WITHOUT a title
TEXT_TEMPLATE_NO_TITLE = """\
{name}
{company}
{phones}"""

# =============================================================================
# FOOTER TEMPLATE (added via mail flow rule to all outgoing emails)
# =============================================================================

FOOTER_RULE_NAME = "SJIFR Email Footer"
LOGO_URL = "https://www.sjifire.org/assets/sjifire-logo-clear.png"
WEBSITE_URL = "https://www.sjifire.org"
ADDRESS = "1011 Mullis St, Friday Harbor, WA 98250"
DISCLAIMER = (
    "This email may contain confidential information intended only for the recipient. "
    "If you received this in error, please notify the sender and delete this message."
)

FOOTER_HTML = f"""\
<div style="margin-top: 35px; padding-top: 15px; border-top: 2px solid #72150c;">
<table cellpadding="0" cellspacing="0" style="font-size: 11px; width: 100%;">
<tr>
<td style="padding-right: 15px; vertical-align: top; width: 70px;">
<img src="{LOGO_URL}" alt="SJIFR" width="60" style="border-radius: 4px;">
</td>
<td style="vertical-align: top; line-height: 1.5; color: #666;">
<strong style="color: #72150c; font-size: 12px;">{COMPANY_NAME}</strong><br>
{ADDRESS}<br>
<a href="{WEBSITE_URL}">{WEBSITE_URL}</a>
</td>
<td style="text-align: right; vertical-align: top; padding-left: 20px;">
<strong style="color: #333; font-size: 13px;">{OFFICE_PHONE}</strong><br>
<span style="font-size: 11px; color: #72150c; font-weight: bold;">Emergency: 911</span>
</td>
</tr>
</table>
<p style="font-size: 10px; color: #999; line-height: 1.4; margin-top: 12px; margin-bottom: 0;">
{DISCLAIMER}
</p>
</div>"""

FOOTER_TEXT = f"""
---
{COMPANY_NAME}
{ADDRESS}
{WEBSITE_URL}
{OFFICE_PHONE} | Emergency: 911

{DISCLAIMER}
"""

# =============================================================================


def _get_title_line(user: EntraUser) -> str | None:
    """Build the title line for a user's signature.

    Args:
        user: EntraUser with profile data

    Returns:
        Title string or None if no rank/title
    """
    if user.rank and user.job_title:
        return f"{user.rank} - {user.job_title}"
    elif user.rank:
        return user.rank
    elif user.job_title:
        return user.job_title
    return None


def _get_phone_line(user: EntraUser) -> str:
    """Build the phone line for a user's signature.

    Args:
        user: EntraUser with profile data

    Returns:
        Phone string with office and optionally cell
    """
    phones = f"Office: {OFFICE_PHONE}"
    if user.mobile_phone:
        phones += f" | Cell: {user.mobile_phone}"
    return phones


def generate_signature_html(user: EntraUser) -> str:
    """Generate HTML signature for a user.

    Args:
        user: EntraUser with profile data

    Returns:
        HTML signature string
    """
    # Use first/last name, not display_name (which may include rank prefix)
    name = f"{user.first_name} {user.last_name}".strip() or user.display_name
    title = _get_title_line(user)
    phones = _get_phone_line(user)

    if title:
        return HTML_TEMPLATE_WITH_TITLE.format(
            name=name, title=title, company=COMPANY_NAME, phones=phones
        )
    else:
        return HTML_TEMPLATE_NO_TITLE.format(name=name, company=COMPANY_NAME, phones=phones)


def generate_signature_text(user: EntraUser) -> str:
    """Generate plain text signature for a user.

    Args:
        user: EntraUser with profile data

    Returns:
        Plain text signature string
    """
    # Use first/last name, not display_name (which may include rank prefix)
    name = f"{user.first_name} {user.last_name}".strip() or user.display_name
    title = _get_title_line(user)
    phones = _get_phone_line(user)

    if title:
        return TEXT_TEMPLATE_WITH_TITLE.format(
            name=name, title=title, company=COMPANY_NAME, phones=phones
        )
    else:
        return TEXT_TEMPLATE_NO_TITLE.format(name=name, company=COMPANY_NAME, phones=phones)


async def sync_user_signature(
    client: ExchangeOnlineClient,
    user: EntraUser,
    dry_run: bool = False,
    remove: bool = False,
) -> tuple[bool, str | None]:
    """Sync or remove signature for a single user.

    Args:
        client: Exchange Online client
        user: EntraUser to sync signature for
        dry_run: If True, don't make changes
        remove: If True, remove the signature instead of setting it

    Returns:
        Tuple of (success, error_message)
    """
    if not user.email:
        return False, "No email address"

    if remove:
        if dry_run:
            logger.info(f"Would remove signature for {user.display_name} ({user.email})")
            return True, None

        # Remove signature via PowerShell
        script = f"""
Set-MailboxMessageConfiguration -Identity '{user.email}' `
    -SignatureHtml '' `
    -SignatureText '' `
    -AutoAddSignature $false `
    -AutoAddSignatureOnReply $false `
    -ErrorAction Stop
Write-Output 'SUCCESS'
"""
        result = client._run_powershell([script], parse_json=False)

        if result and "SUCCESS" in str(result):
            logger.info(f"Removed signature for {user.display_name} ({user.email})")
            return True, None
        else:
            error = f"Failed to remove signature: {result}"
            logger.error(f"{user.email}: {error}")
            return False, error

    # Set signature
    html_sig = generate_signature_html(user)
    text_sig = generate_signature_text(user)

    if dry_run:
        logger.info(f"Would set signature for {user.display_name} ({user.email})")
        return True, None

    # Set signature via PowerShell
    script = f"""
$config = Get-MailboxMessageConfiguration -Identity '{user.email}' -ErrorAction Stop
Set-MailboxMessageConfiguration -Identity '{user.email}' `
    -SignatureHtml @'
{html_sig}
'@ `
    -SignatureText @'
{text_sig}
'@ `
    -AutoAddSignature $true `
    -AutoAddSignatureOnReply $true `
    -ErrorAction Stop
Write-Output 'SUCCESS'
"""

    result = client._run_powershell([script], parse_json=False)

    if result and "SUCCESS" in str(result):
        logger.info(f"Set signature for {user.display_name} ({user.email})")
        return True, None
    else:
        error = f"Failed to set signature: {result}"
        logger.error(f"{user.email}: {error}")
        return False, error


async def sync_signatures(
    users: list[EntraUser],
    dry_run: bool = False,
    remove: bool = False,
) -> tuple[int, int, list[str]]:
    """Sync or remove signatures for all users.

    Args:
        users: List of EntraUser objects
        dry_run: If True, don't make changes
        remove: If True, remove signatures instead of setting them

    Returns:
        Tuple of (success_count, failure_count, error_messages)
    """
    client = ExchangeOnlineClient()

    success = 0
    failure = 0
    errors: list[str] = []

    # Process users in batches to avoid overwhelming Exchange
    # Each user requires a separate PowerShell connection currently
    for user in users:
        ok, error = await sync_user_signature(client, user, dry_run, remove)
        if ok:
            success += 1
        else:
            failure += 1
            if error:
                errors.append(f"{user.email}: {error}")

    await client.close()
    return success, failure, errors


def sync_footer(dry_run: bool = False) -> tuple[bool, str | None]:
    """Sync the organization email footer via mail flow rule.

    Creates or updates a transport rule that appends the footer HTML
    to all outgoing emails from @sjifire.org.

    Args:
        dry_run: If True, don't make changes

    Returns:
        Tuple of (success, error_message)
    """
    if dry_run:
        logger.info(f"Would create/update mail flow rule: {FOOTER_RULE_NAME}")
        logger.info("Footer HTML:")
        print(FOOTER_HTML)
        return True, None

    client = ExchangeOnlineClient()

    # Escape single quotes in HTML for PowerShell
    escaped_html = FOOTER_HTML.replace("'", "''")

    script = f"""
$ruleName = '{FOOTER_RULE_NAME}'
$rule = Get-TransportRule -Identity $ruleName -ErrorAction SilentlyContinue

if ($rule) {{
    Write-Output "Updating existing rule: $ruleName"
    Set-TransportRule -Identity $ruleName `
        -ApplyHtmlDisclaimerText @'
{escaped_html}
'@ `
        -ApplyHtmlDisclaimerLocation Append `
        -ApplyHtmlDisclaimerFallbackAction Wrap `
        -ErrorAction Stop
}} else {{
    Write-Output "Creating new rule: $ruleName"
    New-TransportRule -Name $ruleName `
        -FromScope InOrganization `
        -ApplyHtmlDisclaimerText @'
{escaped_html}
'@ `
        -ApplyHtmlDisclaimerLocation Append `
        -ApplyHtmlDisclaimerFallbackAction Wrap `
        -ErrorAction Stop
}}
Write-Output 'SUCCESS'
"""

    result = client._run_powershell([script], parse_json=False)

    if result and "SUCCESS" in str(result):
        logger.info(f"Mail flow rule '{FOOTER_RULE_NAME}' synced successfully")
        return True, None
    else:
        error = f"Failed to sync mail flow rule: {result}"
        logger.error(error)
        return False, error


def remove_footer(dry_run: bool = False) -> tuple[bool, str | None]:
    """Remove the organization email footer mail flow rule.

    Args:
        dry_run: If True, don't make changes

    Returns:
        Tuple of (success, error_message)
    """
    if dry_run:
        logger.info(f"Would remove mail flow rule: {FOOTER_RULE_NAME}")
        return True, None

    client = ExchangeOnlineClient()

    script = f"""
$ruleName = '{FOOTER_RULE_NAME}'
$rule = Get-TransportRule -Identity $ruleName -ErrorAction SilentlyContinue

if ($rule) {{
    Remove-TransportRule -Identity $ruleName -Confirm:$false -ErrorAction Stop
    Write-Output 'REMOVED'
}} else {{
    Write-Output 'NOT_FOUND'
}}
"""

    result = client._run_powershell([script], parse_json=False)

    if result and "REMOVED" in str(result):
        logger.info(f"Mail flow rule '{FOOTER_RULE_NAME}' removed successfully")
        return True, None
    elif result and "NOT_FOUND" in str(result):
        logger.info(f"Mail flow rule '{FOOTER_RULE_NAME}' not found (already removed)")
        return True, None
    else:
        error = f"Failed to remove mail flow rule: {result}"
        logger.error(error)
        return False, error


async def run_sync(
    dry_run: bool = False,
    email: str | None = None,
    preview: bool = False,
    remove: bool = False,
) -> int:
    """Run signature sync.

    Args:
        dry_run: If True, don't make changes
        email: If provided, only sync this user
        preview: If True, show signature preview for the user
        remove: If True, remove all signatures and footer rule

    Returns:
        Exit code
    """
    logger.info("=" * 60)
    if remove:
        logger.info("Email Signature & Footer Removal")
    else:
        logger.info("Email Signature Sync")
    logger.info("=" * 60)

    if preview and not email:
        logger.error("--preview requires --email")
        return 1

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

    # Get users from Entra ID
    logger.info("")
    logger.info("Fetching users from Entra ID...")

    user_manager = EntraUserManager()

    try:
        # Get only employees (excludes room/resource mailboxes)
        all_users = await user_manager.get_employees(include_disabled=False)

        # Filter to sjifire.org domain
        users = [u for u in all_users if u.email and u.email.lower().endswith("@sjifire.org")]

        if email:
            # Filter to specific user
            users = [u for u in users if u.email and u.email.lower() == email.lower()]
            if not users:
                logger.error(f"User not found: {email}")
                return 1

        logger.info(f"Found {len(users)} employees")

    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        return 1

    # Handle preview mode
    if preview:
        user = users[0]
        logger.info("")
        logger.info(f"Signature preview for {user.display_name} ({user.email}):")
        logger.info("-" * 40)
        logger.info(f"Rank: {user.rank or '(none)'}")
        logger.info(f"Job Title: {user.job_title or '(none)'}")
        logger.info("-" * 40)
        logger.info("HTML Signature:")
        print(generate_signature_html(user))
        logger.info("-" * 40)
        logger.info("Text Signature:")
        print(generate_signature_text(user))
        return 0

    # Sync or remove signatures
    logger.info("")
    if remove:
        logger.info("Removing signatures...")
    else:
        logger.info("Syncing signatures...")

    success, failure, errors = await sync_signatures(users, dry_run, remove)

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Summary")
    logger.info("=" * 60)
    logger.info(f"  Successful: {success}")
    logger.info(f"  Failed: {failure}")

    if errors:
        logger.info("")
        logger.info("Errors:")
        for error in errors[:10]:  # Limit to first 10
            logger.error(f"  {error}")
        if len(errors) > 10:
            logger.error(f"  ... and {len(errors) - 10} more")

    # Sync or remove footer mail flow rule
    logger.info("")
    if remove:
        logger.info("Removing email footer mail flow rule...")
        footer_ok, footer_error = remove_footer(dry_run)
    else:
        logger.info("Syncing email footer mail flow rule...")
        footer_ok, footer_error = sync_footer(dry_run)

    if not footer_ok:
        logger.error(f"Footer operation failed: {footer_error}")
        return 1

    return 0 if failure == 0 else 1


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sync email signatures for all users. "
        "Sets OWA signatures based on Entra ID profile data."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--email",
        metavar="EMAIL",
        help="Only sync signature for this user",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Show signature preview for the user (requires --email)",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove all signatures and the footer mail flow rule",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(
        run_sync(
            dry_run=args.dry_run,
            email=args.email,
            preview=args.preview,
            remove=args.remove,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
