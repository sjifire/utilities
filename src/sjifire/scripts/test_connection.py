#!/usr/bin/env python3
"""Test connections to ESO and Microsoft services."""

import argparse
import asyncio

from ..eso.scraper import ESOScraper
from ..utils.config import get_settings


async def test_eso_connection() -> bool:
    """Test ESO login."""
    settings = get_settings()

    if not settings.has_eso_credentials:
        print("✗ ESO credentials not configured")
        return False

    print("Testing ESO connection...")
    try:
        async with ESOScraper(settings) as scraper:
            await scraper.login()
        print("✓ ESO login successful")
        return True
    except Exception as e:
        print(f"✗ ESO login failed: {e}")
        return False


async def test_power_automate() -> bool:
    """Test Power Automate URL (just validates it's set)."""
    settings = get_settings()

    if not settings.has_power_automate:
        print("✗ Power Automate URL not configured")
        return False

    # Just check it looks like a valid URL
    url = settings.power_automate_url
    if url.startswith("https://") and "logic.azure.com" in url:
        print("✓ Power Automate URL configured")
        print(f"  URL: {url[:60]}...")
        return True
    else:
        print("⚠ Power Automate URL doesn't look valid")
        return False


async def test_graph_credentials() -> bool:
    """Test MS Graph credentials are set."""
    settings = get_settings()

    if not settings.has_graph_credentials:
        print("✗ MS Graph credentials not configured")
        return False

    print("✓ MS Graph credentials configured")
    print(f"  Tenant ID: {settings.ms_graph_tenant_id[:8]}...")
    print(f"  Client ID: {settings.ms_graph_client_id[:8]}...")
    return True


async def test_all():
    """Run all connection tests."""
    print("Connection Test Script")
    print("=" * 40)
    print()

    results = {
        "ESO Suite": await test_eso_connection(),
        "Power Automate": await test_power_automate(),
        "MS Graph": await test_graph_credentials(),
    }

    print()
    print("=" * 40)
    print("Summary:")
    for name, success in results.items():
        status = "✓" if success else "✗"
        print(f"  {status} {name}")

    return all(results.values())


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Test connections to services")
    parser.add_argument(
        "--eso-only",
        action="store_true",
        help="Only test ESO connection",
    )

    args = parser.parse_args()

    if args.eso_only:
        success = asyncio.run(test_eso_connection())
    else:
        success = asyncio.run(test_all())

    exit(0 if success else 1)


if __name__ == "__main__":
    main()
