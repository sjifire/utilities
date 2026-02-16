"""Task registry — lightweight framework for background tasks.

Each task is an async function decorated with ``@register(name)``.
The runner discovers tasks via ``list_tasks()`` and executes them
with ``run_task()`` or ``run_all()``.
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Module-level registry: name -> async callable
_tasks: dict[str, object] = {}


@dataclass
class TaskResult:
    """Result of a single task execution."""

    name: str
    ok: bool
    count: int = 0
    elapsed: float = 0.0
    error: str = ""


def register(name: str):
    """Decorator to register an async task function.

    Usage::

        @register("neris-cache")
        async def neris_cache_refresh() -> int:
            ...  # returns count of items processed
    """

    def decorator(fn):
        _tasks[name] = fn
        return fn

    return decorator


async def run_task(name: str) -> TaskResult:
    """Run a single registered task with timing and error handling.

    Args:
        name: Registered task name

    Returns:
        TaskResult with outcome details
    """
    fn = _tasks.get(name)
    if fn is None:
        return TaskResult(name=name, ok=False, error=f"Unknown task: {name}")

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
    """Run all registered tasks sequentially.

    Returns:
        List of TaskResult for each task
    """
    results = []
    for name in sorted(_tasks):
        results.append(await run_task(name))  # noqa: PERF401 — can't use comprehension with await
    return results


def list_tasks() -> list[str]:
    """Return sorted list of registered task names."""
    return sorted(_tasks)
