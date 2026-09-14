"""`notes recall [<query>] [--cwd <path>]`: the bounded block of notes worth surfacing for a prompt.

The Claude Code hook runs it on every prompt, so it must stay inside the hook's time budget: the pre-command sync
commits pending edits but never pushes (the command is offline), and `pymorphy3` is loaded only for a Cyrillic
query. The output is deterministic and contains only titles, clickable paths, and reasons; an empty block prints
nothing at all, so the hook injects nothing.
"""

import os
from pathlib import Path

import click

from notes.cli import get_session, vault_command
from notes.output import emit, render_link, style_label, style_reasons
from notes.search import RecallItem, RecallResult, recall, recall_as_data

UNREAD_HEADING = "Unread notes:"
RELATED_HEADING = "Related notes:"


@vault_command("recall", offline=True, short_help="Surface the notes relevant to a prompt")
@click.argument("query", required=False)
@click.option(
    "--cwd",
    metavar="PATH",
    help="The working directory; a note with a `paths` entry covering it ranks higher. Defaults to the current one.",
)
@click.pass_context
def recall_command(ctx: click.Context, query: str | None, cwd: str | None) -> None:
    """Surface the notes relevant to a prompt: unread deliveries, then tag, issue-ID, and text matches."""
    session = get_session(ctx)
    result = recall(session.conn, session.config.recall, query, cwd or os.getcwd())
    emit(ctx, render(session.vault, result), [recall_as_data(session.vault, item) for item in result.items])


def render(vault: Path, result: RecallResult) -> str:
    """The two blocks, each under its heading and only when it has lines; empty text when both are empty."""
    lines: list[str] = []
    for heading, items in ((UNREAD_HEADING, result.unread), (RELATED_HEADING, result.related)):
        if items:
            lines.append(style_label(heading))
            lines.extend(render_line(vault, item) for item in items)
    return "\n".join(lines)


def render_line(vault: Path, item: RecallItem) -> str:
    """`- [reasons] [id](abs) - Title`, the reasons comma-separated: `- [issue: ATLAS-27, text, superseded] ...`."""
    reasons = style_reasons(f"[{', '.join(item.reasons)}]")
    return f"- {reasons} {render_link(vault, item.path, item.title)}"
