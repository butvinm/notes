"""Tests for the packaged defaults: the version stamp, `install`, and the shipped prompts and templates."""

from dataclasses import replace
from pathlib import Path

import pytest

from notes import config, defaults_io, document
from notes.document import ValidationError
from notes.vault import is_vault, known_kinds
from tests.conftest import BANNED_GLYPHS

KINDS = ("decision", "fact", "idea", "reminder")
SECTIONS = {
    "decision": ("**Decision:**", "**Rationale:**"),
    "fact": ("**Fact:**", "**Source:**", "**Context:**"),
    "reminder": ("**Remind me:**", "**Why:**"),
    "idea": ("**Idea:**", "**Why it might matter:**", "**Open questions:**"),
}
SCHEDULE = "at 2026-09-09T10:00:00+03:00"
GITIGNORE = "index.sqlite\nindex.sqlite-wal\nindex.sqlite-shm\ndrafts/\nembeddings/\n"


# Version stamp


def test_packaged_version_is_a_positive_integer() -> None:
    assert defaults_io.packaged_version() >= 1


def test_installed_version_is_none_without_a_stamp(bare_vault: Path) -> None:
    assert defaults_io.installed_version(bare_vault) is None


def test_write_version_stamp_records_the_packaged_version(bare_vault: Path) -> None:
    defaults_io.write_version_stamp(bare_vault)

    stamp = bare_vault / ".defaults-version"
    assert stamp.read_text(encoding="utf-8") == f"{defaults_io.packaged_version()}\n"
    assert defaults_io.installed_version(bare_vault) == defaults_io.packaged_version()


def test_unreadable_stamp_counts_as_missing(bare_vault: Path) -> None:
    (bare_vault / ".defaults-version").write_text("garbage\n", encoding="utf-8")

    assert defaults_io.installed_version(bare_vault) is None
    assert defaults_io.newer_defaults_available(bare_vault) is True


def test_newer_defaults_available_compares_with_the_packaged_version(bare_vault: Path) -> None:
    packaged = defaults_io.packaged_version()
    stamp = bare_vault / ".defaults-version"

    assert defaults_io.newer_defaults_available(bare_vault) is True

    stamp.write_text(f"{packaged - 1}\n", encoding="utf-8")
    assert defaults_io.newer_defaults_available(bare_vault) is True

    stamp.write_text(f"{packaged}\n", encoding="utf-8")
    assert defaults_io.newer_defaults_available(bare_vault) is False

    stamp.write_text(f"{packaged + 1}\n", encoding="utf-8")
    assert defaults_io.newer_defaults_available(bare_vault) is False


# install


def expected_files(vault: Path) -> set[Path]:
    files = {vault / "config.toml", vault / "prompt.md", vault / ".gitignore", vault / ".defaults-version"}
    for kind in KINDS:
        files |= {vault / "types" / kind / "prompt.md", vault / "types" / kind / "template.md"}
    return files


def test_packaged_kinds_are_the_six_initial_kinds() -> None:
    assert defaults_io.packaged_kinds() == list(KINDS)


def test_install_creates_every_file_and_makes_the_directory_a_vault(home: Path) -> None:
    vault = home / ".notes"

    written = defaults_io.install(vault, auto_push=False)

    assert set(written) == expected_files(vault)
    assert all(path.is_file() for path in written)
    assert is_vault(vault)
    assert known_kinds(vault) == list(KINDS)
    assert defaults_io.installed_version(vault) == defaults_io.packaged_version()
    assert defaults_io.newer_defaults_available(vault) is False
    assert (vault / ".gitignore").read_text(encoding="utf-8") == GITIGNORE
    assert (vault / "prompt.md").read_text(encoding="utf-8") == defaults_io.packaged_text("prompt.md")
    idea_template = (vault / "types" / "idea" / "template.md").read_text(encoding="utf-8")
    assert idea_template == defaults_io.packaged_text("types", "idea", "template.md")


@pytest.mark.parametrize("auto_push", [False, True])
def test_install_honors_auto_push_and_keeps_the_other_settings(home: Path, auto_push: bool) -> None:
    vault = home / ".notes"

    defaults_io.install(vault, auto_push=auto_push)

    loaded = config.load(vault)
    assert loaded == replace(config.DEFAULTS, git=replace(config.DEFAULTS.git, auto_push=auto_push))


def test_config_text_changes_only_the_auto_push_line() -> None:
    packaged = defaults_io.packaged_text("config.toml")

    assert defaults_io.config_text(auto_push=False) == packaged
    assert defaults_io.config_text(auto_push=True) == packaged.replace("auto_push = false", "auto_push = true")


def test_install_into_an_existing_layout_overwrites_the_defaults(bare_vault: Path) -> None:
    (bare_vault / "config.toml").write_text("[git]\nauto_push = true\n", encoding="utf-8")

    defaults_io.install(bare_vault, auto_push=False)

    assert config.load(bare_vault).git.auto_push is False


# Packaged prompts and templates


@pytest.mark.parametrize("kind", KINDS)
def test_template_renders_into_a_valid_note(bare_vault: Path, kind: str) -> None:
    defaults_io.install(bare_vault, auto_push=False)
    template = (bare_vault / "types" / kind / "template.md").read_text(encoding="utf-8")
    schedule = SCHEDULE if kind in document.SCHEDULE_REQUIRED_KINDS else None
    text = document.render_template(template, "A rendered title", schedule)
    path = f"notes/2026-09-02-{kind}.md"
    (bare_vault / path).write_text(text, encoding="utf-8")

    doc = document.parse(path, text)

    assert document.validate(doc, known_kinds(bare_vault), bare_vault) == []
    note = doc.to_note()
    assert (note.kind, note.status, note.title, note.schedule) == (kind, "active", "A rendered title", schedule)
    assert note.paths == note.tags == note.keywords == note.references == ()
    assert "{" not in text
    for section in SECTIONS[kind]:
        assert section in note.body


@pytest.mark.parametrize("kind", document.SCHEDULE_REQUIRED_KINDS)
def test_scheduled_template_without_a_schedule_is_invalid(bare_vault: Path, kind: str) -> None:
    defaults_io.install(bare_vault, auto_push=False)
    template = (bare_vault / "types" / kind / "template.md").read_text(encoding="utf-8")
    text = document.render_template(template, "No schedule yet")
    path = f"notes/2026-09-02-{kind}.md"

    errors = document.validate(document.parse(path, text), known_kinds(bare_vault), bare_vault)

    assert errors == [ValidationError(8, f"schedule is required for a {kind}")]


def test_only_scheduled_kinds_carry_the_schedule_placeholder() -> None:
    for kind in KINDS:
        template = defaults_io.packaged_text("types", kind, "template.md")
        assert template.count("{title}") == 1
        assert ("schedule: {schedule}" in template) == (kind in document.SCHEDULE_REQUIRED_KINDS)


def test_every_kind_ships_a_prompt_that_names_its_sections() -> None:
    for kind in KINDS:
        prompt = defaults_io.packaged_text("types", kind, "prompt.md")
        assert prompt.startswith("# ")
        for section in SECTIONS[kind]:
            assert section in prompt


def test_shared_prompt_names_every_glyph_the_generator_must_avoid() -> None:
    prompt = defaults_io.packaged_text("prompt.md")

    for glyph in ("em dash", "en dash", "curly quotes", "ellipsis character", "arrow characters", "non-breaking space"):
        assert glyph in prompt, glyph


def test_shared_prompt_states_the_envelope_and_the_follow_up_rules() -> None:
    prompt = defaults_io.packaged_text("prompt.md")

    assert '{"short_name": ' in prompt
    assert '"markdown": ' in prompt
    assert f"`{SCHEDULE}`" in prompt
    assert "explicit approval" in prompt
    assert "personal reflections" in prompt


def test_packaged_files_use_plain_punctuation() -> None:
    root = defaults_io.packaged_root()
    files = [root.joinpath("prompt.md"), root.joinpath("config.toml"), root.joinpath("gitignore")]
    for kind in KINDS:
        files.extend(root.joinpath("types", kind, name) for name in ("prompt.md", "template.md"))

    for file in files:
        text = file.read_text(encoding="utf-8")
        assert not set(text) & set(BANNED_GLYPHS), str(file)
