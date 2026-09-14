"""Output conventions: human lines with clickable absolute paths, or a stable JSON representation under `--json`.

The human lines are coloured when stdout is a terminal: the `style_*` helpers colour one field each, so the commands
agree on what a kind, a status, an unread marker, or a match reason looks like. `NO_COLOR` turns colour off and
`FORCE_COLOR` turns it on regardless of the terminal; the text under the colour never changes, so a pipe, the hook,
and the tests see the same lines without the escape codes.
"""

import json
import os
import sys
from pathlib import Path
from typing import IO, Any

import click

from notes.errors import NotesError

STATUS_COLORS = {"active": "green", "archived": "bright_black", "superseded": "magenta"}


def json_enabled(ctx: click.Context) -> bool:
    """Whether `--json` was given; the flag lives in the `dict` shared by the root group and its subcommands."""
    obj = ctx.find_object(dict)
    return bool(obj and obj.get("json"))


def colors_enabled() -> bool:
    """Whether human output carries colour: never under `NO_COLOR`, always under `FORCE_COLOR`, else on a terminal."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def style(text: str, **attrs: Any) -> str:
    """`click.style` when colour is enabled, the bare text otherwise; `attrs` are its keyword arguments."""
    return click.style(text, **attrs) if colors_enabled() else text


def style_kind(kind: str) -> str:
    return style(kind, fg="cyan")


def style_status(status: str) -> str:
    """An effective status in its colour: active green, archived dim, superseded magenta; an unknown one plain."""
    color = STATUS_COLORS.get(status.strip())
    return style(status, fg=color) if color else status


def style_unread(marker: str) -> str:
    return style(marker, fg="yellow", bold=True)


def style_reasons(reasons: str) -> str:
    return style(reasons, fg="blue")


def style_label(label: str) -> str:
    """A leading word that names what the line reports, such as `delivered:` or a block heading."""
    return style(label, fg="green", bold=True)


def hyperlink(text: str, uri: str) -> str:
    """`text` wrapped in an OSC 8 hyperlink to `uri`: the terminal shows the text and opens the target on click."""
    return f"\x1b]8;;{uri}\x1b\\{text}\x1b]8;;\x1b\\"


def render_link(vault: Path, path: str, title: str) -> str:
    """A note as a clickable reference, in the shape that suits the reader.

    In a pipe, the ID linked to its absolute path, then the title: `[notes/x.md](/home/u/.notes/notes/x.md) - Title`;
    the hook and the skills parse that form. On a terminal, the title in bold, then the ID dimmed, both carrying the
    absolute path as a hyperlink, so the line is half as long and the title comes first.
    """
    if not colors_enabled():
        return f"[{path}]({vault / path}) - {title}"
    uri = (vault / path).as_uri()
    return f"{hyperlink(style(title, bold=True), uri)}  {hyperlink(style(path, fg='bright_black'), uri)}"


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def emit(ctx: click.Context, human: str, data: Any) -> None:
    """Print `data` as JSON when `--json` is active, otherwise `human`; an empty human text prints nothing."""
    if json_enabled(ctx):
        click.echo(to_json(data))
    elif human:
        click.echo(human, color=colors_enabled())


def emit_error(error: NotesError, *, as_json: bool, file: IO[Any] | None = None) -> None:
    """Print an error to stderr (or `file`): `Error: <message>`, or `{"error": {"type", "message"}}` under `--json`."""
    envelope = {"error": {"type": error.type, "message": error.message}}
    text = to_json(envelope) if as_json else f"Error: {error.message}"
    click.echo(text, file=file, err=file is None)
