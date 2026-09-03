"""Tests for `notes notifications enable|disable|status`, with every `systemctl` and `journalctl` call recorded."""

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes import systemd
from notes.cli import cli
from notes.systemd import SERVICE_NAME, TIMER_NAME
from tests.conftest import RecordedCommands, indexed_paths

SYSTEMCTL = ("systemctl", "--user")
NO_VAULT = "no vault at ~/.notes, run `notes init`"
BUS_ERROR = "Failed to connect to bus: No medium found"
NEXT_USEC = 1_788_382_800_000_000
NEXT_ELAPSE = datetime.fromtimestamp(NEXT_USEC / 1_000_000, tz=UTC).astimezone()
JOURNAL = (
    "2026-09-02T15:00:00+0300 host notes[1]: delivered notes/2026-09-02-x.md",
    "2026-09-02T15:01:00+0300 host notes[2]: nothing due",
)


def unit_paths(home: Path) -> tuple[Path, Path]:
    directory = home / ".config" / "systemd" / "user"
    return directory / SERVICE_NAME, directory / TIMER_NAME


def write_units(home: Path) -> tuple[Path, Path]:
    service, timer = unit_paths(home)
    service.parent.mkdir(parents=True, exist_ok=True)
    service.write_text(systemd.render_service((sys.executable, "-m", "notes")), encoding="utf-8")
    timer.write_text(systemd.render_timer(), encoding="utf-8")
    return service, timer


def script_status(
    recorded: RecordedCommands,
    *,
    enabled: str = "enabled",
    active: str = "active",
    next_usec: int | None = NEXT_USEC,
    journal: tuple[str, ...] = JOURNAL,
) -> None:
    ok = {"enabled": 0, "active": 0}
    recorded.script(*SYSTEMCTL, "is-enabled", returncode=ok.get(enabled, 1), stdout=f"{enabled}\n")
    recorded.script(*SYSTEMCTL, "is-active", returncode=ok.get(active, 3), stdout=f"{active}\n")
    entry = {"next": next_usec, "left": next_usec, "last": None, "passed": None, "unit": TIMER_NAME}
    recorded.script(*SYSTEMCTL, "list-timers", stdout=json.dumps([entry]))
    recorded.script("journalctl", stdout="".join(f"{line}\n" for line in journal))


@pytest.fixture
def no_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


# enable


def test_enable_writes_the_units_and_starts_the_timer(
    runner: CliRunner, vault: Path, home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    result = runner.invoke(cli, ["notifications", "enable"])

    assert result.exit_code == 0
    service, timer = unit_paths(home)
    command = f"{sys.executable} -m notes tick"
    assert result.output == f"wrote {service}\nwrote {timer}\nenabled {TIMER_NAME}: `{command}` runs every minute\n"
    assert f"ExecStart={systemd.quote(sys.executable)} -m notes tick\n" in service.read_text(encoding="utf-8")
    assert timer.read_text(encoding="utf-8") == systemd.render_timer()
    assert recorded_commands.commands() == [
        (*SYSTEMCTL, "import-environment", "DISPLAY"),
        (*SYSTEMCTL, "daemon-reload"),
        (*SYSTEMCTL, "enable", "--now", TIMER_NAME),
    ]


def test_enable_json(
    runner: CliRunner, vault: Path, home: Path, recorded_commands: RecordedCommands, no_display: None
) -> None:
    result = runner.invoke(cli, ["notifications", "enable", "--json"])

    assert result.exit_code == 0
    service, timer = unit_paths(home)
    assert json.loads(result.stdout) == {
        "enabled": True,
        "service": str(service),
        "timer": str(timer),
        "command": [sys.executable, "-m", "notes", "tick"],
        "imported_environment": [],
        "warnings": [],
    }
    assert result.stderr == ""
    assert recorded_commands.commands() == [(*SYSTEMCTL, "daemon-reload"), (*SYSTEMCTL, "enable", "--now", TIMER_NAME)]


def test_enable_prints_a_refused_import_environment_as_a_warning(
    runner: CliRunner, vault: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    recorded_commands.script(*SYSTEMCTL, "import-environment", returncode=1, stderr="Access denied\n")
    warning = "importing DISPLAY, WAYLAND_DISPLAY into the user manager failed: Access denied"

    human = runner.invoke(cli, ["notifications", "enable"])
    machine = runner.invoke(cli, ["notifications", "enable", "--json"])

    assert human.exit_code == 0
    assert human.stderr == f"warning: {warning}\n"
    assert f"enabled {TIMER_NAME}" in human.stdout
    assert machine.exit_code == 0
    assert machine.stderr == ""
    data = json.loads(machine.stdout)
    assert data["imported_environment"] == ["DISPLAY", "WAYLAND_DISPLAY"]
    assert data["warnings"] == [warning]


def test_enable_requires_a_vault(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands, no_display: None
) -> None:
    result = runner.invoke(cli, ["notifications", "enable"])

    assert result.exit_code == 1
    assert result.stderr == f"Error: {NO_VAULT}\n"
    assert recorded_commands.calls == []
    assert not any(path.exists() for path in unit_paths(home))


def test_enable_without_systemctl_is_an_error(
    runner: CliRunner, vault: Path, recorded_commands: RecordedCommands, no_display: None
) -> None:
    recorded_commands.missing("systemctl")

    result = runner.invoke(cli, ["notifications", "enable"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Error: systemctl is not installed or not on PATH; without systemd, run `notes tick` from cron every minute\n"
    )


def test_enable_refused_by_the_manager_is_a_json_error(
    runner: CliRunner, vault: Path, recorded_commands: RecordedCommands, no_display: None
) -> None:
    recorded_commands.script(*SYSTEMCTL, "enable", returncode=1, stderr=f"{BUS_ERROR}\n")

    result = runner.invoke(cli, ["notifications", "enable", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": {"type": "systemd_error", "message": f"enabling {TIMER_NAME} failed: {BUS_ERROR}"},
    }


# disable


def test_disable_stops_the_timer_and_removes_the_units(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands
) -> None:
    service, timer = write_units(home)

    result = runner.invoke(cli, ["notifications", "disable"])

    assert result.exit_code == 0
    assert result.output == f"disabled {TIMER_NAME}\nremoved {service}\nremoved {timer}\n"
    assert not service.exists()
    assert not timer.exists()
    assert recorded_commands.commands() == [(*SYSTEMCTL, "disable", "--now", TIMER_NAME), (*SYSTEMCTL, "daemon-reload")]


def test_disable_json(runner: CliRunner, home: Path, recorded_commands: RecordedCommands) -> None:
    service, timer = write_units(home)

    result = runner.invoke(cli, ["notifications", "disable", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"disabled": True, "removed": [str(service), str(timer)]}


def test_disable_when_nothing_is_installed(runner: CliRunner, home: Path, recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(
        *SYSTEMCTL, "disable", returncode=1, stderr=f"Failed to disable unit: Unit {TIMER_NAME} does not exist\n"
    )

    human = runner.invoke(cli, ["notifications", "disable"])
    machine = runner.invoke(cli, ["notifications", "disable", "--json"])

    assert human.exit_code == 0
    assert human.output == f"{TIMER_NAME} is not installed\n"
    assert machine.exit_code == 0
    assert json.loads(machine.stdout) == {"disabled": False, "removed": []}
    assert recorded_commands.commands() == [(*SYSTEMCTL, "disable", "--now", TIMER_NAME)] * 2


def test_disable_refused_while_installed_is_an_error(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands
) -> None:
    service, timer = write_units(home)
    recorded_commands.script(*SYSTEMCTL, "disable", returncode=1, stderr=f"{BUS_ERROR}\n")

    result = runner.invoke(cli, ["notifications", "disable"])

    assert result.exit_code == 1
    assert result.stderr == f"Error: disabling {TIMER_NAME} failed: {BUS_ERROR}\n"
    assert service.exists()
    assert timer.exists()


# status


def test_status_human_output(runner: CliRunner, home: Path, recorded_commands: RecordedCommands) -> None:
    write_units(home)
    script_status(recorded_commands)

    result = runner.invoke(cli, ["notifications", "status"])

    assert result.exit_code == 0
    assert result.output == (
        f"{TIMER_NAME}: enabled, active (unit files installed)\n"
        f"next run: {NEXT_ELAPSE.isoformat(sep=' ', timespec='seconds')}\n"
        f"recent {SERVICE_NAME} journal:\n"
        f"  {JOURNAL[0]}\n"
        f"  {JOURNAL[1]}\n"
    )
    assert recorded_commands.commands() == [
        (*SYSTEMCTL, "is-enabled", TIMER_NAME),
        (*SYSTEMCTL, "is-active", TIMER_NAME),
        (*SYSTEMCTL, "list-timers", "--all", "--output=json", TIMER_NAME),
        ("journalctl", "--user", "-u", SERVICE_NAME, "-n", "5", "--no-pager", "-q", "-o", "short-iso"),
    ]


def test_status_json(runner: CliRunner, home: Path, recorded_commands: RecordedCommands) -> None:
    write_units(home)
    script_status(recorded_commands)

    result = runner.invoke(cli, ["notifications", "status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "timer": TIMER_NAME,
        "service": SERVICE_NAME,
        "installed": True,
        "enabled": "enabled",
        "active": "active",
        "next_elapse": NEXT_ELAPSE.isoformat(timespec="seconds"),
        "journal": list(JOURNAL),
    }


def test_status_for_an_uninstalled_timer(runner: CliRunner, home: Path, recorded_commands: RecordedCommands) -> None:
    script_status(recorded_commands, enabled="not-found", active="inactive", next_usec=None, journal=())

    human = runner.invoke(cli, ["notifications", "status"])
    machine = runner.invoke(cli, ["notifications", "status", "--json"])

    assert human.exit_code == 0
    assert human.output == (
        f"{TIMER_NAME}: not-found, inactive (unit files not installed)\n"
        "next run: none scheduled\n"
        f"no {SERVICE_NAME} journal entries\n"
    )
    assert machine.exit_code == 0
    assert json.loads(machine.stdout) == {
        "timer": TIMER_NAME,
        "service": SERVICE_NAME,
        "installed": False,
        "enabled": "not-found",
        "active": "inactive",
        "next_elapse": None,
        "journal": [],
    }


def test_status_for_a_disabled_timer_with_units_installed(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands
) -> None:
    write_units(home)
    script_status(recorded_commands, enabled="disabled", active="inactive", next_usec=None, journal=())

    result = runner.invoke(cli, ["notifications", "status"])

    assert result.exit_code == 0
    assert result.output.startswith(
        f"{TIMER_NAME}: disabled, inactive (unit files installed)\nnext run: none scheduled\n"
    )


def test_status_reports_an_unreachable_user_manager(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands
) -> None:
    recorded_commands.script(*SYSTEMCTL, "is-enabled", returncode=1, stderr=f"{BUS_ERROR}\n")

    result = runner.invoke(cli, ["notifications", "status"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: querying the timer's enablement failed: {BUS_ERROR}\n"


def test_status_and_disable_work_without_a_vault(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands
) -> None:
    script_status(recorded_commands, enabled="not-found", active="inactive", next_usec=None, journal=())
    recorded_commands.script(*SYSTEMCTL, "disable", returncode=1, stderr="Failed to disable unit\n")

    assert runner.invoke(cli, ["notifications", "status"]).exit_code == 0
    assert runner.invoke(cli, ["notifications", "disable"]).exit_code == 0
    assert not (home / ".notes").exists()


# Shared behaviour


def test_notifications_never_sync_or_commit(
    runner: CliRunner, vault: Path, recorded_commands: RecordedCommands, git_cmd: Callable[..., str], no_display: None
) -> None:
    pending = vault / "notes" / "2026-09-02-pending.md"
    pending.write_text("---\nkind: decision\nstatus: active\n---\n\n# Pending\n", encoding="utf-8")
    script_status(recorded_commands)

    for arguments in (["enable"], ["status"], ["disable"]):
        assert runner.invoke(cli, ["notifications", *arguments]).exit_code == 0

    assert indexed_paths(vault) == []
    assert git_cmd(vault, "log", "--format=%s").splitlines() == ["notes: initialize vault"]
    assert all(command[0] in {"systemctl", "journalctl"} for command in recorded_commands.commands())


def test_notifications_help_lists_the_subcommands_without_touching_the_vault(runner: CliRunner, home: Path) -> None:
    group = runner.invoke(cli, ["notifications", "--help"])
    enable = runner.invoke(cli, ["notifications", "enable", "--help"])

    assert group.exit_code == 0
    for name in ("enable", "disable", "status"):
        assert f"  {name} " in group.output
    assert enable.exit_code == 0
    assert "--json" in enable.output
    assert not (home / ".notes").exists()


def test_notifications_requires_a_subcommand(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["notifications"])

    assert result.exit_code == 2
    assert result.stderr.startswith("Usage: notes notifications [OPTIONS] COMMAND [ARGS]...")


def test_notifications_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert "  notifications " in result.output
