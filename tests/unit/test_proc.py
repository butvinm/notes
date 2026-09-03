"""Tests for the subprocess seam: real commands through `run_command` and the replaceable `runner` hook."""

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from notes import proc
from notes.proc import CommandNotFound, CommandResult, CommandTimeout, run_command

PYTHON = sys.executable


def test_captures_streams_and_exit_code() -> None:
    script = "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)"

    result = run_command([PYTHON, "-c", script])

    assert result == CommandResult((PYTHON, "-c", script), 3, "out", "err")
    assert not result.ok


def test_zero_exit_is_ok() -> None:
    result = run_command([PYTHON, "-c", "pass"])

    assert result.ok
    assert result.returncode == 0


def test_input_reaches_stdin() -> None:
    result = run_command([PYTHON, "-c", "import sys; print(sys.stdin.read().upper())"], input="abc")

    assert result.stdout == "ABC\n"


def test_env_is_an_overlay_on_the_current_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTES_TEST_EXISTING", "kept")
    script = "import os; print(os.environ['NOTES_TEST_EXISTING'], os.environ['NOTES_TEST_ADDED'])"

    result = run_command([PYTHON, "-c", script], env={"NOTES_TEST_ADDED": "added"})

    assert result.stdout == "kept added\n"


def test_cwd_is_honored(tmp_path: Path) -> None:
    result = run_command([PYTHON, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)

    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


def test_path_arguments_are_stringified(tmp_path: Path) -> None:
    result = run_command([PYTHON, "-c", "import sys; print(sys.argv[1])", tmp_path])

    assert result.stdout.strip() == str(tmp_path)
    assert result.command[-1] == str(tmp_path)


def test_missing_executable_raises_command_not_found() -> None:
    with pytest.raises(CommandNotFound, match="command not found: notes-no-such-binary") as info:
        run_command(["notes-no-such-binary", "--version"])

    assert info.value.command == ("notes-no-such-binary", "--version")


def test_missing_cwd_is_not_reported_as_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_command([PYTHON, "-c", "pass"], cwd=tmp_path / "missing")


def test_timeout_raises_command_timeout() -> None:
    with pytest.raises(CommandTimeout, match="timed out after 0.2 seconds: sleep 5") as info:
        run_command(["sleep", "5"], timeout=0.2)

    assert info.value.timeout == 0.2


def test_uncaptured_streams_are_empty_in_the_result(capfd: pytest.CaptureFixture[str]) -> None:
    result = run_command([PYTHON, "-c", "print('visible')"], capture=False)

    assert result.ok
    assert result.stdout == ""
    assert result.stderr == ""
    assert capfd.readouterr().out == "visible\n"


def test_runner_hook_intercepts_every_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[object, ...]] = []

    def fake(
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        input: str | None = None,
        timeout: float | None = None,
        capture: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        calls.append((tuple(args), cwd, input, timeout, capture, env))
        return CommandResult(tuple(str(arg) for arg in args), 0, "fake output", "")

    monkeypatch.setattr(proc, "runner", fake)

    result = run_command(["git", "status"], cwd=tmp_path, timeout=15)

    assert result.stdout == "fake output"
    assert calls == [(("git", "status"), tmp_path, None, 15, True, None)]
