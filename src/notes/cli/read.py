"""`notes read <id>`: close every unread delivery of a note, the explicit acknowledgement of a reminder.

Showing a note's title in a desktop notification, in `notes list`, in `notes show`, or in the recall block never
marks it read; only this command does, by stamping `read_at` on the note's unread `deliveries` rows. A note with
nothing unread reads fine and reports zero. The command is index-only: read state is device-local and lives only in
SQLite, so the pre-command sync indexes what changed but never commits or pushes. The next occurrence of the
schedule creates a new unread delivery, so a recurring reminder becomes unread again after every period.
"""

from pathlib import Path
from typing import Any

import click

from notes import clock, deliveries
from notes import schedule as schedules
from notes.cli import get_session, vault_command
from notes.cli.edit import note_title
from notes.cli.edit import render as render_note
from notes.output import emit
from notes.vault import resolve_id


@vault_command("read", index_only=True, short_help="Mark a note read")
@click.argument("note_id", metavar="ID")
@click.pass_context
def read_command(ctx: click.Context, note_id: str) -> None:
    """Mark a note read: close its unread deliveries and report how many were closed."""
    session = get_session(ctx)
    path = resolve_id(session.vault, note_id)
    now = clock.now()
    closed = deliveries.mark_read(session.conn, path, now)
    title = note_title(session.vault, path)
    read_at = schedules.format_timestamp(now) if closed else None
    emit(ctx, render(session.vault, path, title, closed), as_data(session.vault, path, title, closed, read_at))


def render(vault: Path, path: str, title: str | None, closed: int) -> str:
    """`read: [id](abs) - Title (closed 2 unread deliveries)`, or `(no unread deliveries)` when nothing was open."""
    return f"read: {render_note(vault, path, title)} ({closed_phrase(closed)})"


def closed_phrase(closed: int) -> str:
    if closed == 0:
        return "no unread deliveries"
    noun = "delivery" if closed == 1 else "deliveries"
    return f"closed {closed} unread {noun}"


def as_data(vault: Path, path: str, title: str | None, closed: int, read_at: str | None) -> dict[str, Any]:
    """The JSON shape: the ID, its absolute path, the title, how many deliveries were closed, and the moment stamped
    on them (null when none was closed)."""
    return {"path": path, "abs_path": str(vault / path), "title": title, "closed": closed, "read_at": read_at}
