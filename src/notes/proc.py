"""The single subprocess seam: every external program runs through `run_command`, and tests replace `runner`."""

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one external command; `stdout` and `stderr` are empty when the streams were not captured."""

    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandNotFound(Exception):
    """The executable does not exist on `PATH`."""

    def __init__(self, command: Sequence[str]) -> None:
        self.command = tuple(command)
        super().__init__(f"command not found: {self.command[0]}")


class CommandTimeout(Exception):
    """The command did not finish within the given timeout and was killed."""

    def __init__(self, command: Sequence[str], timeout: float) -> None:
        self.command = tuple(command)
        self.timeout = timeout
        super().__init__(f"command timed out after {timeout:g} seconds: {' '.join(self.command)}")


class Runner(Protocol):
    """The signature of `run_command`; a test fake implements it and is assigned to `runner`."""

    def __call__(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        input: str | None = None,
        timeout: float | None = None,
        capture: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


def _subprocess_runner(
    args: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    input: str | None = None,
    timeout: float | None = None,
    capture: bool = True,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    command = tuple(str(arg) for arg in args)
    full_env = None if env is None else {**os.environ, **env}
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=input,
            timeout=timeout,
            capture_output=capture,
            text=True,
            env=full_env,
            check=False,
        )
    except FileNotFoundError:
        if cwd is not None and not Path(cwd).is_dir():
            raise
        raise CommandNotFound(command) from None
    except subprocess.TimeoutExpired as error:
        raise CommandTimeout(command, timeout or 0) from error
    return CommandResult(command, completed.returncode, completed.stdout or "", completed.stderr or "")


runner: Runner = _subprocess_runner
"""The hook tests replace: assign any `Runner` here to intercept every external command."""


def run_command(
    args: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    input: str | None = None,
    timeout: float | None = None,
    capture: bool = True,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run an external program as an argument array, never through a shell.

    `env` is an overlay on the current environment, so callers add variables without losing `PATH` or `HOME`.
    A missing executable raises `CommandNotFound`,
    an exceeded `timeout` raises `CommandTimeout` after the process is killed,
    and a non-zero exit is an ordinary `CommandResult` for the caller to interpret.
    """
    return runner(args, cwd=cwd, input=input, timeout=timeout, capture=capture, env=env)
