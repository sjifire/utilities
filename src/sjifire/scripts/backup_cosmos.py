#!/usr/bin/env python3
"""Export Cosmos DB collections to JSON files for backup.

Usage:
    uv run backup-cosmos                    # Both collections
    uv run backup-cosmos --incidents-only   # Incidents only
    uv run backup-cosmos --dispatch-only    # Dispatch calls only
    uv run backup-cosmos --output /path/    # Custom output directory
"""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("azure").setLevel(logging.WARNING)


async def _backup_incidents(output_dir: Path, timestamp: str) -> int:
    """Export all incidents to a JSON file.

    Returns:
        Number of documents exported
    """
    from sjifire.mcp.incidents.store import IncidentStore

    async with IncidentStore() as store:
        docs = await store.list_all()

    data = [doc.model_dump(mode="json") for doc in docs]
    path = output_dir / f"cosmos_incidents_{timestamp}.json"
    path.write_text(json.dumps(data, indent=2))

    print(f"Exported {len(data)} incidents to {path}")
    return len(data)


async def _backup_dispatch(output_dir: Path, timestamp: str) -> int:
    """Export all dispatch calls to a JSON file.

    Returns:
        Number of documents exported
    """
    from sjifire.mcp.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        docs = await store.list_all()

    data = [doc.model_dump(mode="json") for doc in docs]
    path = output_dir / f"cosmos_dispatch_{timestamp}.json"
    path.write_text(json.dumps(data, indent=2))

    print(f"Exported {len(data)} dispatch calls to {path}")
    return len(data)


async def _run(args: argparse.Namespace) -> None:
    """Run the backup."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    total = 0

    if not args.dispatch_only:
        total += await _backup_incidents(output_dir, timestamp)
    if not args.incidents_only:
        total += await _backup_dispatch(output_dir, timestamp)

    print(f"\nBackup complete: {total} documents exported to {output_dir}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Export Cosmos DB collections to JSON files for backup.",
    )
    parser.add_argument(
        "--output",
        default="backups",
        help="Output directory (default: backups/)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--incidents-only",
        action="store_true",
        help="Export incidents only",
    )
    group.add_argument(
        "--dispatch-only",
        action="store_true",
        help="Export dispatch calls only",
    )

    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
