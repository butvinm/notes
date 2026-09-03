"""The documentation stays consistent with the code: every command, kind, config key, and schedule form the README
shows is the one the CLI implements, and no document carries a banned glyph."""

import re
import tomllib
from pathlib import Path
from typing import Any

import click
import pytest

from notes import defaults_io, schedule
from notes.cli import cli
from tests.conftest import BANNED_GLYPHS, FROZEN_NOW

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"
CLAUDE_MD = REPO / "CLAUDE.md"
PLAN = REPO / "docs" / "plans" / "completed" / "20260902-notes-v1.md"

CANONICAL_SCHEDULES = ("at 2026-09-09T10:00:00+03:00", "every 3 days from 2026-09-02T10:00:00+03:00")
"""The canonical forms the README shows; they must parse and come back unchanged."""

RELATIVE_SCHEDULES = (
    "in 3 days",
    "in a week",
    "tomorrow",
    "tomorrow 09:30",
    "today 18:00",
    "2026-10-01",
    "2026-10-01 10:00",
    "every 2 weeks",
)
"""The relative forms the README promises are accepted wherever the CLI takes a schedule."""

_TOML_BLOCK_RE = re.compile(r"^```toml\n(.*?)^```$", re.DOTALL | re.MULTILINE)


def _commands(group: click.Group, prefix: str = "notes") -> list[str]:
    """Every command of the CLI spelled the way the README names it: `notes draft save`, not `save`."""
    names: list[str] = []
    for name, command in sorted(group.commands.items()):
        spelled = f"{prefix} {name}"
        if isinstance(command, click.Group):
            names.extend(_commands(command, spelled))
        else:
            names.append(spelled)
    return names


def _readme_toml_blocks() -> list[dict[str, Any]]:
    """The fenced `toml` blocks of the README, parsed; `tomllib` drops the explanatory comments by itself."""
    return [tomllib.loads(block) for block in _TOML_BLOCK_RE.findall(README.read_text(encoding="utf-8"))]


def _packaged_config() -> dict[str, Any]:
    return tomllib.loads(defaults_io.packaged_text("config.toml"))


@pytest.mark.parametrize("path", [README, CLAUDE_MD], ids=["README.md", "CLAUDE.md"])
def test_documents_exist_and_carry_no_banned_glyphs(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.strip()
    found = sorted({character for character in text if character in BANNED_GLYPHS})
    assert not found, f"{path.name} contains banned glyphs: {[hex(ord(c)) for c in found]}"


@pytest.mark.parametrize("spelled", _commands(cli))
def test_readme_describes_every_command(spelled: str) -> None:
    """A command absent from the README is either undocumented or renamed; both need the README updated."""
    assert f"`{spelled}" in README.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", defaults_io.packaged_kinds())
def test_readme_names_every_packaged_kind(kind: str) -> None:
    assert f"`{kind}`" in README.read_text(encoding="utf-8")


def test_readme_config_blocks_agree_with_the_packaged_defaults() -> None:
    """Every key a README `toml` block shows carries the value `notes init` installs."""
    packaged = _packaged_config()
    blocks = _readme_toml_blocks()
    assert blocks, "the README shows no toml block"
    for block in blocks:
        for section, values in block.items():
            assert section in packaged, f"the README documents an unknown config section {section}"
            for key, value in values.items():
                assert key in packaged[section], f"the README documents an unknown config key {section}.{key}"
                assert packaged[section][key] == value, f"the README value of {section}.{key} is stale"


def test_readme_documents_every_config_key() -> None:
    """One block is the whole `config.toml`, so a new key cannot be added without documenting it."""
    packaged = _packaged_config()
    assert packaged in _readme_toml_blocks()


@pytest.mark.parametrize("text", CANONICAL_SCHEDULES)
def test_readme_canonical_schedules_parse_and_round_trip(text: str) -> None:
    assert text in README.read_text(encoding="utf-8")
    assert schedule.format(schedule.parse(text)) == text


@pytest.mark.parametrize("text", RELATIVE_SCHEDULES)
def test_readme_relative_schedules_are_accepted(text: str) -> None:
    assert f"`{text}`" in README.read_text(encoding="utf-8")
    canonical = schedule.normalize_input(text, FROZEN_NOW, FROZEN_NOW.tzinfo)
    assert schedule.format(schedule.parse(canonical)) == canonical


def test_readme_names_the_plugin_installation_routes() -> None:
    text = README.read_text(encoding="utf-8")
    assert "claude --plugin-dir" in text
    assert "/plugin marketplace add butvinm/notes" in text
    assert "/plugin install notes@notes" in text


def test_readme_covers_the_remaining_required_topics() -> None:
    """The topics the plan asks the README to carry, each pinned to the phrase that carries it."""
    text = README.read_text(encoding="utf-8")
    for needle in (
        "uv tool install .",
        "~/.notes/",
        "YYYY-MM-DD-<slug>.md",
        "notes-tick.timer",
        "[generator]",
        '"short_name"',
        "notes init --remote",
    ):
        assert needle in text, f"the README no longer mentions {needle}"


def test_claude_md_names_the_checks_and_the_seam() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    for needle in ("uv sync", "uv run pytest", "uv run ruff check", "uv run ruff format --check", "uv run ty check"):
        assert needle in text
    assert "proc.run_command" in text
    assert "src/notes/defaults/" in text


def test_claude_md_points_at_documents_that_exist() -> None:
    """The two documents CLAUDE.md sends a reader to are the ones in the repository."""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert README.name in text
    plan_reference = PLAN.relative_to(REPO).as_posix()
    assert plan_reference in text
    assert PLAN.is_file()
