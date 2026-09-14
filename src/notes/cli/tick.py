"""`notes tick [--now <timestamp>]`: deliver the scheduled notes that are due, the minutely job of the systemd timer.

The command is index-only: its pre-command sync indexes what changed but never commits or pushes, because a job
that runs every minute must not touch the repository or the network. Every due note gets one delivery row, one
desktop notification, and one sound when the sound is on. A failure to reach the desktop is a warning on stderr
and the delivery stays unread, so `notes list --unread` and `notes recall` still surface the note; the exit code
stays 0. Nothing due prints nothing. `--now` is hidden: it fixes the clock for tests and for replaying a moment
by hand, and it must carry a UTC offset like every timestamp of a schedule.
"""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from notes import clock, deliveries
from notes import schedule as schedules
from notes.cli import get_session, vault_command
from notes.deliveries import Delivered
from notes.output import emit, render_link, style_label


def _parse_now(_ctx: click.Context, _param: click.Parameter, value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return schedules.parse_timestamp(value)
    except ValueError as error:
        raise click.BadParameter(str(error)) from None


@vault_command("tick", index_only=True, short_help="Deliver the notes that are due")
@click.option(
    "--now",
    "now",
    metavar="TIMESTAMP",
    hidden=True,
    callback=_parse_now,
    help="Run as if it were this moment (ISO 8601 with a UTC offset) instead of the current time.",
)
@click.pass_context
def tick_command(ctx: click.Context, now: datetime | None) -> None:
    """Deliver the notes that are due: record each once, notify the desktop, play the sound."""
    session = get_session(ctx)
    delivered = deliveries.tick(session.conn, session.config.notifications, now or clock.now())
    for item in delivered:
        for warning in item.warnings:
            click.echo(f"warning: {warning}", err=True)
    emit(ctx, render(session.vault, delivered), [as_data(session.vault, item) for item in delivered])


def render(vault: Path, delivered: Iterable[Delivered]) -> str:
    """One line per delivery: `delivered: [id](abs) - Title (due <occurrence>)`; empty when nothing was due."""
    return "\n".join(render_line(vault, item) for item in delivered)


def render_line(vault: Path, item: Delivered) -> str:
    return f"{style_label('delivered:')} {render_link(vault, item.path, item.title)} (due {item.occurrence_at})"


def as_data(vault: Path, item: Delivered) -> dict[str, Any]:
    """The JSON shape of one delivery: `path`, `abs_path`, `title`, `occurrence_at`, `delivered_at`, `notified`."""
    return {
        "path": item.path,
        "abs_path": str(vault / item.path),
        "title": item.title,
        "occurrence_at": item.occurrence_at,
        "delivered_at": item.delivered_at,
        "notified": item.notified.ok,
    }
