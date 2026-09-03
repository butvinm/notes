"""Output conventions: human lines with clickable absolute paths, or a stable JSON representation under `--json`."""

import json
from pathlib import Path
from typing import IO, Any

import click

from notes.errors import NotesError


def json_enabled(ctx: click.Context) -> bool:
    """Whether `--json` was given; the flag lives in the `dict` shared by the root group and its subcommands."""
    obj = ctx.find_object(dict)
    return bool(obj and obj.get("json"))


def render_link(vault: Path, path: str, title: str) -> str:
    """The ID linked to its absolute path, then the title: `[notes/x.md](/home/u/.notes/notes/x.md) - Title`."""
    return f"[{path}]({vault / path}) - {title}"


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def emit(ctx: click.Context, human: str, data: Any) -> None:
    """Print `data` as JSON when `--json` is active, otherwise `human`; an empty human text prints nothing."""
    if json_enabled(ctx):
        click.echo(to_json(data))
    elif human:
        click.echo(human)


def emit_error(error: NotesError, *, as_json: bool, file: IO[Any] | None = None) -> None:
    """Print an error to stderr (or `file`): `Error: <message>`, or `{"error": {"type", "message"}}` under `--json`."""
    envelope = {"error": {"type": error.type, "message": error.message}}
    text = to_json(envelope) if as_json else f"Error: {error.message}"
    click.echo(text, file=file, err=file is None)
