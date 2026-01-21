#!/usr/bin/env python3
"""Update Microsoft Form via Power Automate."""

import argparse
import asyncio

from ..forms.updater import FormUpdater
from ..utils.config import get_settings


async def update_form(
    dry_run: bool = True,
    verbose: bool = False,
) -> bool:
    """Build payload and send to Power Automate."""
    settings = get_settings()
    updater = FormUpdater(settings)

    print("Microsoft Forms Update Script")
    print("=" * 40)

    # Load config files
    print("\nLoading configuration files...")
    apparatus = updater.load_apparatus()
    print(f"✓ Loaded {len(apparatus)} apparatus from config/apparatus.csv")

    personnel = updater.load_personnel()
    print(f"✓ Loaded {len(personnel)} personnel from config/personnel.json")

    # Build payload
    payload = updater.build_payload(apparatus=apparatus, personnel=personnel)

    # Print summary
    if verbose:
        updater.print_summary(payload)
    else:
        print(f"\nPayload: {len(apparatus)} apparatus, {len(personnel)} personnel")

    # Send or dry run
    if dry_run:
        print("\n=== Dry Run Mode ===")
        print("Use --send flag to actually trigger Power Automate")

        if settings.has_power_automate:
            url_preview = settings.power_automate_url[:50] + "..."
            print(f"Power Automate URL: {url_preview}")
        else:
            print("⚠ POWER_AUTOMATE_URL not set in .env")

        if verbose:
            import json
            print("\nPayload that would be sent:")
            payload_dict = {
                "updateType": payload.update_type,
                "timestamp": payload.timestamp.isoformat(),
                "apparatusChoices": payload.apparatus_choices,
                "personnelChoices": payload.personnel_choices,
            }
            print(json.dumps(payload_dict, indent=2))

        return True
    else:
        return await updater.send_update(payload)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Update Microsoft Form via Power Automate"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send the update (default is dry run)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed output",
    )

    args = parser.parse_args()

    success = asyncio.run(
        update_form(
            dry_run=not args.send,
            verbose=args.verbose,
        )
    )

    if not success:
        exit(1)


if __name__ == "__main__":
    main()
