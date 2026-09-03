"""The vault-owned prompts and templates: reading them for a kind, and composing them the way the generator sees them.

`<vault>/prompt.md` holds the shared drafting instructions, `<vault>/types/<kind>/prompt.md` says what to capture
for the kind, and `<vault>/types/<kind>/template.md` is the scaffold `notes new` fills in. `notes prompt <kind>`
prints `compose` as is; the generator prompt of `notes draft` continues it with the vault's existing tags,
the context packet, and for revisions the previous draft and the feedback, one `section` each.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from notes.errors import UsageError
from notes.vault import kind_prompt_path, kind_template_path, known_kinds, shared_prompt_path

_BACKTICK_RUN_RE = re.compile(r"`+")


@dataclass(frozen=True)
class PromptParts:
    """The three vault-owned texts behind a kind; a missing `prompt.md` reads as an empty string."""

    kind: str
    shared: str
    kind_prompt: str
    template: str


def require_kind(vault: Path, kind: str) -> str:
    """`kind` when the vault knows it, otherwise a `UsageError` listing the known kinds."""
    kinds = known_kinds(vault)
    if kind not in kinds:
        known = ", ".join(kinds) or "none installed under types/"
        raise UsageError(f"unknown kind `{kind}` (known kinds: {known})")
    return kind


def load_parts(vault: Path, kind: str) -> PromptParts:
    """Read the shared prompt, the kind prompt, and the kind template of a known kind."""
    require_kind(vault, kind)
    return PromptParts(
        kind=kind,
        shared=_read_optional(shared_prompt_path(vault)),
        kind_prompt=_read_optional(kind_prompt_path(vault, kind)),
        template=kind_template_path(vault, kind).read_text(encoding="utf-8"),
    )


def compose(parts: PromptParts) -> str:
    """The prompt text before any context: the shared prompt, the kind prompt, and the template in a `Template` section.

    Empty prompts are skipped, so a user-added kind without a `prompt.md` still gets a well-formed prompt.
    """
    pieces = [parts.shared.strip(), parts.kind_prompt.strip(), section("Template", fenced(parts.template, "markdown"))]
    return "\n\n".join(piece for piece in pieces if piece)


def section(title: str, body: str) -> str:
    """A top-level heading over `body`, the shape every part of the generator prompt takes."""
    return f"# {title}\n\n{body.strip()}"


def fenced(text: str, info: str = "") -> str:
    """`text` inside a code fence longer than any backtick run it contains,
    so a fence inside the text cannot close it.
    """
    longest = max((len(run) for run in _BACKTICK_RUN_RE.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{text.rstrip()}\n{fence}"


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""
