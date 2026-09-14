"""`notes new <kind> [<title>] [--name]`: scaffold a note from the kind template, open it in the editor, commit it.

In a terminal the command asks for what the arguments left open (the title, the short name behind the filename,
the schedule of a reminder) and opens the editor. Outside a terminal, or under `--json`, it takes the
title from the argument, the suggested short name silently, no schedule, and skips the editor, so a script gets the
bare scaffold. From there on it is `notes edit`'s flow: normalize a relative schedule, sync, commit, report.
"""

from datetime import date, datetime
from pathlib import Path

import click

from notes import clock, deliveries, document, editor
from notes import schedule as schedules
from notes.cli import get_session, interactive, vault_command
from notes.cli.edit import finish
from notes.errors import UsageError
from notes.output import json_enabled
from notes.prompts import require_kind
from notes.slug import create_dated_note, suggest_short_name
from notes.vault import kind_template_path

SCHEDULE_EXAMPLES = "`in 3 days`, `tomorrow 09:00`, `2026-10-01 18:00`, `every 2 weeks`"


@vault_command("new")
@click.argument("kind")
@click.argument("title", required=False)
@click.option(
    "--name",
    "short_name",
    metavar="SHORT-NAME",
    help="The short name behind the filename, instead of the suggestion made from the title.",
)
@click.pass_context
def new_command(ctx: click.Context, kind: str, title: str | None, short_name: str | None) -> None:
    """Create a note of KIND from its template and open it in $VISUAL or $EDITOR."""
    session = get_session(ctx)
    require_kind(session.vault, kind)
    with_editor = interactive()
    prompts = with_editor and not json_enabled(ctx)
    if with_editor:
        editor.require_editor()
    now = clock.now()
    title = ask_title(title, prompts)
    short_name = ask_short_name(title, short_name, prompts)
    schedule = ask_schedule(kind, now, prompts)
    path = create(session.vault, kind, title, short_name, schedule, now.date())
    # The name was claimed exclusively, so a delivery row under it is a leftover of a note deleted by hand and
    # would hand this note the old one's unread state.
    deliveries.clear(session.conn, path)
    finish(ctx, session, path, open_editor=with_editor)


def ask_title(given: str | None, prompts: bool) -> str:
    """The title from the argument or, in a terminal, from a prompt; whitespace is collapsed to one line."""
    if given is None:
        if not prompts:
            raise UsageError(
                "a title is required: notes new <kind> <title> (prompts are shown only in a terminal without --json)"
            )
        given = click.prompt("Title")
    title = " ".join(given.split())
    if not title:
        raise UsageError("the title must not be empty")
    return title


def ask_short_name(title: str, given: str | None, prompts: bool) -> str:
    """`--name`, or the suggestion made from the title: offered for editing in a terminal, taken silently otherwise."""
    if given is not None:
        return given
    suggestion = suggest_short_name(title)
    if prompts:
        return click.prompt("Short name for the filename", default=suggestion)
    return suggestion


def ask_schedule(kind: str, now: datetime, prompts: bool) -> str | None:
    """In a terminal, the schedule of a kind that requires one, asked until the answer is acceptable; else None."""
    if kind not in document.SCHEDULE_REQUIRED_KINDS or not prompts:
        return None

    def normalize(text: str) -> str:
        try:
            canonical = schedules.normalize_input(text, now)
        except ValueError as error:
            raise click.UsageError(str(error)) from None
        problem = document.schedule_problem(canonical)
        if problem is not None:
            raise click.UsageError(problem)
        return canonical

    return click.prompt(f"Schedule ({SCHEDULE_EXAMPLES})", value_proc=normalize)


def create(vault: Path, kind: str, title: str, short_name: str, schedule: str | None, created: date) -> str:
    """Write the note rendered from the kind template under a dated, collision-free filename and return its ID.

    The file is created exclusively, so a second `notes new` running at the same moment with the same short name
    lands on the next suffix instead of writing over this note.
    """
    template = kind_template_path(vault, kind).read_text(encoding="utf-8")
    content = document.render_template(template, title, schedule).encode("utf-8")
    try:
        return create_dated_note(vault, created, short_name, content)
    except ValueError as error:
        raise UsageError(f"cannot build a filename: {error}") from None
