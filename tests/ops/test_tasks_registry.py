"""Tests for the task registry framework."""

from sjifire.ops.tasks.registry import (
    TaskResult,
    _tasks,
    is_auto,
    list_tasks,
    register,
    run_all,
    run_task,
)

# ---------------------------------------------------------------------------
# Helpers — isolated registry for tests
# ---------------------------------------------------------------------------


def _clear_registry():
    _tasks.clear()


class TestRegister:
    def setup_method(self):
        _clear_registry()

    def teardown_method(self):
        _clear_registry()

    def test_registers_task(self):
        @register("test-task")
        async def my_task():
            return 42

        assert "test-task" in _tasks
        fn, auto = _tasks["test-task"]
        assert fn is my_task
        assert auto is True

    def test_registers_manual_task(self):
        @register("manual-task", auto=False)
        async def my_task():
            return 42

        assert "manual-task" in _tasks
        fn, auto = _tasks["manual-task"]
        assert fn is my_task
        assert auto is False

    def test_multiple_registrations(self):
        @register("a")
        async def task_a():
            return 1

        @register("b")
        async def task_b():
            return 2

        assert list_tasks() == ["a", "b"]


class TestRunTask:
    def setup_method(self):
        _clear_registry()

    def teardown_method(self):
        _clear_registry()

    async def test_runs_task_successfully(self):
        @register("ok-task")
        async def ok_task():
            return 5

        result = await run_task("ok-task")

        assert result.ok is True
        assert result.name == "ok-task"
        assert result.count == 5
        assert result.elapsed > 0
        assert result.error == ""

    async def test_handles_task_error(self):
        @register("bad-task")
        async def bad_task():
            msg = "something broke"
            raise RuntimeError(msg)

        result = await run_task("bad-task")

        assert result.ok is False
        assert result.name == "bad-task"
        assert "something broke" in result.error
        assert result.elapsed >= 0

    async def test_unknown_task(self):
        result = await run_task("nonexistent")

        assert result.ok is False
        assert "Unknown task" in result.error


class TestRunAll:
    def setup_method(self):
        _clear_registry()

    def teardown_method(self):
        _clear_registry()

    async def test_runs_all_tasks_in_order(self):
        execution_order = []

        @register("alpha")
        async def task_alpha():
            execution_order.append("alpha")
            return 1

        @register("beta")
        async def task_beta():
            execution_order.append("beta")
            return 2

        results = await run_all()

        assert len(results) == 2
        assert execution_order == ["alpha", "beta"]  # sorted
        assert all(r.ok for r in results)

    async def test_continues_after_failure(self):
        @register("fail-first")
        async def fail_first():
            msg = "boom"
            raise RuntimeError(msg)

        @register("succeed-second")
        async def succeed_second():
            return 10

        results = await run_all()

        assert len(results) == 2
        assert results[0].ok is False
        assert results[1].ok is True
        assert results[1].count == 10

    async def test_skips_manual_tasks(self):
        @register("auto-task")
        async def auto_task():
            return 1

        @register("manual-task", auto=False)
        async def manual_task():
            return 2

        results = await run_all()

        assert len(results) == 1
        assert results[0].name == "auto-task"
        assert results[0].count == 1

    async def test_manual_task_runs_explicitly(self):
        @register("manual-only", auto=False)
        async def manual_only():
            return 99

        result = await run_task("manual-only")
        assert result.ok is True
        assert result.count == 99

    async def test_empty_registry(self):
        results = await run_all()
        assert results == []


class TestIsAuto:
    def setup_method(self):
        _clear_registry()

    def teardown_method(self):
        _clear_registry()

    def test_auto_task(self):
        @register("auto")
        async def t():
            return 0

        assert is_auto("auto") is True

    def test_manual_task(self):
        @register("manual", auto=False)
        async def t():
            return 0

        assert is_auto("manual") is False

    def test_unknown_task(self):
        assert is_auto("nonexistent") is False


class TestListTasks:
    def setup_method(self):
        _clear_registry()

    def teardown_method(self):
        _clear_registry()

    def test_empty_registry(self):
        assert list_tasks() == []

    def test_sorted_output(self):
        @register("zebra")
        async def z():
            return 0

        @register("alpha")
        async def a():
            return 0

        assert list_tasks() == ["alpha", "zebra"]


class TestTaskResult:
    def test_defaults(self):
        r = TaskResult(name="test", ok=True)
        assert r.count == 0
        assert r.elapsed == 0.0
        assert r.error == ""

    def test_with_values(self):
        r = TaskResult(name="x", ok=False, count=5, elapsed=1.23, error="oops")
        assert r.name == "x"
        assert r.ok is False
        assert r.count == 5
        assert r.elapsed == 1.23
        assert r.error == "oops"
