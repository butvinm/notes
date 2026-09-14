"""`notes notifications enable|disable|status`: the systemd user timer that runs `notes tick` every minute.

These commands manage the timer from the outside: no sync, no index, no commit. `enable` needs the vault to
exist, because a timer without a vault would fail on every run; `disable` and `status` work regardless.
"""

from typing import Any

import click

from notes import systemd
from notes.cli import SectionedGroup, json_option
from notes.output import emit, json_enabled
from notes.systemd import SERVICE_NAME, TIMER_NAME
from notes.vault import require_vault


@click.group("notifications", cls=SectionedGroup, short_help="Manage the timer that delivers reminders")
def notifications_group() -> None:
    """Manage the systemd user timer that runs `notes tick` every minute and delivers due notes."""


@notifications_group.command("enable", short_help="Install and start the timer")
@json_option
@click.pass_context
def enable_command(ctx: click.Context) -> None:
    """Install and start the timer: write the user units, import the display variables, enable it now."""
    require_vault()
    outcome = systemd.enable(systemd.exec_command())
    if not json_enabled(ctx):
        for warning in outcome.warnings:
            click.echo(f"warning: {warning}", err=True)
    command = [*outcome.command, "tick"]
    lines = [
        f"wrote {outcome.service}",
        f"wrote {outcome.timer}",
        f"enabled {TIMER_NAME}: `{' '.join(command)}` runs every minute",
    ]
    data = {
        "enabled": True,
        "service": str(outcome.service),
        "timer": str(outcome.timer),
        "command": command,
        "imported_environment": list(outcome.imported),
        "warnings": list(outcome.warnings),
    }
    emit(ctx, "\n".join(lines), data)


@notifications_group.command("disable", short_help="Stop and remove the timer")
@json_option
@click.pass_context
def disable_command(ctx: click.Context) -> None:
    """Stop the timer and remove its unit files; running it when nothing is installed is not an error."""
    outcome = systemd.disable()
    if outcome.was_installed:
        lines = [f"disabled {TIMER_NAME}", *(f"removed {path}" for path in outcome.removed)]
    else:
        lines = [f"{TIMER_NAME} is not installed"]
    data = {"disabled": outcome.was_installed, "removed": [str(path) for path in outcome.removed]}
    emit(ctx, "\n".join(lines), data)


@notifications_group.command("status", short_help="Report the timer state and recent journal")
@json_option
@click.pass_context
def status_command(ctx: click.Context) -> None:
    """Report whether the timer is installed, enabled, and active, its next run, and the recent service journal."""
    state = systemd.status()
    installed = "unit files installed" if state.installed else "unit files not installed"
    lines = [f"{TIMER_NAME}: {state.enabled}, {state.active} ({installed})"]
    if state.next_elapse is not None:
        lines.append(f"next run: {state.next_elapse.isoformat(sep=' ', timespec='seconds')}")
    else:
        lines.append("next run: none scheduled")
    if state.journal:
        lines.append(f"recent {SERVICE_NAME} journal:")
        lines.extend(f"  {line}" for line in state.journal)
    else:
        lines.append(f"no {SERVICE_NAME} journal entries")
    data: dict[str, Any] = {
        "timer": TIMER_NAME,
        "service": SERVICE_NAME,
        "installed": state.installed,
        "enabled": state.enabled,
        "active": state.active,
        "next_elapse": state.next_elapse.isoformat(timespec="seconds") if state.next_elapse else None,
        "journal": list(state.journal),
    }
    emit(ctx, "\n".join(lines), data)
