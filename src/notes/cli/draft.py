"""`notes draft create|list|show|revise|save|discard`: agent-assisted drafting, with confirmation as the trust boundary.

`create` reads a JSON context packet on stdin, composes the vault-owned prompts around it, runs the configured
generator, and stores the envelope it returns as a draft under `drafts/`; a failing generator leaves no draft.
`revise` runs the generator again with the previous Markdown and the feedback. `save` is the only way a draft
becomes a note: the schedule is normalized, a missing `kind` filled in, the Markdown validated exactly as sync
validates a file, and only then is the note written under `notes/`, indexed, committed, and the draft deleted.
A draft that fails validation is kept, with its errors reported by line. `discard` deletes a draft as it is.

Every subcommand is a vault command: the pre-command sync runs as for any other command, and drafts themselves
are never indexed, searched, recalled, or committed.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

import click
from ruamel.yaml.comments import CommentedMap

from notes import clock, deliveries, document, drafts, generator
from notes.cli import SectionedGroup, get_session, vault_command
from notes.cli.edit import as_data, fail_invalid, invalid_of, note_title, render
from notes.document import ValidationError
from notes.drafts import Draft
from notes.errors import UsageError, ValidationFailed
from notes.output import emit, json_enabled, render_ref
from notes.prompts import require_kind
from notes.slug import create_dated_note, dated_filename
from notes.sync import InvalidFile, error_lines
from notes.vault import known_kinds


@click.group("draft", cls=SectionedGroup, short_help="Draft notes with the generator")
def draft_group() -> None:
    """Draft notes with the configured generator and keep them until they are saved or discarded."""


@vault_command("create", short_help="Draft a note from a context packet on stdin")
@click.argument("kind")
@click.pass_context
def create_command(ctx: click.Context, kind: str) -> None:
    """Draft a note of KIND from the JSON context packet on stdin and store it under drafts/."""
    session = get_session(ctx)
    require_kind(session.vault, kind)
    packet = read_packet()
    prompt = generator.compose_prompt(session.vault, session.conn, kind, context=packet)
    envelope = generator.parse_envelope(generator.run(session.config.generator, prompt))
    draft = drafts.create(session.vault, kind, envelope.short_name, envelope.markdown, packet, clock.now())
    emit(ctx, render_draft(session.vault, draft), draft_as_data(session.vault, draft))


@vault_command("list", short_help="List the stored drafts")
@click.pass_context
def list_command(ctx: click.Context) -> None:
    """List the stored drafts, one line each, oldest first."""
    session = get_session(ctx)
    found = drafts.list_all(session.vault)
    human = "\n".join(render_draft(session.vault, draft) for draft in found)
    emit(ctx, human, [draft_as_data(session.vault, draft) for draft in found])


@vault_command("show", short_help="Print a draft")
@click.argument("draft_id", metavar="DRAFT-ID")
@click.pass_context
def show_command(ctx: click.Context, draft_id: str) -> None:
    """Print the draft's Markdown as it stands; with --json, its metadata, Markdown, and context packet."""
    session = get_session(ctx)
    draft = drafts.load(session.vault, draft_id)
    markdown = drafts.read_markdown(session.vault, draft.id)
    if not json_enabled(ctx):
        click.echo(markdown, nl=False)
        return
    context = drafts.read_context(session.vault, draft.id)
    emit(ctx, "", draft_as_data(session.vault, draft) | {"markdown": markdown, "context": context})


@vault_command("revise", short_help="Run the generator again with feedback")
@click.argument("draft_id", metavar="DRAFT-ID")
@click.argument("feedback", required=False)
@click.pass_context
def revise_command(ctx: click.Context, draft_id: str, feedback: str | None) -> None:
    """Run the generator again with FEEDBACK (an argument, or stdin when omitted) and replace the draft's Markdown."""
    session = get_session(ctx)
    draft = drafts.load(session.vault, draft_id)
    feedback = read_feedback(feedback)
    previous = drafts.read_markdown(session.vault, draft.id)
    context = drafts.read_context(session.vault, draft.id)
    prompt = generator.compose_prompt(
        session.vault, session.conn, draft.kind, context=context, previous=previous, feedback=feedback
    )
    envelope = generator.parse_envelope(generator.run(session.config.generator, prompt))
    draft = drafts.save_markdown(
        session.vault,
        draft,
        envelope.markdown,
        now=clock.now(),
        short_name=envelope.short_name,
        feedback=feedback,
    )
    emit(ctx, render_draft(session.vault, draft), draft_as_data(session.vault, draft))


@vault_command("save", short_help="Save a draft as a note")
@click.argument("draft_id", metavar="DRAFT-ID")
@click.option(
    "--name",
    "short_name",
    metavar="SHORT-NAME",
    help="The short name behind the filename, instead of the one the generator proposed.",
)
@click.pass_context
def save_command(ctx: click.Context, draft_id: str, short_name: str | None) -> None:
    """Turn the draft into a note: validate it, write it under notes/, index and commit it, delete the draft.

    A relative schedule is made canonical and a missing kind filled in first. A draft that is not a valid note is
    kept and its errors are reported with line numbers.
    """
    session = get_session(ctx)
    vault = session.vault
    draft = drafts.load(vault, draft_id)
    now = clock.now()
    name = short_name or draft.short_name
    path = target_path(vault, now, name)
    markdown_file = drafts.markdown_path(vault, draft.id)
    prepare(markdown_file, draft.kind, now)
    raw = markdown_file.read_bytes()
    errors = document.validate(document.parse(path, raw.decode("utf-8")), known_kinds(vault), vault)
    if errors:
        fail_draft(ctx, vault, draft, errors)
    # The note is validated under the name it is about to get and then created exclusively, so a note another
    # process wrote under that name meanwhile is not overwritten; only the numeric suffix can differ, and no
    # validation rule depends on it.
    path = create_dated_note(vault, now.date(), name, raw)
    # The name was claimed exclusively, so a delivery row under it is a leftover of a note deleted by hand and
    # would hand this note the old one's unread state.
    deliveries.clear(session.conn, path)
    report = session.sync()
    invalid = invalid_of(report, path)
    if invalid is None:
        drafts.delete(vault, draft.id)
    title = note_title(vault, path)
    emit(ctx, render(vault, path, title), as_data(vault, path, title, invalid) | {"draft": draft.id})
    if invalid is not None:
        fail_invalid(ctx, vault, invalid)


@vault_command("discard", short_help="Delete a draft")
@click.argument("draft_id", metavar="DRAFT-ID")
@click.pass_context
def discard_command(ctx: click.Context, draft_id: str) -> None:
    """Delete a draft without saving it."""
    session = get_session(ctx)
    draft = drafts.load(session.vault, draft_id)
    drafts.delete(session.vault, draft.id)
    data = {"id": draft.id, "kind": draft.kind, "short_name": draft.short_name, "discarded": True}
    emit(ctx, f"discarded draft {draft.id}", data)


for _command in (create_command, list_command, show_command, revise_command, save_command, discard_command):
    draft_group.add_command(_command)


def read_stdin(if_terminal: str) -> str:
    """Everything on stdin; when stdin is a terminal nothing is read and `if_terminal` is raised as a `UsageError`."""
    if sys.stdin.isatty():
        raise UsageError(if_terminal)
    return sys.stdin.read()


def read_packet() -> dict[str, Any]:
    """The context packet: one JSON object on stdin; a terminal, an empty stream, or anything else is a `UsageError`."""
    text = read_stdin("the context packet is read from stdin: pipe a JSON object into `notes draft create <kind>`")
    if not text.strip():
        raise UsageError("the context packet on stdin is empty: expected a JSON object")
    try:
        packet = json.loads(text)
    except ValueError as error:
        raise UsageError(f"the context packet on stdin is not valid JSON: {error}") from None
    if not isinstance(packet, dict):
        raise UsageError(f"the context packet must be a JSON object, not {generator.describe_json(packet)}")
    return packet


def read_feedback(given: str | None) -> str:
    """The feedback from the argument, or from stdin when the argument is missing; blank feedback is a `UsageError`."""
    if given is None:
        given = read_stdin("feedback is required: notes draft revise <draft-id> <feedback>, or pipe it on stdin")
    if not given.strip():
        raise UsageError("the feedback must not be empty")
    return given.strip()


def target_path(vault: Path, now: datetime, short_name: str) -> str:
    """The dated, collision-free ID the note will get when saved at `now` under `short_name`."""
    try:
        return dated_filename(vault, now.date(), short_name)
    except ValueError as error:
        raise UsageError(f"cannot build a filename: {error}") from None


def prepare(markdown_file: Path, kind: str, now: datetime) -> None:
    """Make the draft's Markdown ready for validation, in place: a relative schedule becomes canonical and a missing
    or null `kind` is filled with the draft's kind. A draft without a loadable frontmatter is left for validation."""
    document.normalize_schedule(markdown_file, now)

    def fill_kind(mapping: CommentedMap) -> bool:
        if mapping.get("kind") is not None:
            return False
        if "kind" in mapping:
            mapping["kind"] = kind
        else:
            mapping.insert(0, "kind", kind)
        return True

    try:
        document.rewrite_frontmatter(markdown_file, fill_kind)
    except ValidationFailed:
        return


def fail_draft(ctx: click.Context, vault: Path, draft: Draft, errors: list[ValidationError]) -> NoReturn:
    """Report a draft that is not a valid note and exit 1; the draft is kept for revision or editing.

    Human mode prints one `draft.md:line: message` line per error on stderr, as `notes check` does for a file; the
    error message, in both modes, summarizes them.
    """
    relative = drafts.relative_markdown_path(vault, draft.id)
    if not json_enabled(ctx):
        for line in error_lines(vault, [InvalidFile(relative, tuple(errors))]):
            click.echo(line, err=True)
    summary = "; ".join(f"line {error.line}: {error.message}" if error.line else error.message for error in errors)
    raise ValidationFailed(
        f"draft {draft.id} is not a valid note and is kept: {summary}; revise it or edit {vault / relative}"
    )


def render_draft(vault: Path, draft: Draft) -> str:
    """`[draft-id](abs draft.md) - short name`: the shape of a note link, pointing at the draft's Markdown."""
    return render_ref(drafts.markdown_path(vault, draft.id), draft.id, draft.short_name)


def draft_as_data(vault: Path, draft: Draft) -> dict[str, Any]:
    """The JSON shape of a draft: its metadata plus the vault-relative and absolute paths of its Markdown."""
    return {
        "id": draft.id,
        "kind": draft.kind,
        "short_name": draft.short_name,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
        "revisions": list(draft.revisions),
        "path": drafts.relative_markdown_path(vault, draft.id),
        "abs_path": str(drafts.markdown_path(vault, draft.id)),
    }
