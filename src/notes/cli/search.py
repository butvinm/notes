"""`notes search <query>`: the notes matching by text, tag, or issue ID, best first, each with the reasons it matched.

Archived and superseded notes stay out of the text matches unless `--all` is given; a tag or issue-ID match always
shows them, with their status as the last reason. The pre-command sync has already run, so a note written a moment
ago is searchable.
"""

from collections.abc import Iterable
from pathlib import Path

import click

from notes.cli import get_session, vault_command
from notes.output import emit, render_link
from notes.search import SearchResult, as_data, search

DEFAULT_LIMIT = 20


@vault_command("search")
@click.argument("query")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=DEFAULT_LIMIT,
    show_default=True,
    metavar="N",
    help="Print at most N results.",
)
@click.option(
    "--all",
    "include_all",
    is_flag=True,
    help=(
        "Include archived and superseded notes among the text matches "
        "(an exact tag or issue-ID match shows them anyway)."
    ),
)
@click.pass_context
def search_command(ctx: click.Context, query: str, limit: int, include_all: bool) -> None:
    """Search notes by text, tags, and issue IDs; best matches first."""
    session = get_session(ctx)
    results = search(session.conn, query, limit=limit, include_all=include_all)
    emit(ctx, render(session.vault, results), [as_data(session.vault, result) for result in results])


def render(vault: Path, results: Iterable[SearchResult]) -> str:
    """One line per result, best first."""
    return "\n".join(render_line(vault, result) for result in results)


def render_line(vault: Path, result: SearchResult) -> str:
    """`[reasons] [id](abs) - Title`, the reasons comma-separated: `[issue: ATLAS-27, text, superseded]`."""
    return f"[{', '.join(result.reasons)}] {render_link(vault, result.path, result.title)}"
