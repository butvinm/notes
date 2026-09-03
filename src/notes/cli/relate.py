"""`notes relate <id> <relation> <other-id>`: write a relation into a note's frontmatter, then index and commit it.

The relation goes into the frontmatter of the first note as a `related` entry pointing at the second, so `supersedes`
gives the second note the effective status `superseded` without touching its file. The first note must be a valid
note already: a file that fails validation could not be indexed or committed after the change, so `relate` refuses
to edit it and points at `notes check` instead. The second note only has to exist, which is all validation asks of a
relation target.
"""

from typing import Any

import click

from notes import relations
from notes.cli import get_session, vault_command
from notes.cli.edit import note_title, render
from notes.errors import ValidationFailed
from notes.output import emit
from notes.vault import resolve_id


@vault_command("relate")
@click.argument("note_id", metavar="ID")
@click.argument("relation", metavar="RELATION")
@click.argument("other_id", metavar="OTHER-ID")
@click.pass_context
def relate_command(ctx: click.Context, note_id: str, relation: str, other_id: str) -> None:
    """Add a relation (supersedes, child, or related) from one note to another.

    The relation is written into the frontmatter of ID, which is then indexed and committed.
    """
    session = get_session(ctx)
    vault = session.vault
    src = resolve_id(vault, note_id)
    dst = resolve_id(vault, other_id)
    if any(item.path == src for item in session.report.invalid):
        raise ValidationFailed(f"{src} is not a valid note, so no relation was added; run `notes check`")
    reference = relations.add_relation(vault, src, relation, dst)
    session.sync(message=f"notes: update {src}")
    source_title, target_title = note_title(vault, src), note_title(vault, dst)
    human = f"{relation}: {render(vault, src, source_title)} -> {render(vault, dst, target_title)}"
    data: dict[str, Any] = {
        "path": src,
        "abs_path": str(vault / src),
        "title": source_title,
        "relation": relation,
        "target": {"path": dst, "abs_path": str(vault / dst), "title": target_title},
        "reference": reference,
    }
    emit(ctx, human, data)
