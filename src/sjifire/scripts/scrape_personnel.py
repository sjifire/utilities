#!/usr/bin/env python3
"""Scrape personnel from ESO Suite incidents."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from ..eso.models import PersonnelList
from ..eso.scraper import ESOScraper
from ..utils.config import get_settings


async def scrape_personnel(
    max_incidents: int = 30,
    headless: bool = True,
    output_path: Path | None = None,
) -> PersonnelList:
    """Scrape personnel from ESO and save to file."""
    settings = get_settings()

    if not settings.has_eso_credentials:
        raise ValueError(
            "ESO credentials not configured. "
            "Set ESO_USERNAME, ESO_PASSWORD, ESO_AGENCY in .env"
        )

    print("ESO Personnel Scraper")
    print("=" * 40)

    async with ESOScraper(settings) as scraper:
        await scraper.login()
        personnel = await scraper.scrape_personnel_from_incidents(max_incidents)

    # Create personnel list
    personnel_list = PersonnelList(
        scraped_at=datetime.utcnow(),
        personnel=personnel,
    )

    # Save to JSON
    if output_path is None:
        output_path = settings.config_dir / "personnel.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to JSON-serializable dict with camelCase keys
    output_data = {
        "scrapedAt": personnel_list.scraped_at.isoformat(),
        "source": personnel_list.source,
        "count": personnel_list.count,
        "personnel": [
            {
                "esoId": p.eso_id,
                "firstName": p.first_name,
                "lastName": p.last_name,
                "fullName": p.full_name,
            }
            for p in personnel_list.personnel
        ],
    }

    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\nSaved {len(personnel)} personnel to: {output_path}")

    # Also save CSV
    csv_path = output_path.with_suffix(".csv")
    with open(csv_path, "w") as f:
        f.write("esoId,lastName,firstName,fullName\n")
        for p in personnel:
            f.write(f'{p.eso_id},"{p.last_name}","{p.first_name}","{p.full_name}"\n')

    print(f"Saved CSV to: {csv_path}")

    # Print summary
    print("\n=== Personnel Summary ===")
    for p in personnel:
        print(f"  {p.eso_id}: {p.full_name}")

    return personnel_list


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Scrape personnel from ESO Suite")
    parser.add_argument(
        "--max-incidents",
        type=int,
        default=30,
        help="Maximum number of incidents to scrape (default: 30)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in visible mode (not headless)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output file path (default: config/personnel.json)",
    )

    args = parser.parse_args()

    asyncio.run(
        scrape_personnel(
            max_incidents=args.max_incidents,
            headless=not args.no_headless,
            output_path=args.output,
        )
    )


if __name__ == "__main__":
    main()
