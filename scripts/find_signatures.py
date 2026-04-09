#!/usr/bin/env python3
"""Find active users with custom Outlook email signatures.

Uses Exchange Online PowerShell to query MailboxMessageConfiguration
for all user mailboxes and reports which ones have signatures enabled.
"""

from sjifire.exchange.client import ExchangeOnlineClient


def main() -> None:
    """Find and report users with custom Outlook email signatures."""
    client = ExchangeOnlineClient()

    # Get all user mailboxes with signature configuration in one connection
    commands = [
        (
            "$results = Get-EXOMailbox -RecipientTypeDetails UserMailbox "
            "-Filter 'IsMailboxEnabled -eq $true' -ResultSize Unlimited "
            "| ForEach-Object { "
            "$config = Get-MailboxMessageConfiguration -Identity $_.UserPrincipalName "
            "-ErrorAction SilentlyContinue; "
            "if ($config -and ($config.SignatureHtml -or $config.SignatureText)) { "
            "[PSCustomObject]@{ "
            "UPN = $_.UserPrincipalName; "
            "DisplayName = $_.DisplayName; "
            "AutoAdd = $config.AutoAddSignature; "
            "AutoAddReply = $config.AutoAddSignatureOnReply; "
            "HasHtml = [bool]$config.SignatureHtml; "
            "HasText = [bool]$config.SignatureText "
            "} } } | ConvertTo-Json -Depth 3"
        ),
        "if (-not $results) { '[]' }",
    ]

    print("Connecting to Exchange Online and querying mailbox signatures...")
    print("(This may take a minute for all mailboxes)\n")

    result = client._run_powershell(commands, parse_json=True)

    if not result:
        print("No users with custom signatures found (or query failed).")
        return

    # Normalize to list
    if isinstance(result, dict):
        if "raw" in result:
            print(f"Unexpected output: {result['raw'][:500]}")
            return
        users = [result]
    elif isinstance(result, list):
        users = result
    else:
        print(f"Unexpected result type: {type(result)}")
        return

    if not users:
        print("No users with custom signatures found.")
        return

    # Split into auto-add enabled vs just defined
    auto_add = [u for u in users if u.get("AutoAdd")]
    defined_only = [u for u in users if not u.get("AutoAdd")]

    print(f"Found {len(users)} user(s) with custom signatures:\n")

    if auto_add:
        print(f"--- AUTO-ADD ENABLED ({len(auto_add)}) ---")
        print("These users have signatures that auto-insert on new messages:\n")
        for u in sorted(auto_add, key=lambda x: x.get("DisplayName", "")):
            reply = " (+replies)" if u.get("AutoAddReply") else ""
            sig_type = "HTML" if u.get("HasHtml") else "Text"
            print(f"  {u['DisplayName']:<30} {u['UPN']:<40} {sig_type}{reply}")

    if defined_only:
        print(f"\n--- SIGNATURE DEFINED BUT NOT AUTO-ADDING ({len(defined_only)}) ---")
        print("These users have a signature saved but it's not set to auto-insert:\n")
        for u in sorted(defined_only, key=lambda x: x.get("DisplayName", "")):
            sig_type = "HTML" if u.get("HasHtml") else "Text"
            print(f"  {u['DisplayName']:<30} {u['UPN']:<40} {sig_type}")

    print(f"\nTotal: {len(users)} users with signatures "
          f"({len(auto_add)} auto-add, {len(defined_only)} defined only)")


if __name__ == "__main__":
    main()
