#!/usr/bin/env python3
"""Manage kiosk URLs for station bay monitors.

Tokens are signed with ``KIOSK_SIGNING_KEY`` (from env or Key Vault).
Same label + same key always produces the same URL, so ``create`` is
idempotent — run it anytime to recover a URL.

Commands:
    create  - Generate a kiosk URL for a label
    verify  - Check whether a URL or token is valid
    test    - Print the test-mode URL (simulated calls)
    rotate  - Generate a new signing key (invalidates all URLs)

Usage:
    uv run kiosk-url create "Station 31 Bay TV"
    uv run kiosk-url verify <url-or-token>
    uv run kiosk-url test
    uv run kiosk-url rotate
"""

import argparse
import os
import sys
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv


def _server_url() -> str:
    from sjifire.core.config import get_org_config

    return os.getenv("MCP_SERVER_URL", f"https://ops.{get_org_config().domain}")


def _extract_token(value: str) -> str:
    """Extract token from a full URL or bare token string."""
    if value.startswith("http"):
        parsed = urlparse(value)
        params = parse_qs(parsed.query)
        tokens = params.get("token", [])
        if tokens:
            return tokens[0]
    return value


def cmd_create(args: argparse.Namespace) -> int:
    """Generate a kiosk URL for a label."""
    load_dotenv(verbose=False)
    from sjifire.ops.kiosk.store import create_token

    token = create_token(label=args.label)
    url = f"{_server_url()}/kiosk?token={token}"

    print(url)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify a kiosk URL or token."""
    load_dotenv(verbose=False)
    from sjifire.ops.kiosk.store import validate_token

    token = _extract_token(args.value)
    payload = validate_token(token)
    if payload is None:
        print("INVALID — bad signature or rotated key")
        return 1

    print(f"VALID — label: {payload.get('label', '(none)')}")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Print the test-mode URL."""
    load_dotenv(verbose=False)
    print(f"{_server_url()}/kiosk?test_mode=true")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    """Rotate the signing key in Azure Key Vault."""
    import secrets
    import subprocess

    load_dotenv(verbose=False)
    vault = os.getenv("KEY_VAULT_NAME", "gh-website-utilities")
    new_key = secrets.token_hex(32)

    print(f"Rotating KIOSK-SIGNING-KEY in vault '{vault}'...")
    result = subprocess.run(
        [
            "az",
            "keyvault",
            "secret",
            "set",
            "--vault-name",
            vault,
            "--name",
            "KIOSK-SIGNING-KEY",
            "--value",
            new_key,
            "--output",
            "none",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: {result.stderr.strip()}")
        return 1

    print("Key rotated. All existing kiosk URLs are now invalid.")
    print()
    print("Next steps:")
    print("  1. Deploy to pick up the new key:")
    print("     ./scripts/deploy-ops.sh")
    print("  2. Recreate URLs for each kiosk:")
    print('     uv run kiosk-url create "Station 31 Bay TV"')
    return 0


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Manage kiosk URLs for station bay monitors")
    sub = parser.add_subparsers(dest="command")

    create_p = sub.add_parser("create", help="Generate a kiosk URL")
    create_p.add_argument("label", help="Label for this kiosk (e.g. 'Station 31 Bay TV')")

    verify_p = sub.add_parser("verify", help="Verify a kiosk URL or token")
    verify_p.add_argument("value", help="Full kiosk URL or bare token string")

    sub.add_parser("test", help="Print the test-mode URL")
    sub.add_parser("rotate", help="Rotate signing key (invalidates all URLs)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "create": cmd_create,
        "verify": cmd_verify,
        "test": cmd_test,
        "rotate": cmd_rotate,
    }
    sys.exit(commands[args.command](args))
