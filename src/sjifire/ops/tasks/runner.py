"""CLI entry point for background tasks.

Usage::

    uv run ops-tasks              # run all scheduled tasks
    uv run ops-tasks neris-sync   # run specific task(s)
    uv run ops-tasks --list       # list available tasks

Exit code 0 on success, 1 on any failure.
"""

import argparse
import asyncio
import logging
import sys

from sjifire.ops.tasks.registry import is_auto, list_tasks, run_all, run_task

logger = logging.getLogger(__name__)


def _import_tasks() -> None:
    """Import all task modules to trigger @register decorators."""
    import sjifire.ops.tasks.dispatch_sync
    import sjifire.ops.tasks.ispyfire_sync
    import sjifire.ops.tasks.neris_sync  # noqa: F401


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops-tasks",
        description="Run background tasks for the SJI Fire ops server.",
    )
    parser.add_argument(
        "tasks",
        nargs="*",
        metavar="TASK",
        help="Task name(s) to run. Omit to run all scheduled tasks.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_tasks",
        help="List available tasks and exit.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    """Execute tasks based on parsed args. Returns exit code."""
    _import_tasks()

    if args.list_tasks:
        tasks = list_tasks()
        if tasks:
            for name in tasks:
                suffix = "" if is_auto(name) else "  (manual)"
                print(f"  {name}{suffix}")
        else:
            print("No tasks registered")
        return 0

    if args.tasks:
        all_ok = True
        for name in args.tasks:
            result = await run_task(name)
            _print_result(result)
            if not result.ok:
                all_ok = False
        return 0 if all_ok else 1
    else:
        results = await run_all()
        for result in results:
            _print_result(result)
        return 0 if all(r.ok for r in results) else 1


def _print_result(result) -> None:
    """Print a task result to stdout."""
    if result.ok:
        print(f"  OK  {result.name}: {result.count} items in {result.elapsed:.1f}s")
    else:
        print(f"  FAIL {result.name}: {result.error} ({result.elapsed:.1f}s)")


def main() -> None:
    """Entry point for ``uv run ops-tasks``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()
    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)
