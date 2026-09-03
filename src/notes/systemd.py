"""The systemd user timer that runs `notes tick` every minute: unit rendering, enable, disable, and status.

`notes notifications enable` writes `notes-tick.service` and `notes-tick.timer` into the user unit directory,
imports the display variables into the user manager so the service reaches the desktop session, and starts the
timer; `disable` reverts all of that; `status` reports what the user manager knows. Every `systemctl` and
`journalctl` call goes through `proc.run_command`, so tests script the answers and never touch the real manager.

The timer is the Linux integration, not a requirement: `notes tick` stays portable and runs from cron on a system
without systemd. `Persistent=true` is what makes the timer preferable, it catches the runs missed while the
machine was off.
"""

import json
import os
import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from notes import proc
from notes.errors import SystemdError
from notes.proc import CommandResult

SERVICE_NAME = "notes-tick.service"
TIMER_NAME = "notes-tick.timer"
ENTRY_POINT = "notes"
"""The console script name; when the running program is that script, the service runs it directly."""

DISPLAY_VARIABLES = ("DISPLAY", "WAYLAND_DISPLAY")
"""Imported into the user manager on enable, so `notify-send` and the sound reach the current desktop session."""

SYSTEMCTL_TIMEOUT = 30.0
"""Seconds one `systemctl` or `journalctl` call may take; a wedged user manager must not hang the command."""

JOURNAL_LINES = 5

_SAFE_ARGUMENT_RE = re.compile(r"[A-Za-z0-9_./:@+=,-]+")


# Locations and the command the service runs


def unit_dir() -> Path:
    """The user unit directory: `$XDG_CONFIG_HOME/systemd/user`, or `~/.config/systemd/user` when unset."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "systemd" / "user"


def service_path() -> Path:
    return unit_dir() / SERVICE_NAME


def timer_path() -> Path:
    return unit_dir() / TIMER_NAME


def exec_command(argv0: str | None = None, executable: str | None = None) -> tuple[str, ...]:
    """The command the service runs, without the `tick` argument.

    The absolute path of the `notes` entry point when that is what is running (the normal case after
    `uv tool install`), otherwise the interpreter with `-m notes`, which works from a virtual environment or tests.
    """
    argv0 = sys.argv[0] if argv0 is None else argv0
    executable = sys.executable if executable is None else executable
    path = Path(argv0)
    if path.name == ENTRY_POINT:
        located = path if path.is_absolute() else Path(shutil.which(argv0) or argv0)
        if located.is_file():
            return (str(located.resolve()),)
    return (executable, "-m", "notes")


# Unit rendering


def quote(argument: str) -> str:
    """One `ExecStart` argument as the unit file parser expects it.

    `%` and `$` are doubled because they introduce specifiers and variable references; anything beyond the plain
    path characters is wrapped in double quotes with C-style escapes.
    """
    text = argument.replace("%", "%%").replace("$", "$$")
    if _SAFE_ARGUMENT_RE.fullmatch(argument):
        return text
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def render_service(exec_command: Sequence[str]) -> str:
    """`notes-tick.service`: a oneshot running `<exec_command> tick`, no working directory, output to the journal."""
    exec_start = " ".join(quote(argument) for argument in (*exec_command, "tick"))
    return (
        "[Unit]\n"
        "Description=notes: deliver due scheduled notes as desktop notifications\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={exec_start}\n"
    )


def render_timer() -> str:
    """`notes-tick.timer`: every minute, catching up on missed runs, with a loose accuracy to allow coalescing."""
    return (
        "[Unit]\n"
        "Description=notes: run notes tick every minute\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=minutely\n"
        "Persistent=true\n"
        "AccuracySec=10s\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


# systemctl and journalctl


def systemctl(*args: str) -> CommandResult:
    """Run `systemctl --user <args>`; a non-zero exit is the caller's to interpret, a missing binary is an error."""
    command = ["systemctl", "--user", *args]
    try:
        return proc.run_command(command, timeout=SYSTEMCTL_TIMEOUT)
    except proc.CommandNotFound as error:
        raise SystemdError(
            "systemctl is not installed or not on PATH; without systemd, run `notes tick` from cron every minute"
        ) from error
    except proc.CommandTimeout as error:
        raise SystemdError(f"systemctl {args[0]} timed out after {error.timeout:g} seconds") from error


def _checked(result: CommandResult, what: str) -> CommandResult:
    if not result.ok:
        raise SystemdError(f"{what}: {_tail(result.stderr) or f'systemctl exited with status {result.returncode}'}")
    return result


def _tail(text: str, lines: int = 3) -> str:
    """The last few non-empty lines of stderr, joined into one line."""
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(kept[-lines:])


def _journal() -> tuple[str, ...]:
    """The last `JOURNAL_LINES` lines the service logged; empty when `journalctl` is missing or the journal fails."""
    command = ["journalctl", "--user", "-u", SERVICE_NAME, "-n", str(JOURNAL_LINES), "--no-pager", "-q"]
    try:
        result = proc.run_command([*command, "-o", "short-iso"], timeout=SYSTEMCTL_TIMEOUT)
    except (proc.CommandNotFound, proc.CommandTimeout):
        return ()
    if not result.ok:
        return ()
    return tuple(line for line in result.stdout.splitlines() if line.strip())


# Enable, disable, status


@dataclass(frozen=True)
class EnableOutcome:
    """What `enable` did: the unit files it wrote, the command the service runs, and the variables it imported.

    `warnings` holds the non-fatal failures, so far only a refused `import-environment`.
    """

    service: Path
    timer: Path
    command: tuple[str, ...]
    imported: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def enable(exec_command: Sequence[str]) -> EnableOutcome:
    """Write both units, import the set display variables, reload the manager, and enable and start the timer.

    A refused `import-environment` is a warning: the timer still runs, the notification may just not reach the
    desktop until the variables are imported by other means. A failed reload or enable is a `SystemdError`.
    """
    directory = unit_dir()
    directory.mkdir(parents=True, exist_ok=True)
    service, timer = directory / SERVICE_NAME, directory / TIMER_NAME
    command = tuple(exec_command)
    service.write_text(render_service(command), encoding="utf-8")
    timer.write_text(render_timer(), encoding="utf-8")
    warnings: list[str] = []
    imported = tuple(name for name in DISPLAY_VARIABLES if os.environ.get(name))
    if imported:
        result = systemctl("import-environment", *imported)
        if not result.ok:
            detail = _tail(result.stderr) or f"systemctl exited with status {result.returncode}"
            warnings.append(f"importing {', '.join(imported)} into the user manager failed: {detail}")
    _checked(systemctl("daemon-reload"), "systemctl daemon-reload failed")
    _checked(systemctl("enable", "--now", TIMER_NAME), f"enabling {TIMER_NAME} failed")
    return EnableOutcome(service, timer, command, imported, tuple(warnings))


@dataclass(frozen=True)
class DisableOutcome:
    """What `disable` did: whether the manager accepted `disable --now`, and the unit files that were removed."""

    disabled: bool
    removed: tuple[Path, ...]

    @property
    def was_installed(self) -> bool:
        """False when there was nothing to disable: no unit files and a manager that does not know the timer."""
        return self.disabled or bool(self.removed)


def disable() -> DisableOutcome:
    """Stop and disable the timer, remove both unit files, and reload the manager.

    Running it when the timer was never enabled is not an error: the manager's refusal is expected when no unit
    file exists, and the outcome says nothing was installed.
    """
    paths = (service_path(), timer_path())
    installed = any(path.exists() for path in paths)
    result = systemctl("disable", "--now", TIMER_NAME)
    if not result.ok and installed:
        raise SystemdError(f"disabling {TIMER_NAME} failed: {_tail(result.stderr)}")
    removed = tuple(path for path in paths if path.exists())
    for path in removed:
        path.unlink()
    if result.ok or removed:
        _checked(systemctl("daemon-reload"), "systemctl daemon-reload failed")
    return DisableOutcome(result.ok, removed)


@dataclass(frozen=True)
class Status:
    """The timer as the user manager sees it.

    `installed` says whether both unit files exist in the user unit directory; `enabled` and `active` are the
    words `is-enabled` and `is-active` print (`enabled`, `disabled`, `not-found`, `active`, `inactive`, `failed`);
    `next_elapse` is the next scheduled run, `None` while the timer is not running; `journal` holds the last few
    lines the service logged.
    """

    installed: bool
    enabled: str
    active: str
    next_elapse: datetime | None
    journal: tuple[str, ...]


def status() -> Status:
    """Ask the user manager about the timer; an unreachable manager is a `SystemdError`."""
    installed = service_path().is_file() and timer_path().is_file()
    enabled = _state(systemctl("is-enabled", TIMER_NAME), "querying the timer's enablement failed")
    active = _state(systemctl("is-active", TIMER_NAME), "querying the timer's activity failed")
    next_elapse = _next_elapse(systemctl("list-timers", "--all", "--output=json", TIMER_NAME))
    return Status(installed, enabled, active, next_elapse, _journal())


def _state(result: CommandResult, what: str) -> str:
    """The state word `is-enabled` and `is-active` print; both exit non-zero for anything but the happy state."""
    state = result.stdout.strip()
    if state:
        return state
    if result.ok:
        return "unknown"
    raise SystemdError(f"{what}: {_tail(result.stderr) or f'systemctl exited with status {result.returncode}'}")


def _next_elapse(result: CommandResult) -> datetime | None:
    """The `next` field of `list-timers --output=json`, microseconds since the epoch, as a local aware datetime."""
    if not result.ok:
        return None
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("unit") == TIMER_NAME:
            usec = entry.get("next")
            if isinstance(usec, int) and not isinstance(usec, bool) and usec > 0:
                return datetime.fromtimestamp(usec / 1_000_000, tz=UTC).astimezone()
    return None
