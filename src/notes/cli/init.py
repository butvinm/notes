"""`notes init`: create the vault at `~/.notes`, or clone it from a remote that already holds one.

A plain command: there is no vault to sync yet. In a terminal it asks for the remote and offers the timer when the
options were not given; a non-interactive run (no terminal, or `--json`) never prompts.
"""

from typing import Any

import click

from notes import init as init_flow
from notes.cli import interactive, json_option
from notes.init import InitOutcome
from notes.output import emit, json_enabled
from notes.systemd import TIMER_NAME
from notes.vault import root


@click.command("init", short_help="Create the vault, or clone it from a remote")
@click.option("--remote", metavar="URL", help="Git remote to push to, or to clone when it already holds a vault.")
@click.option(
    "--auto-push",
    is_flag=True,
    help="Push after every change (recorded in config.toml); with --remote, also push the initial commit.",
)
@click.option(
    "--enable-notifications",
    is_flag=True,
    help="Enable the systemd user timer that runs `notes tick` every minute.",
)
@json_option
@click.pass_context
def init_command(ctx: click.Context, remote: str | None, auto_push: bool, enable_notifications: bool) -> None:
    """Create the vault at ~/.notes, or clone it from --remote when the remote already holds one."""
    prompts = interactive() and not json_enabled(ctx)
    if prompts:
        init_flow.check_location(root())
        if remote is None:
            answer = click.prompt("Git remote URL (leave empty for a local-only vault)", default="", show_default=False)
            remote = answer.strip() or None
        if not enable_notifications:
            enable_notifications = click.confirm(
                "Enable desktop notifications through a systemd user timer?", default=False
            )
    outcome = init_flow.run(remote=remote, auto_push=auto_push, enable_notifications=enable_notifications)
    if not json_enabled(ctx):
        for warning in outcome.warnings:
            click.echo(f"warning: {warning}", err=True)
    emit(ctx, render(outcome), as_data(outcome))


def render(outcome: InitOutcome) -> str:
    """The human report: where the vault came from, the remote, the push, and the timer."""
    if outcome.cloned:
        count = outcome.report.note_count
        lines = [
            f"cloned vault from {outcome.remote} into {outcome.vault}",
            f"indexed {count} {'note' if count == 1 else 'notes'}",
        ]
    else:
        lines = [f"initialized vault at {outcome.vault}"]
        if outcome.remote is not None:
            lines.append(f"remote {outcome.remote_name}: {outcome.remote}")
        if outcome.pushed:
            lines.append(f"pushed to {outcome.remote_name} and set it as the upstream")
    if outcome.notifications is not None:
        command = " ".join((*outcome.notifications.command, "tick"))
        lines.append(f"enabled {TIMER_NAME}: `{command}` runs every minute")
    return "\n".join(lines)


def as_data(outcome: InitOutcome) -> dict[str, Any]:
    """The JSON shape of `notes init --json`."""
    return {
        "vault": str(outcome.vault),
        "cloned": outcome.cloned,
        "remote": outcome.remote,
        "pushed": outcome.pushed,
        "notes_indexed": outcome.report.note_count,
        "invalid_files": len(outcome.report.invalid),
        "notifications_enabled": outcome.notifications is not None,
        "warnings": list(outcome.warnings),
    }
