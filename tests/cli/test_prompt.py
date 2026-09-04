"""Tests for `notes prompt <kind>`: the vault-owned prompts and template printed as the generator receives them."""

import json
from collections.abc import Callable
from pathlib import Path

from click.testing import CliRunner

from notes.cli import cli
from tests.conftest import indexed_paths

KNOWN = "decision, fact, idea, reminder"
NO_VAULT = "no vault at ~/.notes, run `notes init`"
RECIPE_TEMPLATE = "---\nkind: recipe\nstatus: active\n---\n\n# {title}\n"


def read(vault: Path, *parts: str) -> str:
    return vault.joinpath(*parts).read_text(encoding="utf-8")


def test_prompt_prints_shared_prompt_kind_prompt_and_fenced_template(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["prompt", "decision"])

    assert result.exit_code == 0
    shared = read(vault, "prompt.md").strip()
    kind_prompt = read(vault, "types", "decision", "prompt.md").strip()
    template = read(vault, "types", "decision", "template.md").rstrip()
    assert result.output == f"{shared}\n\n{kind_prompt}\n\n# Template\n\n```markdown\n{template}\n```\n"


def test_prompt_json_carries_the_three_parts_separately(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["prompt", "reminder", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "kind": "reminder",
        "shared_prompt": read(vault, "prompt.md"),
        "kind_prompt": read(vault, "types", "reminder", "prompt.md"),
        "template": read(vault, "types", "reminder", "template.md"),
    }
    assert result.stderr == ""


def test_prompt_reads_the_vault_owned_files_not_the_packaged_ones(runner: CliRunner, vault: Path) -> None:
    (vault / "prompt.md").write_text("Edited shared prompt\n", encoding="utf-8")
    (vault / "types" / "fact" / "prompt.md").write_text("Edited fact prompt\n", encoding="utf-8")

    result = runner.invoke(cli, ["prompt", "fact"])

    assert result.exit_code == 0
    assert result.output.startswith("Edited shared prompt\n\nEdited fact prompt\n\n# Template\n\n```markdown\n")


def test_prompt_for_a_user_added_kind_without_a_prompt_file(runner: CliRunner, vault: Path) -> None:
    kind_dir = vault / "types" / "recipe"
    kind_dir.mkdir()
    (kind_dir / "template.md").write_text(RECIPE_TEMPLATE, encoding="utf-8")

    human = runner.invoke(cli, ["prompt", "recipe"])
    machine = runner.invoke(cli, ["prompt", "recipe", "--json"])

    assert human.exit_code == 0
    shared = read(vault, "prompt.md").strip()
    assert human.output == f"{shared}\n\n# Template\n\n```markdown\n{RECIPE_TEMPLATE.rstrip()}\n```\n"
    assert machine.exit_code == 0
    assert json.loads(machine.stdout) == {
        "kind": "recipe",
        "shared_prompt": read(vault, "prompt.md"),
        "kind_prompt": "",
        "template": RECIPE_TEMPLATE,
    }


def test_prompt_rejects_an_unknown_kind(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["prompt", "recipe"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: unknown kind `recipe` (known kinds: {KNOWN})\n"


def test_prompt_unknown_kind_json_envelope(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["prompt", "recipe", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": {"type": "usage_error", "message": f"unknown kind `recipe` (known kinds: {KNOWN})"},
    }


def test_prompt_fails_without_a_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["prompt", "decision"])

    assert result.exit_code == 1
    assert result.stderr == f"Error: {NO_VAULT}\n"


def test_prompt_never_syncs_or_commits(runner: CliRunner, vault: Path, git_cmd: Callable[..., str]) -> None:
    pending = vault / "notes" / "2026-09-02-pending.md"
    pending.write_text("---\nkind: decision\nstatus: active\n---\n\n# Pending\n", encoding="utf-8")

    result = runner.invoke(cli, ["prompt", "decision"])

    assert result.exit_code == 0
    assert indexed_paths(vault) == []
    assert git_cmd(vault, "log", "--format=%s").splitlines() == ["notes: initialize vault"]
    assert pending.is_file()


def test_prompt_requires_a_kind_argument(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["prompt"])

    assert result.exit_code == 2
    assert "Missing argument 'KIND'" in result.stderr


def test_prompt_help_never_touches_the_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["prompt", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert not (home / ".notes").exists()


def test_prompt_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert "  prompt " in result.output
