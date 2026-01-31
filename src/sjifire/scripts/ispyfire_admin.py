#!/usr/bin/env python3
"""iSpyFire admin CLI for user management.

Commands:
    activate    - Activate a user and send password reset email
    deactivate  - Deactivate a user and logout all devices
    list        - List users in iSpyFire
"""

import argparse
import logging
import sys

from sjifire.ispyfire.client import ISpyFireClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def cmd_activate(args) -> int:
    """Activate a user and send password reset email."""
    email = args.email

    with ISpyFireClient() as client:
        # Find the user
        people = client.get_people(include_deleted=True)
        target = next((p for p in people if p.email == email), None)

        if not target:
            print(f"Error: User not found: {email}")
            return 1

        print(f"Found: {target.display_name} <{target.email}>")
        print(f"  is_active: {target.is_active}")
        print(f"  is_login_active: {target.is_login_active}")

        if target.is_active and target.is_login_active:
            print("\nUser is already active.")
            return 0

        print("\nActivating user...")
        if client.reactivate_person(target.id, target.email):
            print("User activated and password reset email sent.")
            return 0
        else:
            print("Error: Failed to activate user.")
            return 1


def cmd_deactivate(args) -> int:
    """Deactivate a user and logout all devices."""
    email = args.email

    with ISpyFireClient() as client:
        # Find the user
        people = client.get_people(include_inactive=True)
        target = next((p for p in people if p.email == email), None)

        if not target:
            print(f"Error: User not found: {email}")
            return 1

        print(f"Found: {target.display_name} <{target.email}>")
        print(f"  is_active: {target.is_active}")
        print(f"  is_login_active: {target.is_login_active}")

        if not target.is_active and not target.is_login_active:
            print("\nUser is already inactive.")
            return 0

        print("\nDeactivating user (logout push, remove devices, set flags)...")
        if client.deactivate_person(target.id, email=target.email):
            print("User deactivated.")
            return 0
        else:
            print("Error: Failed to deactivate user.")
            return 1


def cmd_list(args) -> int:
    """List users in iSpyFire."""
    with ISpyFireClient() as client:
        people = client.get_people(
            include_inactive=args.inactive,
            include_deleted=args.inactive,
        )

        # Sort by last name
        people = sorted(people, key=lambda p: p.last_name)

        print(f"\n{'Status':<10} {'Name':<30} {'Email':<40} {'Title'}")
        print("-" * 100)

        for p in people:
            status = "Active" if p.is_active else "Inactive"
            title = p.title or ""
            print(f"{status:<10} {p.display_name:<30} {p.email or '':<40} {title}")

        print(f"\nTotal: {len(people)} users")
        return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="iSpyFire admin CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # activate command
    activate_parser = subparsers.add_parser(
        "activate", help="Activate a user and send password reset email"
    )
    activate_parser.add_argument("email", help="Email of user to activate")
    activate_parser.set_defaults(func=cmd_activate)

    # deactivate command
    deactivate_parser = subparsers.add_parser(
        "deactivate", help="Deactivate a user and logout all devices"
    )
    deactivate_parser.add_argument("email", help="Email of user to deactivate")
    deactivate_parser.set_defaults(func=cmd_deactivate)

    # list command
    list_parser = subparsers.add_parser("list", help="List users in iSpyFire")
    list_parser.add_argument("--inactive", action="store_true", help="Include inactive users")
    list_parser.set_defaults(func=cmd_list)

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
