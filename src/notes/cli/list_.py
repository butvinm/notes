"""`notes list`: the indexed notes, unread first and newest first, narrowed by kind, effective status, tag, or path.

The module is `list_` because `list` is a builtin; the command is `notes list`. Every filter is optional and the given
ones combine with AND. The pre-command sync has already run, so a note written by hand a moment ago is listed too.
"""

from collections.abc import Iterable
from pathlib import Path

import click

from notes import queries
from notes.cli import get_session, vault_command
from notes.output import emit, render_link, style_kind, style_status, style_unread
from notes.prompts import require_kind
from notes.queries import EFFECTIVE_STATUSES, NoteRow

UNREAD_MARKER = "[unread]"


@vault_command("list", short_help="List notes, with filters")
@click.option("--unread", is_flag=True, help="Only notes with a delivery that has not been read.")
@click.option("--kind", metavar="KIND", help="Only notes of this kind.")
@click.option(
    "--status",
    type=click.Choice(EFFECTIVE_STATUSES),
    help="Only notes with this effective status (`superseded` is derived from relations).",
)
@click.option("--tag", metavar="TAG", help="Only notes carrying this tag, matched case-insensitively.")
@click.option(
    "--path",
    metavar="PATH",
    help="Only notes with a `paths` entry equal to PATH or containing it (`~` expanded, relative to the cwd).",
)
@click.pass_context
def list_command(
    ctx: click.Context, unread: bool, kind: str | None, status: str | None, tag: str | None, path: str | None
) -> None:
    """List notes, unread first and newest first; filters combine with AND."""
    session = get_session(ctx)
    if kind is not None:
        require_kind(session.vault, kind)
    rows = queries.list_notes(session.conn, unread=unread, kind=kind, status=status, tag=tag, path=path)
    emit(ctx, render(session.vault, rows), [queries.as_data(session.vault, row) for row in rows])


def render(vault: Path, rows: Iterable[NoteRow]) -> str:
    """One line per note: `[unread] <kind> <effective status> [id](abs) - Title`, as aligned columns.

    The kind and status columns are padded to the widest value in the listing. The `[unread]` column exists only when
    some note is unread; the read notes then carry blanks in its place, so the links start at the same column.
    """
    rows = list(rows)
    marker_width = len(UNREAD_MARKER) if any(row.unread for row in rows) else 0
    kind_width = max((len(row.kind) for row in rows), default=0)
    status_width = max((len(row.effective_status) for row in rows), default=0)
    return "\n".join(render_line(vault, row, marker_width, kind_width, status_width) for row in rows)


def render_line(vault: Path, row: NoteRow, marker_width: int = 0, kind_width: int = 0, status_width: int = 0) -> str:
    """One listing line; the widths pad each column, and a zero marker width leaves the `[unread]` column out."""
    columns: list[str] = []
    if marker_width:
        columns.append(style_unread(UNREAD_MARKER) if row.unread else " " * marker_width)
    columns.append(style_kind(row.kind.ljust(kind_width)))
    columns.append(style_status(row.effective_status.ljust(status_width)))
    columns.append(render_link(vault, row.path, row.title))
    return " ".join(columns)
