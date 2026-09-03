"""Agent-assisted drafting: the prompt the generator receives, the generator run, and the envelope it returns.

The generator is whatever `[generator] command` in `config.toml` names, Codex by default: a program that reads the
prompt on stdin and prints one JSON object `{"short_name": ..., "markdown": ...}` on stdout. `compose_prompt`
continues `prompts.compose` (shared prompt, kind prompt, template) with the vault's existing tags and kinds, the
context packet, and for a revision the previous draft and the feedback. `run` executes the command through
`run_command`, and `parse_envelope` reads the envelope whether it came bare, inside a fenced block, or after
chatter. Nothing here touches the draft store, so a failing run leaves no partial draft behind.
"""

import json
import re
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from notes import proc, prompts, queries
from notes.config import GeneratorConfig
from notes.errors import GeneratorError
from notes.vault import known_kinds

REVISION_INSTRUCTION = (
    "Revise the previous draft according to the feedback below "
    "and return the complete revised note in the same envelope."
)

STDERR_TAIL_LINES = 3
"""How many of the generator's last stderr lines a non-zero exit carries into the error message."""

_FENCED_JSON_RE = re.compile(r"^(`{3,})json[ \t]*\n(.*?)\n\1[ \t]*$", re.DOTALL | re.MULTILINE)
_NOT_JSON = object()


@dataclass(frozen=True)
class Envelope:
    """What the generator returns: the short name for the filename and the complete note as Markdown."""

    short_name: str
    markdown: str


def compose_prompt(
    vault: Path,
    conn: sqlite3.Connection,
    kind: str,
    *,
    context: Mapping[str, Any] | None = None,
    previous: str | None = None,
    feedback: str | None = None,
) -> str:
    """The full generator prompt for a draft of `kind`, in the order the shared prompt announces.

    `prompts.compose` (shared prompt, kind prompt, template) comes first, then the tags in the index and the vault's
    kinds, then the context packet as pretty-printed JSON when there is one, then for a revision the previous draft
    and the feedback. An unknown kind is a `UsageError`.
    """
    parts = prompts.load_parts(vault, kind)
    pieces = [
        prompts.compose(parts),
        prompts.section("Existing tags", _listing(queries.all_tags(conn), "The vault has no tags yet.")),
        prompts.section("Existing kinds", _listing(known_kinds(vault), "The vault has no kinds installed.")),
    ]
    if context is not None:
        packet = json.dumps(context, indent=2, ensure_ascii=False)
        pieces.append(prompts.section("Context", prompts.fenced(packet, "json")))
    if previous is not None:
        draft = prompts.fenced(previous, "markdown")
        pieces.append(prompts.section("Previous draft", f"{REVISION_INSTRUCTION}\n\n{draft}"))
    if feedback is not None and feedback.strip():
        pieces.append(prompts.section("Feedback", feedback))
    return "\n\n".join(pieces)


def run(config: GeneratorConfig, prompt: str) -> str:
    """Run the generator with `prompt` on stdin and return what it printed on stdout.

    `config.command` is executed as an argument array, never through a shell, within `config.timeout_seconds`
    (no limit when that is zero or less). Its stderr is captured alongside stdout and forwarded to this process's
    stderr once it finishes, so the generator's own diagnostics stay visible. A missing program, a timeout, and a
    non-zero exit (carrying the last lines of that stderr) are `GeneratorError`s.
    """
    command = list(config.command)
    timeout = config.timeout_seconds if config.timeout_seconds > 0 else None
    try:
        result = proc.run_command(command, input=prompt, timeout=timeout)
    except proc.CommandNotFound as error:
        raise GeneratorError(
            f"generator command not found: {command[0]} (set [generator] command in config.toml)"
        ) from error
    except proc.CommandTimeout as error:
        raise GeneratorError(f"generator {command[0]} timed out after {error.timeout:g} seconds") from error
    _forward(result.stderr)
    if not result.ok:
        detail = _tail(result.stderr) or "it printed no error output"
        raise GeneratorError(f"generator {command[0]} exited with status {result.returncode}: {detail}")
    return result.stdout


def parse_envelope(stdout: str) -> Envelope:
    """The envelope in the generator's output.

    Three shapes are accepted, tried in this order: the whole output as one JSON object, the first fenced
    ```json block, and the last top-level JSON object in the text that has a `markdown` key (chatter before or
    after it is ignored). `short_name` and `markdown` must both be non-empty strings; the short name is trimmed,
    the Markdown kept as is. Anything else is a `GeneratorError`.
    """
    data = _envelope_object(stdout)
    short_name = _required_string(data, "short_name")
    markdown = _required_string(data, "markdown")
    return Envelope(short_name.strip(), markdown)


def _envelope_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise GeneratorError("the generator printed nothing")
    whole = _loads(stripped)
    if whole is not _NOT_JSON:
        if not isinstance(whole, dict):
            raise GeneratorError(f"the generator returned {describe_json(whole)} instead of a JSON object")
        return whole
    fenced = _FENCED_JSON_RE.search(text)
    if fenced is not None:
        block = _loads(fenced.group(2).strip())
        if isinstance(block, dict):
            return block
    for candidate in reversed(list(_top_level_objects(text))):
        if "markdown" in candidate:
            return candidate
    raise GeneratorError("the generator output contains no JSON object with a `markdown` field")


def _top_level_objects(text: str) -> Iterator[dict[str, Any]]:
    """Every JSON object in `text` that starts at a `{` outside any object decoded before it, in order."""
    decoder = json.JSONDecoder()
    position = 0
    while (start := text.find("{", position)) != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            position = start + 1
            continue
        if isinstance(value, dict):
            yield value
        position = end


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return _NOT_JSON


def describe_json(value: object) -> str:
    """What a decoded JSON value is, for a message expecting an object: `null`, `a boolean`, `a JSON array`, ..."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, list):
        return "a JSON array"
    if isinstance(value, str):
        return "a JSON string"
    return "a number"


def _required_string(data: Mapping[str, Any], key: str) -> str:
    if key not in data:
        raise GeneratorError(f"the generator envelope has no `{key}`")
    value = data[key]
    if not isinstance(value, str):
        raise GeneratorError(f"the generator envelope's `{key}` must be a string")
    if not value.strip():
        raise GeneratorError(f"the generator envelope's `{key}` is empty")
    return value


def _listing(items: Sequence[str], empty: str) -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty


def _tail(text: str, lines: int = STDERR_TAIL_LINES) -> str:
    """The last few non-empty lines of the generator's stderr, joined into one line."""
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(kept[-lines:])


def _forward(stderr: str) -> None:
    """Write the generator's captured stderr to this process's stderr, ending with a newline."""
    if stderr:
        sys.stderr.write(stderr if stderr.endswith("\n") else stderr + "\n")
        sys.stderr.flush()
