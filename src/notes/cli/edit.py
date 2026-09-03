"""`notes edit <id>`: open a note in the editor, normalize a relative schedule, then index and commit the result.

The editor opens only in a terminal (stdin and stdout both); elsewhere the step is skipped and the command validates
and commits whatever the file holds, which is how a script or an agent checks in a note it wrote by itself.
The tail of the flow, `finish`, is shared with `notes new`: normalize the schedule, sync (which validates and
commits), print the note as a link, and when the file is not a valid note, print its errors with line numbers and
exit 1; the file then stays on disk, unindexed and uncommitted, until it is fixed.
"""

from pathlib import Path
from typing import Any

import click

from notes import clock, document, editor
from notes.cli import Session, get_session, interactive, vault_command
from notes.output import emit, json_enabled, render_link
from notes.sync import InvalidFile, SyncReport, error_lines
from notes.vault import resolve_id


@vault_command("edit")
@click.argument("note_id", metavar="ID")
@click.pass_context
def edit_command(ctx: click.Context, note_id: str) -> None:
    """Open a note in $VISUAL or $EDITOR, then index and commit it."""
    session = get_session(ctx)
    path = resolve_id(session.vault, note_id)
    with_editor = interactive()
    if with_editor:
        editor.require_editor()
    finish(ctx, session, path, open_editor=with_editor)


def finish(ctx: click.Context, session: Session, path: str, *, open_editor: bool) -> None:
    """Edit when asked, normalize the schedule, sync with commit, and report the note; exit 1 when it is invalid."""
    absolute = session.vault / path
    if open_editor:
        editor.open_in_editor(absolute)
    document.normalize_schedule(absolute, clock.now())
    report = session.sync()
    invalid = invalid_of(report, path)
    title = note_title(session.vault, path)
    emit(ctx, render(session.vault, path, title), as_data(session.vault, path, title, invalid))
    if invalid is not None:
        fail_invalid(ctx, session.vault, invalid)


def invalid_of(report: SyncReport, path: str) -> InvalidFile | None:
    """The report's entry for `path`, when the sync found the file invalid."""
    return next((item for item in report.invalid if item.path == path), None)


def fail_invalid(ctx: click.Context, vault: Path, invalid: InvalidFile) -> None:
    """Exit 1 for a note left invalid, after printing its errors with line numbers in human mode (the JSON printed
    before already carries them)."""
    if not json_enabled(ctx):
        for line in error_lines(vault, [invalid]):
            click.echo(line, err=True)
        message = f"Error: {invalid.path} is not a valid note and stays uncommitted; fix it and run `notes sync`"
        click.echo(message, err=True)
    ctx.exit(1)


def render(vault: Path, path: str, title: str | None) -> str:
    """The note as a link with its title, or the bare link when the file has no H1."""
    return render_link(vault, path, title) if title else f"[{path}]({vault / path})"


def as_data(vault: Path, path: str, title: str | None, invalid: InvalidFile | None) -> dict[str, Any]:
    """The JSON shape of `notes new` and `notes edit`: the ID, its absolute path, the title, and the validation."""
    errors = [] if invalid is None else [{"line": error.line, "message": error.message} for error in invalid.errors]
    return {"path": path, "abs_path": str(vault / path), "title": title, "valid": invalid is None, "errors": errors}


def note_title(vault: Path, path: str) -> str | None:
    """The H1 of the note at the vault-relative `path`, read from disk so a file the index does not hold works too."""
    try:
        return document.load(vault, path).title
    except (OSError, UnicodeDecodeError):
        return None
