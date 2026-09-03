"""Tests for vault location, layout helpers, kind discovery, and note ID resolution."""

from collections.abc import Callable
from pathlib import Path

import pytest

from notes import vault
from notes.errors import NoVaultError, UsageError


def test_root_follows_home(home: Path) -> None:
    assert vault.root() == home / ".notes"


def test_root_changes_with_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "other"))

    assert vault.root() == tmp_path / "other" / ".notes"


@pytest.mark.parametrize(
    ("helper", "expected"),
    [
        (vault.notes_dir, "notes"),
        (vault.types_dir, "types"),
        (vault.drafts_dir, "drafts"),
        (vault.db_path, "index.sqlite"),
        (vault.config_path, "config.toml"),
        (vault.shared_prompt_path, "prompt.md"),
    ],
)
def test_layout_helpers(helper: Callable[[Path], Path], expected: str) -> None:
    assert helper(Path("/home/user/.notes")) == Path("/home/user/.notes") / expected


def test_is_vault_requires_config_and_notes_dir(tmp_path: Path) -> None:
    assert not vault.is_vault(tmp_path / "missing")

    config_only = tmp_path / "config-only"
    config_only.mkdir()
    (config_only / "config.toml").write_text("", encoding="utf-8")
    assert not vault.is_vault(config_only)

    notes_only = tmp_path / "notes-only"
    (notes_only / "notes").mkdir(parents=True)
    assert not vault.is_vault(notes_only)

    (notes_only / "config.toml").write_text("", encoding="utf-8")
    assert vault.is_vault(notes_only)


def test_is_vault_rejects_notes_file(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "notes").write_text("not a directory", encoding="utf-8")

    assert not vault.is_vault(tmp_path)


def test_require_vault_returns_root(bare_vault: Path) -> None:
    assert vault.require_vault() == bare_vault


def test_require_vault_without_vault(home: Path) -> None:
    with pytest.raises(NoVaultError, match=r"no vault at ~/\.notes, run `notes init`"):
        vault.require_vault()


def test_require_vault_with_incomplete_layout(home: Path) -> None:
    (home / ".notes" / "notes").mkdir(parents=True)

    with pytest.raises(NoVaultError):
        vault.require_vault()


def test_known_kinds_lists_directories_with_template(bare_vault: Path) -> None:
    types = bare_vault / "types"
    for kind in ("reminder", "decision", "fact"):
        (types / kind).mkdir()
        (types / kind / "template.md").write_text("---\nkind: x\n---\n# {title}\n", encoding="utf-8")
    (types / "prompt-only").mkdir()
    (types / "prompt-only" / "prompt.md").write_text("no template here", encoding="utf-8")
    (types / "stray-file.md").write_text("", encoding="utf-8")

    assert vault.known_kinds(bare_vault) == ["decision", "fact", "reminder"]


def test_known_kinds_without_types_dir(tmp_path: Path) -> None:
    assert vault.known_kinds(tmp_path) == []


@pytest.fixture
def note_file(bare_vault: Path) -> Path:
    path = bare_vault / "notes" / "2026-09-02-kafka.md"
    path.write_text("---\nkind: decision\nstatus: active\n---\n\n# Kafka\n", encoding="utf-8")
    return path


def test_resolve_id_vault_relative(bare_vault: Path, note_file: Path) -> None:
    assert vault.resolve_id(bare_vault, "notes/2026-09-02-kafka.md") == "notes/2026-09-02-kafka.md"


def test_resolve_id_absolute_inside_vault(bare_vault: Path, note_file: Path) -> None:
    assert vault.resolve_id(bare_vault, str(note_file)) == "notes/2026-09-02-kafka.md"


def test_resolve_id_bare_filename(bare_vault: Path, note_file: Path) -> None:
    assert vault.resolve_id(bare_vault, "2026-09-02-kafka.md") == "notes/2026-09-02-kafka.md"


def test_resolve_id_expands_tilde(bare_vault: Path, note_file: Path) -> None:
    assert vault.resolve_id(bare_vault, "~/.notes/notes/2026-09-02-kafka.md") == "notes/2026-09-02-kafka.md"


def test_resolve_id_accepts_dot_prefix(bare_vault: Path, note_file: Path) -> None:
    assert vault.resolve_id(bare_vault, "./notes/2026-09-02-kafka.md") == "notes/2026-09-02-kafka.md"


def test_resolve_id_absolute_through_symlink(bare_vault: Path, note_file: Path, tmp_path: Path) -> None:
    link = tmp_path / "vault-link"
    link.symlink_to(bare_vault)

    assert vault.resolve_id(bare_vault, str(link / "notes" / "2026-09-02-kafka.md")) == "notes/2026-09-02-kafka.md"


def test_resolve_id_subdirectory_forms(bare_vault: Path) -> None:
    sub = bare_vault / "notes" / "archive"
    sub.mkdir()
    old = sub / "2025-01-01-old.md"
    old.write_text("# Old\n", encoding="utf-8")

    assert vault.resolve_id(bare_vault, "notes/archive/2025-01-01-old.md") == "notes/archive/2025-01-01-old.md"
    assert vault.resolve_id(bare_vault, "archive/2025-01-01-old.md") == "notes/archive/2025-01-01-old.md"
    assert vault.resolve_id(bare_vault, str(old)) == "notes/archive/2025-01-01-old.md"


def test_resolve_id_missing_file(bare_vault: Path) -> None:
    with pytest.raises(UsageError, match=r"no note at notes/2026-09-02-missing\.md"):
        vault.resolve_id(bare_vault, "2026-09-02-missing.md")


def test_resolve_id_directory_is_not_a_note(bare_vault: Path) -> None:
    (bare_vault / "notes" / "archive").mkdir()

    with pytest.raises(UsageError, match="no note at notes/archive"):
        vault.resolve_id(bare_vault, "archive")


def test_resolve_id_rejects_paths_outside_notes(bare_vault: Path) -> None:
    outside = [
        "notes/../config.toml",
        str(bare_vault / "config.toml"),
        "/etc/hostname",
        "notes",
        "notes/",
        ".",
    ]
    for text in outside:
        with pytest.raises(UsageError, match="not a path under notes/"):
            vault.resolve_id(bare_vault, text)


def test_resolve_id_rejects_empty_text(bare_vault: Path) -> None:
    with pytest.raises(UsageError, match="empty note id"):
        vault.resolve_id(bare_vault, "   ")


def test_canonical_id_needs_no_file(bare_vault: Path) -> None:
    for text in ("notes/2026-09-03-new.md", "2026-09-03-new.md", str(bare_vault / "notes" / "2026-09-03-new.md")):
        assert vault.canonical_id(bare_vault, text) == "notes/2026-09-03-new.md"
    assert vault.canonical_id(bare_vault, "archive/2026-09-03-new.md") == "notes/archive/2026-09-03-new.md"
    assert vault.canonical_id(bare_vault, "notes/archive/../2026-09-03-new.md") == "notes/2026-09-03-new.md"
    assert not (bare_vault / "notes" / "2026-09-03-new.md").exists()


def test_canonical_id_rejects_paths_outside_notes_and_empty_text(bare_vault: Path) -> None:
    for text in ("notes/../config.toml", "/etc/hostname", "notes", "."):
        with pytest.raises(UsageError, match="not a path under notes/"):
            vault.canonical_id(bare_vault, text)
    with pytest.raises(UsageError, match="empty note id"):
        vault.canonical_id(bare_vault, "")
