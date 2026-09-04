"""Tests for reading and composing the vault-owned prompts and templates."""

from pathlib import Path

import pytest

from notes import prompts
from notes.errors import UsageError
from notes.prompts import PromptParts

TEMPLATE = "---\nkind: decision\n---\n\n# {title}\n"


def test_section_puts_a_heading_over_the_body() -> None:
    assert prompts.section("Template", "body\n") == "# Template\n\nbody"


def test_fenced_uses_three_backticks_by_default() -> None:
    assert prompts.fenced("plain\n") == "```\nplain\n```"
    assert prompts.fenced("plain", "markdown") == "```markdown\nplain\n```"


def test_fenced_outgrows_backtick_runs_inside_the_text() -> None:
    text = "before\n````yaml\nnested\n````\nafter"

    assert prompts.fenced(text) == f"`````\n{text}\n`````"


def test_compose_orders_shared_kind_and_template() -> None:
    parts = PromptParts("decision", "Shared.\n", "Kind.\n", TEMPLATE)

    assert prompts.compose(parts) == f"Shared.\n\nKind.\n\n# Template\n\n```markdown\n{TEMPLATE.rstrip()}\n```"


def test_compose_skips_empty_prompts() -> None:
    parts = PromptParts("recipe", "", "  \n", "# {title}\n")

    assert prompts.compose(parts) == "# Template\n\n```markdown\n# {title}\n```"


def test_load_parts_reads_the_vault_files(vault: Path) -> None:
    parts = prompts.load_parts(vault, "reminder")

    assert parts.kind == "reminder"
    assert parts.shared == (vault / "prompt.md").read_text(encoding="utf-8")
    assert parts.kind_prompt == (vault / "types" / "reminder" / "prompt.md").read_text(encoding="utf-8")
    assert parts.template == (vault / "types" / "reminder" / "template.md").read_text(encoding="utf-8")


def test_load_parts_tolerates_missing_prompt_files(vault: Path) -> None:
    (vault / "prompt.md").unlink()
    (vault / "types" / "idea" / "prompt.md").unlink()

    parts = prompts.load_parts(vault, "idea")

    assert parts.shared == ""
    assert parts.kind_prompt == ""
    assert parts.template.startswith("---\nkind: idea\n")


def test_load_parts_rejects_an_unknown_kind(vault: Path) -> None:
    expected = r"unknown kind `recipe` \(known kinds: decision, fact, idea, reminder\)"

    with pytest.raises(UsageError, match=expected):
        prompts.load_parts(vault, "recipe")


def test_require_kind_names_the_empty_types_directory(bare_vault: Path) -> None:
    with pytest.raises(UsageError, match=r"unknown kind `decision` \(known kinds: none installed under types/\)"):
        prompts.require_kind(bare_vault, "decision")


def test_require_kind_returns_a_known_kind(vault: Path) -> None:
    assert prompts.require_kind(vault, "fact") == "fact"
