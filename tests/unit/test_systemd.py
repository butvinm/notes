"""Tests for the systemd layer: unit rendering, the service command, and enable, disable, status through the seam."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from notes import systemd
from notes.errors import SystemdError
from notes.proc import CommandResult, CommandTimeout
from notes.systemd import SERVICE_NAME, TIMER_NAME, DisableOutcome, EnableOutcome, Status
from tests.conftest import RecordedCommands

SYSTEMCTL = ("systemctl", "--user")
JOURNALCTL = ("journalctl", "--user", "-u", SERVICE_NAME, "-n", "5", "--no-pager", "-q", "-o", "short-iso")
ENTRY_POINT = "/home/u/.local/bin/notes"
NEXT_USEC = 1_788_382_800_000_000
NEXT_ELAPSE = datetime.fromtimestamp(NEXT_USEC / 1_000_000, tz=UTC)
BUS_ERROR = "Failed to connect to bus: No medium found"


def unit_dir(home: Path) -> Path:
    return home / ".config" / "systemd" / "user"


def write_units(home: Path, *names: str) -> list[Path]:
    """Put rendered unit files into the user unit directory, both of them when no names are given."""
    directory = unit_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name in names or (SERVICE_NAME, TIMER_NAME):
        text = systemd.render_timer() if name == TIMER_NAME else systemd.render_service((ENTRY_POINT,))
        (directory / name).write_text(text, encoding="utf-8")
        written.append(directory / name)
    return written


def timers_json(*entries: dict[str, object]) -> str:
    return json.dumps(list(entries))


def timer_entry(next_usec: int | None, unit: str = TIMER_NAME) -> dict[str, object]:
    return {"next": next_usec, "left": next_usec, "last": None, "passed": None, "unit": unit, "activates": SERVICE_NAME}


# Rendering


def test_render_service_runs_the_entry_point_as_a_oneshot_without_a_working_directory() -> None:
    text = systemd.render_service((ENTRY_POINT,))

    assert text.startswith("[Unit]\nDescription=notes: ")
    assert f"\n[Service]\nType=oneshot\nExecStart={ENTRY_POINT} tick\n" in text
    assert "WorkingDirectory" not in text
    assert text.endswith("\n")


def test_render_service_for_the_module_form() -> None:
    text = systemd.render_service(("/usr/bin/python3", "-m", "notes"))

    assert "ExecStart=/usr/bin/python3 -m notes tick\n" in text


def test_render_service_quotes_a_path_with_spaces() -> None:
    text = systemd.render_service(("/home/my user/bin/notes",))

    assert 'ExecStart="/home/my user/bin/notes" tick\n' in text


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("/usr/bin/python3", "/usr/bin/python3"),
        ("-m", "-m"),
        ("notes", "notes"),
        ("/home/my user/notes", '"/home/my user/notes"'),
        ("/opt/100%/notes", '"/opt/100%%/notes"'),
        ("/opt/$HOME/notes", '"/opt/$$HOME/notes"'),
        ('/opt/say "hi"/notes', '"/opt/say \\"hi\\"/notes"'),
        ("/opt/back\\slash/notes", '"/opt/back\\\\slash/notes"'),
        ("/opt/заметки/notes", '"/opt/заметки/notes"'),
    ],
)
def test_quote_escapes_specifiers_variables_and_unusual_characters(argument: str, expected: str) -> None:
    assert systemd.quote(argument) == expected


def test_render_timer_runs_minutely_persistently_and_installs_into_timers_target() -> None:
    text = systemd.render_timer()

    assert text.startswith("[Unit]\nDescription=notes: ")
    assert "\n[Timer]\nOnCalendar=minutely\nPersistent=true\nAccuracySec=10s\n" in text
    assert text.endswith("\n[Install]\nWantedBy=timers.target\n")


# Locations


def test_unit_dir_follows_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert systemd.unit_dir() == tmp_path / "xdg" / "systemd" / "user"
    assert systemd.service_path() == tmp_path / "xdg" / "systemd" / "user" / SERVICE_NAME
    assert systemd.timer_path() == tmp_path / "xdg" / "systemd" / "user" / TIMER_NAME


def test_unit_dir_defaults_to_config_under_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME")

    assert systemd.unit_dir() == home / ".config" / "systemd" / "user"


def test_unit_dir_treats_an_empty_xdg_config_home_as_unset(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "")

    assert systemd.unit_dir() == home / ".config" / "systemd" / "user"


# The command the service runs


def test_exec_command_uses_the_running_notes_entry_point(tmp_path: Path) -> None:
    script = tmp_path / "bin" / "notes"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/python3\n", encoding="utf-8")

    assert systemd.exec_command(str(script), "/usr/bin/python3") == (str(script.resolve()),)


def test_exec_command_resolves_a_bare_entry_point_through_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "bin" / "notes"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/python3\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(script.parent))

    assert systemd.exec_command("notes", "/usr/bin/python3") == (str(script.resolve()),)


def test_exec_command_falls_back_to_the_module_form(tmp_path: Path) -> None:
    module_form = ("/usr/bin/python3", "-m", "notes")

    assert (
        systemd.exec_command("/usr/lib/python3.13/site-packages/pytest/__main__.py", "/usr/bin/python3") == module_form
    )
    assert systemd.exec_command(str(tmp_path / "notes"), "/usr/bin/python3") == module_form
    assert systemd.exec_command("", "/usr/bin/python3") == module_form


def test_exec_command_defaults_to_the_running_interpreter() -> None:
    assert systemd.exec_command() == (sys.executable, "-m", "notes")


# enable


def test_enable_writes_both_units_and_drives_systemctl_in_order(
    home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    outcome = systemd.enable((ENTRY_POINT,))

    service, timer = unit_dir(home) / SERVICE_NAME, unit_dir(home) / TIMER_NAME
    assert outcome == EnableOutcome(service, timer, (ENTRY_POINT,), ("DISPLAY",), ())
    assert service.read_text(encoding="utf-8") == systemd.render_service((ENTRY_POINT,))
    assert timer.read_text(encoding="utf-8") == systemd.render_timer()
    assert recorded_commands.commands() == [
        (*SYSTEMCTL, "import-environment", "DISPLAY"),
        (*SYSTEMCTL, "daemon-reload"),
        (*SYSTEMCTL, "enable", "--now", TIMER_NAME),
    ]
    assert all(call.timeout == systemd.SYSTEMCTL_TIMEOUT for call in recorded_commands.calls)


def test_enable_imports_every_display_variable_that_is_set(
    home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    outcome = systemd.enable((ENTRY_POINT,))

    assert outcome.imported == ("DISPLAY", "WAYLAND_DISPLAY")
    assert recorded_commands.commands(*SYSTEMCTL, "import-environment") == [
        (*SYSTEMCTL, "import-environment", "DISPLAY", "WAYLAND_DISPLAY")
    ]


def test_enable_skips_import_environment_when_no_display_variable_is_set(
    home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "")

    outcome = systemd.enable((ENTRY_POINT,))

    assert outcome.imported == ()
    assert recorded_commands.commands() == [(*SYSTEMCTL, "daemon-reload"), (*SYSTEMCTL, "enable", "--now", TIMER_NAME)]


def test_enable_treats_a_refused_import_environment_as_a_warning(
    home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    recorded_commands.script(
        *SYSTEMCTL, "import-environment", returncode=1, stderr="Failed to import environment: Access denied\n"
    )

    outcome = systemd.enable((ENTRY_POINT,))

    assert outcome.warnings == (
        "importing DISPLAY into the user manager failed: Failed to import environment: Access denied",
    )
    assert recorded_commands.commands()[1:] == [
        (*SYSTEMCTL, "daemon-reload"),
        (*SYSTEMCTL, "enable", "--now", TIMER_NAME),
    ]


def test_enable_fails_when_the_manager_refuses_the_timer(
    home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    recorded_commands.script(*SYSTEMCTL, "enable", returncode=1, stderr=f"{BUS_ERROR}\n")

    with pytest.raises(SystemdError, match=f"enabling {TIMER_NAME} failed: {BUS_ERROR}"):
        systemd.enable((ENTRY_POINT,))


def test_enable_fails_when_daemon_reload_fails(
    home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    recorded_commands.script(*SYSTEMCTL, "daemon-reload", returncode=1)

    with pytest.raises(SystemdError, match="systemctl daemon-reload failed: systemctl exited with status 1"):
        systemd.enable((ENTRY_POINT,))

    assert recorded_commands.commands() == [(*SYSTEMCTL, "daemon-reload")]


def test_enable_without_systemctl_is_an_error(
    home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    recorded_commands.missing("systemctl")

    with pytest.raises(SystemdError, match="systemctl is not installed or not on PATH") as info:
        systemd.enable((ENTRY_POINT,))

    assert info.value.type == "systemd_error"
    assert "run `notes tick` from cron" in info.value.message


def test_a_hanging_systemctl_is_an_error(
    home: Path, recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    def hang(command: tuple[str, ...]) -> CommandResult:
        raise CommandTimeout(command, systemd.SYSTEMCTL_TIMEOUT)

    recorded_commands.respond(*SYSTEMCTL, "daemon-reload", using=hang)

    with pytest.raises(SystemdError, match="systemctl daemon-reload timed out after 30 seconds"):
        systemd.enable((ENTRY_POINT,))


# disable


def test_disable_stops_the_timer_removes_the_units_and_reloads(home: Path, recorded_commands: RecordedCommands) -> None:
    service, timer = write_units(home)

    outcome = systemd.disable()

    assert outcome == DisableOutcome(True, (service, timer))
    assert outcome.was_installed
    assert not service.exists()
    assert not timer.exists()
    assert recorded_commands.commands() == [(*SYSTEMCTL, "disable", "--now", TIMER_NAME), (*SYSTEMCTL, "daemon-reload")]


def test_disable_when_nothing_is_installed_is_not_an_error(home: Path, recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(
        *SYSTEMCTL, "disable", returncode=1, stderr=f"Failed to disable unit: Unit {TIMER_NAME} does not exist\n"
    )

    outcome = systemd.disable()

    assert outcome == DisableOutcome(False, ())
    assert not outcome.was_installed
    assert recorded_commands.commands() == [(*SYSTEMCTL, "disable", "--now", TIMER_NAME)]


def test_disable_refused_while_units_exist_is_an_error(home: Path, recorded_commands: RecordedCommands) -> None:
    service, timer = write_units(home)
    recorded_commands.script(*SYSTEMCTL, "disable", returncode=1, stderr=f"{BUS_ERROR}\n")

    with pytest.raises(SystemdError, match=f"disabling {TIMER_NAME} failed: {BUS_ERROR}"):
        systemd.disable()

    assert service.exists()
    assert timer.exists()
    assert recorded_commands.commands() == [(*SYSTEMCTL, "disable", "--now", TIMER_NAME)]


def test_disable_removes_a_lone_unit_file(home: Path, recorded_commands: RecordedCommands) -> None:
    (timer,) = write_units(home, TIMER_NAME)

    outcome = systemd.disable()

    assert outcome == DisableOutcome(True, (timer,))
    assert not timer.exists()


# status


def test_status_composes_the_recorded_outputs(home: Path, recorded_commands: RecordedCommands) -> None:
    write_units(home)
    recorded_commands.script(*SYSTEMCTL, "is-enabled", stdout="enabled\n")
    recorded_commands.script(*SYSTEMCTL, "is-active", stdout="active\n")
    recorded_commands.script(*SYSTEMCTL, "list-timers", stdout=timers_json(timer_entry(NEXT_USEC)))
    recorded_commands.script(
        "journalctl",
        stdout="2026-09-02T15:00:00+0300 host notes[1]: delivered notes/2026-09-02-x.md\n"
        "2026-09-02T15:01:00+0300 host notes[2]: nothing due\n",
    )

    state = systemd.status()

    assert state == Status(
        installed=True,
        enabled="enabled",
        active="active",
        next_elapse=NEXT_ELAPSE,
        journal=(
            "2026-09-02T15:00:00+0300 host notes[1]: delivered notes/2026-09-02-x.md",
            "2026-09-02T15:01:00+0300 host notes[2]: nothing due",
        ),
    )
    assert state.next_elapse is not None
    assert state.next_elapse.tzinfo is not None
    assert recorded_commands.commands() == [
        (*SYSTEMCTL, "is-enabled", TIMER_NAME),
        (*SYSTEMCTL, "is-active", TIMER_NAME),
        (*SYSTEMCTL, "list-timers", "--all", "--output=json", TIMER_NAME),
        JOURNALCTL,
    ]


def test_status_for_a_timer_the_manager_does_not_know(home: Path, recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(*SYSTEMCTL, "is-enabled", returncode=4, stdout="not-found\n")
    recorded_commands.script(*SYSTEMCTL, "is-active", returncode=4, stdout="inactive\n")
    recorded_commands.script(*SYSTEMCTL, "list-timers", stdout="[]\n")

    assert systemd.status() == Status(False, "not-found", "inactive", None, ())


def test_status_with_only_one_unit_file_is_not_installed(home: Path, recorded_commands: RecordedCommands) -> None:
    write_units(home, SERVICE_NAME)
    recorded_commands.script(*SYSTEMCTL, "is-enabled", returncode=1, stdout="disabled\n")
    recorded_commands.script(*SYSTEMCTL, "is-active", returncode=3, stdout="inactive\n")
    recorded_commands.script(*SYSTEMCTL, "list-timers", stdout=timers_json(timer_entry(None)))

    assert systemd.status() == Status(False, "disabled", "inactive", None, ())


def test_status_ignores_other_timers_and_a_missing_next_elapse(home: Path, recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(*SYSTEMCTL, "is-enabled", stdout="enabled\n")
    recorded_commands.script(*SYSTEMCTL, "is-active", stdout="active\n")
    recorded_commands.script(
        *SYSTEMCTL, "list-timers", stdout=timers_json(timer_entry(5, unit="other.timer"), timer_entry(None))
    )

    assert systemd.status().next_elapse is None


def test_status_tolerates_unparseable_list_timers_and_a_missing_journalctl(
    home: Path, recorded_commands: RecordedCommands
) -> None:
    recorded_commands.script(*SYSTEMCTL, "is-enabled", stdout="enabled\n")
    recorded_commands.script(*SYSTEMCTL, "is-active", stdout="active\n")
    recorded_commands.script(*SYSTEMCTL, "list-timers", stdout="NEXT LEFT LAST PASSED UNIT ACTIVATES\n")
    recorded_commands.missing("journalctl")

    assert systemd.status() == Status(False, "enabled", "active", None, ())


def test_status_tolerates_a_failing_list_timers_and_journalctl(home: Path, recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(*SYSTEMCTL, "is-enabled", stdout="enabled\n")
    recorded_commands.script(*SYSTEMCTL, "is-active", stdout="active\n")
    recorded_commands.script(*SYSTEMCTL, "list-timers", returncode=1, stderr="oops\n")
    recorded_commands.script("journalctl", returncode=1, stderr="No journal files were found.\n")

    assert systemd.status() == Status(False, "enabled", "active", None, ())


def test_status_reports_an_unreachable_user_manager(home: Path, recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(*SYSTEMCTL, "is-enabled", returncode=1, stderr=f"{BUS_ERROR}\n")

    with pytest.raises(SystemdError, match=f"querying the timer's enablement failed: {BUS_ERROR}"):
        systemd.status()

    assert recorded_commands.commands() == [(*SYSTEMCTL, "is-enabled", TIMER_NAME)]


def test_status_reports_a_failing_is_active_without_output(home: Path, recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(*SYSTEMCTL, "is-enabled", stdout="enabled\n")
    recorded_commands.script(*SYSTEMCTL, "is-active", returncode=1)

    with pytest.raises(SystemdError, match="querying the timer's activity failed: systemctl exited with status 1"):
        systemd.status()


def test_status_without_systemctl_is_an_error(home: Path, recorded_commands: RecordedCommands) -> None:
    recorded_commands.missing("systemctl")

    with pytest.raises(SystemdError, match="systemctl is not installed or not on PATH"):
        systemd.status()
