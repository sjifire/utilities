"""CLI entry point for background tasks.

Usage::

    uv run ops-tasks              # run all tasks
    uv run ops-tasks neris-sync   # run specific task
    uv run ops-tasks --list       # list available tasks

Exit code 0 on success, 1 on any failure.
"""

import asyncio
import logging
import sys

from dotenv import load_dotenv

from sjifire.ops.tasks.registry import list_tasks, run_all, run_task

logger = logging.getLogger(__name__)


def _import_tasks() -> None:
    """Import all task modules to trigger @register decorators."""
    import sjifire.ops.tasks.dispatch_sync
    import sjifire.ops.tasks.ispyfire_sync
    import sjifire.ops.tasks.neris_sync  # noqa: F401


async def _run(args: list[str]) -> int:
    """Parse args and execute tasks. Returns exit code."""
    _import_tasks()

    if "--list" in args:
        tasks = list_tasks()
        if tasks:
            for name in tasks:
                print(name)
        else:
            print("No tasks registered")
        return 0

    # Filter out flags, remaining args are task names
    task_names = [a for a in args if not a.startswith("-")]

    if task_names:
        # Run specific tasks
        all_ok = True
        for name in task_names:
            result = await run_task(name)
            _print_result(result)
            if not result.ok:
                all_ok = False
        return 0 if all_ok else 1
    else:
        # Run all tasks
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
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    exit_code = asyncio.run(_run(sys.argv[1:]))
    sys.exit(exit_code)
