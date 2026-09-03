"""`notes show <id>`: the note file verbatim, or under `--json` its metadata, effective status, unread count, and body.

The human output is the file's bytes exactly as they are on disk, so it works for any file under `notes/`, valid or
not. The JSON comes from the index and therefore exists only for a valid note; for an invalid file the command fails
and points at `notes check`.
"""

import click

from notes import queries
from notes.cli import get_session, vault_command
from notes.errors import ValidationFailed
from notes.output import json_enabled, to_json
from notes.vault import resolve_id


@vault_command("show")
@click.argument("note_id", metavar="ID")
@click.pass_context
def show_command(ctx: click.Context, note_id: str) -> None:
    """Print a note as it is on disk; with --json, its metadata, effective status, unread count, and body."""
    session = get_session(ctx)
    path = resolve_id(session.vault, note_id)
    if not json_enabled(ctx):
        click.echo((session.vault / path).read_bytes(), nl=False)
        return
    row = queries.get_note(session.conn, path)
    if row is None:
        raise ValidationFailed(f"{path} is not a valid note, so it has no metadata; run `notes check`")
    data = queries.as_data(session.vault, row)
    data["related"] = [
        {"relation": item.relation, "note": item.target} for item in queries.relations_of(session.conn, path)
    ]
    data["body"] = row.body
    click.echo(to_json(data))
