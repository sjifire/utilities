"""Sync email signatures for all users via transport rule.

Sets Exchange custom attributes on each user's mailbox, then creates a
mail flow transport rule that appends a personalized signature + organization
footer to all outgoing emails. Works with ALL email clients because the
signature is applied server-side by Exchange after the email is sent.

Templates are loaded from ``config/signatures/<name>.json``, ``.html``, and
``.txt``. The default template is ``default``. Attribute slot assignments
are defined in ``sjifire.core.extension_attrs``.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sjifire.core.extension_attrs import (
    SIG_PHONE_PS,
    SIG_PHONE_TOKEN,
    SIG_TITLE_HTML_PS,
    SIG_TITLE_HTML_TOKEN,
    SIG_TITLE_TEXT_PS,
    SIG_TITLE_TEXT_TOKEN,
)
from sjifire.entra.users import EntraUser, EntraUserManager
from sjifire.exchange.client import ExchangeOnlineClient, _escape_ps_string

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Silence verbose HTTP logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

# =============================================================================
# SIGNATURE TEMPLATE
# =============================================================================

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "signatures"


@dataclass
class SignatureTemplate:
    """Loaded signature template with rendered HTML and text."""

    rule_name: str
    company_name_html: str
    company_name_text: str
    office_phone: str
    rule_html: str
    rule_text: str


def load_template(name: str = "default") -> SignatureTemplate:
    """Load a signature template from config/signatures/.

    Reads ``<name>.json`` for settings and ``<name>.html`` / ``<name>.txt``
    for the transport rule templates. Template placeholders use ``{{key}}``
    syntax and are filled from the JSON config plus Exchange attribute tokens.

    Args:
        name: Template name (matches filenames without extension)

    Returns:
        SignatureTemplate with rendered HTML and text
    """
    config_path = CONFIG_DIR / f"{name}.json"
    html_path = CONFIG_DIR / f"{name}.html"
    text_path = CONFIG_DIR / f"{name}.txt"

    for p in (config_path, html_path, text_path):
        if not p.exists():
            msg = f"Signature template file not found: {p}"
            raise FileNotFoundError(msg)

    config = json.loads(config_path.read_text())

    # Build substitution variables from config + Exchange tokens
    variables = {
        **config,
        "title_html_token": SIG_TITLE_HTML_TOKEN,
        "title_text_token": SIG_TITLE_TEXT_TOKEN,
        "phone_token": SIG_PHONE_TOKEN,
    }

    def _render(template: str) -> str:
        result = template
        for key, value in variables.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        return result

    return SignatureTemplate(
        rule_name=config["rule_name"],
        company_name_html=config["company_name_html"],
        company_name_text=config["company_name_text"],
        office_phone=config["office_phone"],
        rule_html=_render(html_path.read_text()),
        rule_text=_render(text_path.read_text()),
    )


# =============================================================================


def _format_phone(number: str) -> str:
    """Format a phone number to (XXX) XXX-XXXX."""
    digits = re.sub(r"\D", "", number)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return number


def _get_title_line(user: EntraUser) -> str:
    """Build the title line for a user's signature.

    Returns:
        Title string or empty string if no rank/title
    """
    rank = user.rank.replace("<br>", "").strip() if user.rank else None
    job_title = user.job_title
    if rank and job_title and rank != job_title:
        return f"{rank} - {job_title}"
    elif rank:
        return rank
    elif job_title:
        return job_title
    return ""


def _get_phone_line(user: EntraUser, office_phone: str) -> str:
    """Build the phone line for a user's signature."""
    phones = f"Office: {office_phone}"
    if user.mobile_phone:
        phones += f" | Cell: {_format_phone(user.mobile_phone)}"
    return phones


def sync_custom_attributes(
    users: list[EntraUser],
    template: SignatureTemplate,
    dry_run: bool = False,
    remove: bool = False,
) -> tuple[int, int, list[str]]:
    """Batch-set custom attributes on mailboxes for transport rule personalization.

    Slot assignments defined in ``sjifire.core.extension_attrs``.

    Returns:
        Tuple of (success_count, failure_count, error_messages)
    """
    if dry_run:
        for user in users:
            if remove:
                logger.info("Would clear attributes for %s (%s)", user.display_name, user.email)
            else:
                title = _get_title_line(user) or "(none)"
                phone = _get_phone_line(user, template.office_phone)
                logger.info(
                    "Would set attributes for %s (%s): title=%s, phone=%s",
                    user.display_name,
                    user.email,
                    title,
                    phone,
                )
        return len(users), 0, []

    client = ExchangeOnlineClient()

    # Build batch PowerShell script
    commands = [
        "$success = 0",
        "$failure = 0",
        "$errors = @()",
    ]

    for user in users:
        email = _escape_ps_string(user.email)
        if remove:
            attr1 = ""
            attr2 = ""
            attr3 = ""
        else:
            title = _get_title_line(user)
            attr1 = _escape_ps_string(f"{title}<br>" if title else "")
            attr2 = _escape_ps_string(_get_phone_line(user, template.office_phone))
            attr3 = _escape_ps_string(title)

        commands.append(
            f"try {{ "
            f"Set-Mailbox -Identity '{email}'"
            f" -{SIG_TITLE_HTML_PS} '{attr1}'"
            f" -{SIG_PHONE_PS} '{attr2}'"
            f" -{SIG_TITLE_TEXT_PS} '{attr3}'"
            f" -ErrorAction Stop; "
            f"$success++ "
            f"}} catch {{ "
            f"$failure++; "
            f"$errors += '{email}: ' + $_.Exception.Message "
            f"}}"
        )

    commands.append(
        "@{ Success = $success; Failure = $failure; Errors = $errors } | ConvertTo-Json -Depth 2"
    )

    result = client._run_powershell(commands)

    if not result or not isinstance(result, dict):
        return 0, len(users), ["Failed to execute batch script"]

    success = result.get("Success", 0)
    failure = result.get("Failure", 0)
    errors_data = result.get("Errors", [])

    # Normalize errors (single item comes as string, not list)
    if isinstance(errors_data, str):
        errors = [errors_data] if errors_data else []
    elif isinstance(errors_data, list):
        errors = [str(e) for e in errors_data if e]
    else:
        errors = []

    action = "Cleared" if remove else "Set"
    logger.info("%s custom attributes: %d successful, %d failed", action, success, failure)
    for error in errors[:10]:
        logger.error("  %s", error)

    return success, failure, errors


def sync_transport_rule(
    template: SignatureTemplate, dry_run: bool = False
) -> tuple[bool, str | None]:
    """Create or update the combined signature + footer transport rule.

    Returns:
        Tuple of (success, error_message)
    """
    if dry_run:
        logger.info("Would create/update mail flow rule: %s", template.rule_name)
        logger.info("Rule HTML:")
        print(template.rule_html)
        return True, None

    client = ExchangeOnlineClient()
    escaped_html = template.rule_html.replace("'", "''")

    script = f"""
$ruleName = '{template.rule_name}'
$rule = Get-TransportRule -Identity $ruleName -ErrorAction SilentlyContinue

if ($rule) {{
    Write-Output "Updating existing rule: $ruleName"
    Set-TransportRule -Identity $ruleName `
        -FromScope InOrganization `
        -SentToScope $null `
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
        logger.info("Mail flow rule '%s' synced successfully", template.rule_name)
        return True, None
    else:
        error = f"Failed to sync mail flow rule: {result}"
        logger.error(error)
        return False, error


def remove_transport_rule(
    template: SignatureTemplate, dry_run: bool = False
) -> tuple[bool, str | None]:
    """Remove the signature + footer transport rule.

    Also removes the old footer-only rule if it exists.

    Returns:
        Tuple of (success, error_message)
    """
    if dry_run:
        logger.info("Would remove mail flow rule: %s", template.rule_name)
        return True, None

    client = ExchangeOnlineClient()

    script = f"""
# Remove new combined rule
$ruleName = '{template.rule_name}'
$rule = Get-TransportRule -Identity $ruleName -ErrorAction SilentlyContinue
if ($rule) {{
    Remove-TransportRule -Identity $ruleName -Confirm:$false -ErrorAction Stop
    Write-Output "REMOVED: $ruleName"
}} else {{
    Write-Output "NOT_FOUND: $ruleName"
}}

# Also remove old footer-only rule if it exists
$oldRule = Get-TransportRule -Identity 'SJIFR Email Footer' -ErrorAction SilentlyContinue
if ($oldRule) {{
    Remove-TransportRule -Identity 'SJIFR Email Footer' -Confirm:$false -ErrorAction Stop
    Write-Output 'REMOVED: SJIFR Email Footer'
}}
"""

    result = client._run_powershell([script], parse_json=False)
    result_str = str(result) if result else ""

    if "REMOVED" in result_str or "NOT_FOUND" in result_str:
        logger.info("Mail flow rules removed successfully")
        return True, None
    else:
        error = f"Failed to remove mail flow rules: {result}"
        logger.error(error)
        return False, error


async def run_sync(
    dry_run: bool = False,
    email: str | None = None,
    preview: bool = False,
    remove: bool = False,
    template_name: str = "default",
) -> int:
    """Run signature sync.

    Returns:
        Exit code
    """
    logger.info("=" * 60)
    if remove:
        logger.info("Email Signature Removal")
    else:
        logger.info("Email Signature Sync")
    logger.info("=" * 60)

    if preview and not email:
        logger.error("--preview requires --email")
        return 1

    if dry_run:
        logger.info("DRY RUN - no changes will be made")

    # Load signature template
    try:
        template = load_template(template_name)
        logger.info("Template: %s", template_name)
    except FileNotFoundError as e:
        logger.error("Template error: %s", e)
        return 1

    # Get users from Entra ID
    logger.info("")
    logger.info("Fetching users from Entra ID...")

    user_manager = EntraUserManager()

    try:
        all_users = await user_manager.get_employees(include_disabled=False)
        users = [u for u in all_users if u.email and u.email.lower().endswith("@sjifire.org")]

        if email:
            users = [u for u in users if u.email and u.email.lower() == email.lower()]
            if not users:
                logger.error("User not found: %s", email)
                return 1

        logger.info("Found %d employees", len(users))

    except Exception as e:
        logger.error("Failed to fetch users: %s", e)
        return 1

    # Handle preview mode
    if preview:
        user = users[0]
        title = _get_title_line(user) or "(none)"
        phone = _get_phone_line(user, template.office_phone)
        logger.info("")
        logger.info("Signature preview for %s (%s):", user.display_name, user.email)
        logger.info("-" * 40)
        logger.info("Rank: %s", user.rank or "(none)")
        logger.info("Job Title: %s", user.job_title or "(none)")
        logger.info("-" * 40)
        logger.info("%s (title): %s", SIG_TITLE_HTML_PS, title)
        logger.info("%s (phone): %s", SIG_PHONE_PS, phone)
        logger.info("-" * 40)
        logger.info("Signature as rendered:")
        name = f"{user.first_name} {user.last_name}".strip() or user.display_name
        print(f"{name}")
        if _get_title_line(user):
            print(f"{_get_title_line(user)}")
        print(f"{template.company_name_text}")
        print(f"{phone}")
        return 0

    # Sync or remove custom attributes
    logger.info("")
    if remove:
        logger.info("Clearing custom attributes...")
    else:
        logger.info("Syncing custom attributes...")

    success, failure, errors = sync_custom_attributes(users, template, dry_run, remove)

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Attributes Summary")
    logger.info("=" * 60)
    logger.info("  Successful: %d", success)
    logger.info("  Failed: %d", failure)

    if errors:
        logger.info("")
        logger.info("Errors:")
        for error in errors[:10]:
            logger.error("  %s", error)
        if len(errors) > 10:
            logger.error("  ... and %d more", len(errors) - 10)

    # Sync or remove transport rule (skip for single-user syncs)
    if not email:
        logger.info("")
        if remove:
            logger.info("Removing mail flow rule...")
            rule_ok, rule_error = remove_transport_rule(template, dry_run)
        else:
            logger.info("Syncing mail flow rule...")
            rule_ok, rule_error = sync_transport_rule(template, dry_run)

        if not rule_ok:
            logger.error("Transport rule operation failed: %s", rule_error)
            return 1

    return 0 if failure == 0 else 1


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sync email signatures via transport rule. "
        "Sets custom attributes on mailboxes and creates a mail flow rule."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--email",
        metavar="EMAIL",
        help="Only sync custom attributes for this user",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Show signature preview for the user (requires --email)",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove all custom attributes and the mail flow rule",
    )
    parser.add_argument(
        "--template",
        default="default",
        metavar="NAME",
        help="Signature template name from config/signatures/ (default: default)",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(
        run_sync(
            dry_run=args.dry_run,
            email=args.email,
            preview=args.preview,
            remove=args.remove,
            template_name=args.template,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
