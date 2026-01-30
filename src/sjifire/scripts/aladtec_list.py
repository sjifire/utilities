"""CLI script to list Aladtec members."""

import argparse
import csv
import json
import logging
import sys
from dataclasses import asdict

from sjifire.aladtec.scraper import AladtecScraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# All columns in display order
ALL_COLUMNS = [
    "first_name",
    "last_name",
    "email",
    "phone",
    "home_phone",
    "employee_type",
    "positions",
    "title",
    "status",
    "work_group",
    "pay_profile",
    "employee_id",
    "station_assignment",
    "evip",
    "date_hired",
]


def run_list(output_format: str = "table") -> int:
    """List Aladtec members.

    Args:
        output_format: Output format (table, csv, json)

    Returns:
        Exit code
    """
    logger.info("Fetching members from Aladtec...")

    try:
        with AladtecScraper() as scraper:
            if not scraper.login():
                logger.error("Failed to log in to Aladtec")
                return 1

            members = scraper.get_members()

        if not members:
            logger.error("No members found")
            return 1

        logger.info(f"Found {len(members)} members\n")

    except Exception as e:
        logger.error(f"Failed to fetch members: {e}")
        return 1

    # Convert to dicts and format positions as comma-delimited
    data = []
    for m in members:
        row = asdict(m)
        # Convert positions list to comma-delimited string
        if row.get("positions"):
            row["positions"] = ", ".join(row["positions"])
        else:
            row["positions"] = ""
        data.append(row)

    # Filter to display columns
    filtered_data = [{col: row.get(col) for col in ALL_COLUMNS} for row in data]

    # Output
    if output_format == "json":
        print(json.dumps(filtered_data, indent=2))
    elif output_format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=ALL_COLUMNS)
        writer.writeheader()
        writer.writerows(filtered_data)
    else:  # table
        # Calculate column widths
        widths = {col: len(col) for col in ALL_COLUMNS}
        for row in filtered_data:
            for col in ALL_COLUMNS:
                val = str(row.get(col) or "")
                widths[col] = max(widths[col], min(len(val), 40))  # Cap at 40 chars

        # Print header
        header = " | ".join(col.ljust(widths[col]) for col in ALL_COLUMNS)
        print(header)
        print("-" * len(header))

        # Print rows
        for row in filtered_data:
            line = " | ".join(
                str(row.get(col) or "")[:40].ljust(widths[col]) for col in ALL_COLUMNS
            )
            print(line)

    return 0


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="List Aladtec members with all data including positions",
    )
    parser.add_argument(
        "--format",
        choices=["table", "csv", "json"],
        default="table",
        help="Output format (default: table)",
    )

    args = parser.parse_args()

    exit_code = run_list(output_format=args.format)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
