"""Tests for the task runner CLI."""

from unittest.mock import patch

from sjifire.ops.tasks.registry import _tasks, register
from sjifire.ops.tasks.runner import _run


def _clear_registry():
    _tasks.clear()


class TestRunnerCli:
    def setup_method(self):
        _clear_registry()

    def teardown_method(self):
        _clear_registry()

    async def test_list_shows_tasks(self, capsys):
        @register("alpha")
        async def alpha():
            return 0

        @register("beta")
        async def beta():
            return 0

        with patch("sjifire.ops.tasks.runner._import_tasks"):
            exit_code = await _run(["--list"])

        assert exit_code == 0
        output = capsys.readouterr().out
        assert "alpha" in output
        assert "beta" in output

    async def test_list_empty_registry(self, capsys):
        with patch("sjifire.ops.tasks.runner._import_tasks"):
            exit_code = await _run(["--list"])

        assert exit_code == 0
        assert "No tasks" in capsys.readouterr().out

    async def test_run_specific_task(self, capsys):
        @register("my-task")
        async def my_task():
            return 7

        with patch("sjifire.ops.tasks.runner._import_tasks"):
            exit_code = await _run(["my-task"])

        assert exit_code == 0
        assert "OK" in capsys.readouterr().out

    async def test_run_unknown_task(self, capsys):
        with patch("sjifire.ops.tasks.runner._import_tasks"):
            exit_code = await _run(["nonexistent"])

        assert exit_code == 1
        assert "FAIL" in capsys.readouterr().out

    async def test_run_all_tasks(self, capsys):
        @register("a")
        async def a():
            return 1

        @register("b")
        async def b():
            return 2

        with patch("sjifire.ops.tasks.runner._import_tasks"):
            exit_code = await _run([])

        assert exit_code == 0
        output = capsys.readouterr().out
        assert "a" in output
        assert "b" in output

    async def test_failure_returns_exit_code_1(self, capsys):
        @register("fail")
        async def fail():
            msg = "boom"
            raise RuntimeError(msg)

        with patch("sjifire.ops.tasks.runner._import_tasks"):
            exit_code = await _run(["fail"])

        assert exit_code == 1
        assert "FAIL" in capsys.readouterr().out

    async def test_mixed_results_returns_exit_code_1(self, capsys):
        @register("good")
        async def good():
            return 5

        @register("bad")
        async def bad():
            msg = "nope"
            raise RuntimeError(msg)

        with patch("sjifire.ops.tasks.runner._import_tasks"):
            exit_code = await _run([])

        assert exit_code == 1
