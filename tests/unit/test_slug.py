"""Tests for slugs, short-name suggestions, dated filenames, and creation-date extraction."""

import unicodedata
from datetime import date
from pathlib import Path

import pytest

from notes import slug

CREATED = date(2026, 9, 2)
TITLE = "Project Atlas task updates over Kafka"


# slugify


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (TITLE, "project-atlas-task-updates-over-kafka"),
        ("Обновления задач через Kafka", "обновления-задач-через-kafka"),
        ("Why doesn't Project Atlas use WebSocket?!", "why-doesn-t-project-atlas-use-websocket"),
        ("ATLAS-27: follow-up (again)", "atlas-27-follow-up-again"),
        ("snake_case_name", "snake-case-name"),
        ("  --Hello, world!--  ", "hello-world"),
        ("multiple   spaces\tand\nnewlines", "multiple-spaces-and-newlines"),
        ("Release 2.0.1", "release-2-0-1"),
        # Latin letters with diacritics are letters and stay; the literals are the point of the case.
        ("Ünïcode Straße", "ünïcode-straße"),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slug.slugify(text) == expected


def test_slugify_keeps_decomposed_characters_together() -> None:
    precomposed = "école"
    decomposed = unicodedata.normalize("NFD", precomposed)
    assert decomposed != precomposed  # the accent is now a combining mark, which is not a letter by itself

    assert slug.slugify(decomposed) == precomposed


@pytest.mark.parametrize("text", ["", "   ", "!!!", "--_--", "?.,;"])
def test_slugify_rejects_text_without_letters_or_digits(text: str) -> None:
    with pytest.raises(ValueError, match="no letters or digits"):
        slug.slugify(text)


# suggest_short_name


def test_suggest_short_name_keeps_short_titles() -> None:
    assert slug.suggest_short_name(TITLE) == TITLE


def test_suggest_short_name_truncates_to_seven_words() -> None:
    title = "Why the Project Atlas does not talk to the backend over WebSocket"

    assert slug.suggest_short_name(title) == "Why the Project Atlas does not talk"


def test_suggest_short_name_collapses_whitespace_and_keeps_punctuation() -> None:
    assert slug.suggest_short_name("  Kafka,   not\tWebSocket!  ") == "Kafka, not WebSocket!"


def test_suggest_short_name_of_empty_title_is_empty() -> None:
    assert slug.suggest_short_name("   ") == ""


# dated_filename


def test_dated_filename_without_collision(bare_vault: Path) -> None:
    assert slug.dated_filename(bare_vault, CREATED, "Project Atlas") == "notes/2026-09-02-project-atlas.md"


def test_dated_filename_appends_numeric_suffixes_on_collision(bare_vault: Path) -> None:
    notes = bare_vault / "notes"
    (notes / "2026-09-02-project-atlas.md").write_text("# taken\n", encoding="utf-8")

    assert slug.dated_filename(bare_vault, CREATED, "Project Atlas") == "notes/2026-09-02-project-atlas-2.md"

    (notes / "2026-09-02-project-atlas-2.md").write_text("# taken too\n", encoding="utf-8")

    assert slug.dated_filename(bare_vault, CREATED, "Project Atlas") == "notes/2026-09-02-project-atlas-3.md"


def test_dated_filename_skips_over_a_gap_in_suffixes(bare_vault: Path) -> None:
    notes = bare_vault / "notes"
    (notes / "2026-09-02-x.md").write_text("# taken\n", encoding="utf-8")
    (notes / "2026-09-02-x-3.md").write_text("# taken\n", encoding="utf-8")

    assert slug.dated_filename(bare_vault, CREATED, "x") == "notes/2026-09-02-x-2.md"


def test_dated_filename_treats_a_directory_as_a_collision(bare_vault: Path) -> None:
    (bare_vault / "notes" / "2026-09-02-x.md").mkdir()

    assert slug.dated_filename(bare_vault, CREATED, "x") == "notes/2026-09-02-x-2.md"


def test_dated_filename_only_looks_at_the_same_date(bare_vault: Path) -> None:
    (bare_vault / "notes" / "2026-09-01-x.md").write_text("# other day\n", encoding="utf-8")

    assert slug.dated_filename(bare_vault, CREATED, "x") == "notes/2026-09-02-x.md"


def test_dated_filename_with_cyrillic_short_name(bare_vault: Path) -> None:
    expected = "notes/2026-09-02-обновления-задач-через-kafka.md"

    assert slug.dated_filename(bare_vault, CREATED, "Обновления задач через Kafka") == expected


def test_dated_filename_result_round_trips_through_creation_date(bare_vault: Path) -> None:
    path = slug.dated_filename(bare_vault, CREATED, "Project Atlas")

    assert slug.creation_date_from_path(path) == CREATED


def test_dated_filename_rejects_empty_slug(bare_vault: Path) -> None:
    with pytest.raises(ValueError, match="no letters or digits"):
        slug.dated_filename(bare_vault, CREATED, "!!!")


# create_dated_note


def test_create_dated_note_writes_the_content_under_the_dated_name(bare_vault: Path) -> None:
    path = slug.create_dated_note(bare_vault, CREATED, "Project Atlas", b"# note\n")

    assert path == "notes/2026-09-02-project-atlas.md"
    assert (bare_vault / path).read_bytes() == b"# note\n"


def test_create_dated_note_takes_the_next_suffix_instead_of_overwriting(bare_vault: Path) -> None:
    taken = bare_vault / "notes" / "2026-09-02-x.md"
    taken.write_text("# taken\n", encoding="utf-8")

    path = slug.create_dated_note(bare_vault, CREATED, "x", b"# new\n")

    assert path == "notes/2026-09-02-x-2.md"
    assert taken.read_text(encoding="utf-8") == "# taken\n"


def test_create_dated_note_treats_a_directory_as_a_collision(bare_vault: Path) -> None:
    (bare_vault / "notes" / "2026-09-02-x.md").mkdir()

    assert slug.create_dated_note(bare_vault, CREATED, "x", b"# new\n") == "notes/2026-09-02-x-2.md"


def test_create_dated_note_rejects_empty_slug(bare_vault: Path) -> None:
    with pytest.raises(ValueError, match="no letters or digits"):
        slug.create_dated_note(bare_vault, CREATED, "!!!", b"# new\n")


# creation_date_from_path


@pytest.mark.parametrize(
    "path",
    [
        "notes/2026-09-02-project-atlas-kafka-task-updates.md",
        "2026-09-02-project-atlas-kafka-task-updates.md",
        "notes/archive/2026-09-02-x.md",
        "/home/user/.notes/notes/2026-09-02-x.md",
        "notes/2026-09-02-обновления-задач-через-kafka.md",
    ],
)
def test_creation_date_from_path(path: str) -> None:
    assert slug.creation_date_from_path(path) == CREATED


def test_creation_date_from_path_ignores_dates_in_directories() -> None:
    assert slug.creation_date_from_path("notes/2025-01-01/2026-09-02-x.md") == CREATED


@pytest.mark.parametrize(
    "path",
    [
        "notes/kafka.md",
        "notes/2026-09-02.md",
        "notes/2026-09-02-.md",
        "notes/2026-9-2-x.md",
        "notes/20260902-x.md",
        "notes/2026-09-02-x.txt",
        "notes/2026-09-02-x",
    ],
)
def test_creation_date_from_path_rejects_undated_filenames(path: str) -> None:
    with pytest.raises(ValueError, match=r"must look like YYYY-MM-DD-<slug>\.md"):
        slug.creation_date_from_path(path)


@pytest.mark.parametrize("path", ["notes/2026-13-40-x.md", "notes/2026-02-30-x.md", "notes/0000-01-01-x.md"])
def test_creation_date_from_path_rejects_impossible_dates(path: str) -> None:
    with pytest.raises(ValueError, match="impossible date"):
        slug.creation_date_from_path(path)
