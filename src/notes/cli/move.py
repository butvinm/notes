"""`notes move <id> <new-path>`: rename a note and every reference to it, then index and commit the move.

The new path must lie under `notes/`, be named `YYYY-MM-DD-<slug>.md`, and not exist yet. The file is renamed with
`git mv` when Git tracks it and with a plain rename otherwise; the untracked case is the invalid note the
auto-commit step never staged, and repairing a badly named file is what `notes move` is for. The moved note's own
`note:` references are expressed again relative to its new directory, every indexed note pointing at the old path
is re-pointed at the new one, the note's deliveries follow it, and the second sync commits all of it as one
`notes: move <old> -> <new>`. A note that is still invalid after the move is reported the way `notes edit` reports
one and stays uncommitted.
"""

from pathlib import Path
from typing import Any

import click

from notes import deliveries, git, queries, relations
from notes.cli import get_session, vault_command
from notes.cli.edit import as_data, fail_invalid, invalid_of, note_title, render
from notes.errors import UsageError
from notes.output import emit
from notes.slug import creation_date_from_path
from notes.vault import canonical_id, resolve_id


@vault_command("move")
@click.argument("note_id", metavar="ID")
@click.argument("new_path", metavar="NEW-PATH")
@click.pass_context
def move_command(ctx: click.Context, note_id: str, new_path: str) -> None:
    """Rename a note to NEW-PATH, rewriting the references to it in other notes, then index and commit the move.

    NEW-PATH is a path under notes/ named YYYY-MM-DD-<slug>.md; the same spellings as for ID are accepted.
    """
    session = get_session(ctx)
    vault = session.vault
    old = resolve_id(vault, note_id)
    new = destination(vault, old, new_path)
    referrers = queries.referrers_of(session.conn, old)
    rename(vault, old, new)
    # Straight after the rename, before the reference rewriting that can take many files and an interruption with
    # them: a delivery left behind under the old path would never be read again, because recall and list join
    # deliveries to the indexed notes.
    deliveries.move(session.conn, old, new)
    updated = [path for path in relations.retarget(vault, old, new, referrers) if path != new]
    report = session.sync(message=git.move_message(old, new))
    invalid = invalid_of(report, new)
    title = note_title(vault, new)
    lines = [render(vault, new, title)]
    lines.extend(f"updated: {render(vault, path, note_title(vault, path))}" for path in updated)
    data: dict[str, Any] = as_data(vault, new, title, invalid) | {
        "old_path": old,
        "updated": [
            {"path": path, "abs_path": str(vault / path), "title": note_title(vault, path)} for path in updated
        ],
    }
    emit(ctx, "\n".join(lines), data)
    if invalid is not None:
        fail_invalid(ctx, vault, invalid)


def destination(vault: Path, old: str, text: str) -> str:
    """The canonical ID of the new path once every check passed: under `notes/`, ending in `.md`, dated, not the
    note's current path, and not taken by any file or directory."""
    new = canonical_id(vault, text)
    if not new.endswith(".md"):
        raise UsageError(f"{new}: a note file must end with .md")
    try:
        creation_date_from_path(new)
    except ValueError as error:
        raise UsageError(str(error)) from None
    if new == old:
        raise UsageError(f"{old} is already the note's path")
    if (vault / new).exists():
        raise UsageError(f"{new} already exists")
    return new


def rename(vault: Path, old: str, new: str) -> None:
    """Move the file: `git mv` when Git tracks it, a plain rename otherwise; the new directory is created first."""
    target = vault / new
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise UsageError(f"cannot create {target.parent}: {error.strerror or error}") from None
    if git.is_repo(vault) and git.is_tracked(vault, old):
        git.mv(vault, old, new)
    else:
        (vault / old).rename(target)
