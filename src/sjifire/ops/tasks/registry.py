"""Task registry — lightweight framework for background tasks.

Each task is an async function decorated with ``@register(name)``.
The runner discovers tasks via ``list_tasks()`` and executes them
with ``run_task()`` or ``run_all()``.
"""

import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Module-level registry: name -> (async callable, auto flag)
_TaskFn = Callable[[], Coroutine[Any, Any, int]]
_tasks: dict[str, tuple[_TaskFn, bool]] = {}


@dataclass
class TaskResult:
    """Result of a single task execution."""

    name: str
    ok: bool
    count: int = 0
    elapsed: float = 0.0
    error: str = ""


def register(name: str, *, auto: bool = True):
    """Decorator to register an async task function.

    Args:
        name: Task name used by the CLI runner.
        auto: If True (default), included in ``run_all()``.
            Set to False for expensive tasks that should only
            run when explicitly requested by name.

    Usage::

        @register("neris-sync")
        async def neris_sync() -> int:
            ...  # returns count of items processed

        @register("expensive-task", auto=False)
        async def expensive() -> int:
            ...  # only runs via: uv run ops-tasks expensive-task
    """

    def decorator(fn):
        _tasks[name] = (fn, auto)
        return fn

    return decorator


async def run_task(name: str) -> TaskResult:
    """Run a single registered task with timing and error handling.

    Args:
        name: Registered task name

    Returns:
        TaskResult with outcome details
    """
    entry = _tasks.get(name)
    if entry is None:
        return TaskResult(name=name, ok=False, error=f"Unknown task: {name}")

    fn = entry[0]
    t0 = time.monotonic()
    try:
        count = await fn()
        elapsed = time.monotonic() - t0
        logger.info("Task %s completed: %d items in %.1fs", name, count, elapsed)
        return TaskResult(name=name, ok=True, count=count, elapsed=elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.exception("Task %s failed after %.1fs", name, elapsed)
        return TaskResult(name=name, ok=False, elapsed=elapsed, error=str(exc))


async def run_all() -> list[TaskResult]:
    """Run all auto-registered tasks sequentially.

    Tasks registered with ``auto=False`` are skipped — they must
    be run explicitly by name.

    Returns:
        List of TaskResult for each task
    """
    results = []
    for name in sorted(_tasks):
        _fn, auto = _tasks[name]
        if not auto:
            continue
        results.append(await run_task(name))
    return results


def list_tasks() -> list[str]:
    """Return sorted list of registered task names."""
    return sorted(_tasks)


def is_auto(name: str) -> bool:
    """Return whether a task is included in automatic runs."""
    entry = _tasks.get(name)
    return entry[1] if entry else False
