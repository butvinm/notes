"""Tests for `notes.relations`: `add_relation` writes into the source note's frontmatter and refuses bad requests.

The notes are plain files in a `bare_vault`; nothing here needs Git or the index.
"""

from pathlib import Path

import pytest

from notes import relations
from notes.errors import UsageError, ValidationFailed

A = "notes/2026-09-02-a.md"
B = "notes/2026-09-01-b.md"
DEEP = "notes/archive/2026-08-01-deep.md"
B_REFERENCE = "2026-09-01-b.md"


def note_text(title: str, frontmatter: str = "kind: decision\nstatus: active\n") -> str:
    return f"---\n{frontmatter}---\n\n# {title}\n"


def write(vault: Path, path: str, text: str) -> None:
    target = vault / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def read(vault: Path, path: str) -> str:
    return (vault / path).read_text(encoding="utf-8")


@pytest.fixture
def notes(bare_vault: Path) -> Path:
    """Three notes without relations: two in `notes/` and one in a subdirectory."""
    write(bare_vault, A, note_text("A"))
    write(bare_vault, B, note_text("B"))
    write(bare_vault, DEEP, note_text("Deep", "kind: fact\nstatus: active\n"))
    return bare_vault


def test_add_relation_creates_the_key_and_returns_the_reference(notes: Path) -> None:
    reference = relations.add_relation(notes, A, "supersedes", B)

    assert reference == B_REFERENCE
    assert read(notes, A) == note_text(
        "A", f"kind: decision\nstatus: active\nrelated:\n  - relation: supersedes\n    note: {B_REFERENCE}\n"
    )
    assert read(notes, B) == note_text("B")


def test_add_relation_appends_after_the_existing_items(notes: Path) -> None:
    existing = f"kind: decision\nstatus: active\nrelated:\n  - relation: child\n    note: {B_REFERENCE}\n"
    write(notes, A, note_text("A", existing))

    relations.add_relation(notes, A, "supersedes", B)

    assert read(notes, A) == note_text("A", f"{existing}  - relation: supersedes\n    note: {B_REFERENCE}\n")


def test_add_relation_fills_a_null_related_key_in_place(notes: Path) -> None:
    write(notes, A, note_text("A", "kind: decision\nrelated:\nstatus: active\n"))

    relations.add_relation(notes, A, "related", B)

    assert read(notes, A) == note_text(
        "A", f"kind: decision\nrelated:\n  - relation: related\n    note: {B_REFERENCE}\nstatus: active\n"
    )


@pytest.mark.parametrize(
    ("src", "dst", "reference"),
    [(A, B, B_REFERENCE), (A, DEEP, "archive/2026-08-01-deep.md"), (DEEP, A, "../2026-09-02-a.md")],
)
def test_reference_is_relative_to_the_source_directory(notes: Path, src: str, dst: str, reference: str) -> None:
    assert relations.add_relation(notes, src, "related", dst) == reference
    assert f"    note: {reference}\n" in read(notes, src)


def test_unknown_relation_is_refused(notes: Path) -> None:
    with pytest.raises(UsageError, match=r"unknown relation `duplicate` \(expected supersedes, child, or related\)"):
        relations.add_relation(notes, A, "duplicate", B)

    assert read(notes, A) == note_text("A")


@pytest.mark.parametrize(("src", "dst"), [("notes/2026-09-02-nope.md", B), (A, "notes/2026-09-02-nope.md")])
def test_missing_note_on_either_side_is_refused(notes: Path, src: str, dst: str) -> None:
    with pytest.raises(UsageError, match="no note at notes/2026-09-02-nope.md"):
        relations.add_relation(notes, src, "related", dst)

    assert read(notes, A) == note_text("A")


def test_self_relation_is_refused(notes: Path) -> None:
    with pytest.raises(UsageError, match="a note cannot relate to itself"):
        relations.add_relation(notes, A, "related", A)

    assert read(notes, A) == note_text("A")


def test_duplicate_is_detected_through_any_spelling_of_the_reference(notes: Path) -> None:
    existing = f"kind: decision\nstatus: active\nrelated:\n  - relation: supersedes\n    note: ./{B_REFERENCE}\n"
    write(notes, A, note_text("A", existing))

    with pytest.raises(UsageError, match=f"duplicate relation: {A} already has `supersedes {B_REFERENCE}`"):
        relations.add_relation(notes, A, "supersedes", B)

    assert read(notes, A) == note_text("A", existing)


def test_same_target_with_another_relation_is_not_a_duplicate(notes: Path) -> None:
    relations.add_relation(notes, A, "supersedes", B)

    relations.add_relation(notes, A, "related", B)

    assert read(notes, A).count(f"note: {B_REFERENCE}\n") == 2


def test_malformed_existing_items_neither_crash_nor_hide_a_duplicate(notes: Path) -> None:
    existing = (
        "kind: decision\nstatus: active\n"
        "related:\n  - plain text\n  - relation: supersedes\n  - relation: supersedes\n    note: ../../outside.md\n"
        f"  - relation: supersedes\n    note: {B_REFERENCE}\n"
    )
    write(notes, A, note_text("A", existing))

    with pytest.raises(UsageError, match="duplicate relation"):
        relations.add_relation(notes, A, "supersedes", B)
    relations.add_relation(notes, A, "child", B)

    assert read(notes, A) == note_text("A", f"{existing}  - relation: child\n    note: {B_REFERENCE}\n")


def test_non_list_related_is_refused(notes: Path) -> None:
    write(notes, A, note_text("A", "kind: decision\nstatus: active\nrelated: nothing\n"))

    with pytest.raises(ValidationFailed, match=f"{A}: related must be a list of `relation` and `note` pairs"):
        relations.add_relation(notes, A, "related", B)

    assert read(notes, A) == note_text("A", "kind: decision\nstatus: active\nrelated: nothing\n")


# retarget


def related(*references: str, relation: str = "related") -> str:
    """A `related:` block with one item per reference."""
    return "related:\n" + "".join(f"  - relation: {relation}\n    note: {reference}\n" for reference in references)


def frontmatter(*extra: str) -> str:
    return "kind: decision\nstatus: active\n" + "".join(extra)


def move(vault: Path, old: str, new: str) -> None:
    """Rename the file the way `notes move` does before calling `retarget`."""
    (vault / new).parent.mkdir(parents=True, exist_ok=True)
    (vault / old).rename(vault / new)


def test_retarget_rewrites_the_moved_notes_references_relative_to_its_new_directory(notes: Path) -> None:
    write(notes, A, note_text("A", frontmatter(related(B_REFERENCE, "./archive/2026-08-01-deep.md"))))
    moved = "notes/archive/2026-09-02-a.md"
    move(notes, A, moved)

    assert relations.retarget(notes, A, moved) == [moved]
    assert read(notes, moved) == note_text("A", frontmatter(related("../2026-09-01-b.md", "2026-08-01-deep.md")))


def test_retarget_repoints_the_referrers_and_leaves_their_other_references_alone(notes: Path) -> None:
    write(notes, A, note_text("A", frontmatter(related("archive/2026-08-01-deep.md"))))
    write(notes, B, note_text("B", frontmatter(related("2026-09-02-a.md", "./archive/2026-08-01-deep.md"))))
    write(notes, DEEP, note_text("Deep", frontmatter(related("../2026-09-02-a.md", relation="supersedes"))))
    moved = "notes/2026-09-03-renamed.md"
    move(notes, A, moved)

    rewritten = relations.retarget(notes, A, moved, [DEEP, B, A])

    assert rewritten == [B, DEEP]
    assert read(notes, B) == note_text(
        "B", frontmatter(related("2026-09-03-renamed.md", "./archive/2026-08-01-deep.md"))
    )
    assert read(notes, DEEP) == note_text(
        "Deep", frontmatter(related("../2026-09-03-renamed.md", relation="supersedes"))
    )
    assert read(notes, moved) == note_text("A", frontmatter(related("archive/2026-08-01-deep.md")))


def test_retarget_into_a_subdirectory_rewrites_both_sides(notes: Path) -> None:
    write(notes, A, note_text("A", frontmatter(related(B_REFERENCE))))
    write(notes, B, note_text("B", frontmatter(related("2026-09-02-a.md", relation="supersedes"))))
    moved = "notes/archive/2026-09-02-a.md"
    move(notes, A, moved)

    assert relations.retarget(notes, A, moved, [B]) == [moved, B]
    assert read(notes, moved) == note_text("A", frontmatter(related("../2026-09-01-b.md")))
    assert read(notes, B) == note_text("B", frontmatter(related("archive/2026-09-02-a.md", relation="supersedes")))


def test_retarget_keeps_a_flow_style_list_and_a_reference_to_the_note_itself(notes: Path) -> None:
    write(notes, A, note_text("A", frontmatter(f"related: [{{relation: related, note: {B_REFERENCE}}}]\n")))
    write(notes, B, note_text("B", frontmatter("related: [{relation: child, note: 2026-09-02-a.md}]\n")))
    moved = "notes/archive/2026-09-02-a.md"
    move(notes, A, moved)

    assert relations.retarget(notes, A, moved, [B]) == [moved, B]
    assert read(notes, moved) == note_text(
        "A", frontmatter("related: [{relation: related, note: ../2026-09-01-b.md}]\n")
    )
    assert read(notes, B) == note_text(
        "B", frontmatter("related: [{relation: child, note: archive/2026-09-02-a.md}]\n")
    )

    write(notes, B, note_text("B", frontmatter(related("2026-09-01-b.md"))))
    renamed = "notes/archive/2026-09-01-renamed.md"
    move(notes, B, renamed)
    assert relations.retarget(notes, B, renamed) == [renamed]
    assert read(notes, renamed) == note_text("B", frontmatter(related("2026-09-01-renamed.md")))


def test_retarget_skips_what_it_cannot_rewrite(notes: Path) -> None:
    write(notes, A, "no frontmatter at all\n")
    write(notes, B, note_text("B", frontmatter(related("2026-09-02-a.md"))))
    stale = "notes/2026-09-03-stale.md"
    write(notes, stale, note_text("Stale", frontmatter(related(B_REFERENCE))))
    unclosed = "notes/2026-09-04-unclosed.md"
    write(notes, unclosed, "---\nkind: decision\n")
    moved = "notes/2026-09-03-renamed.md"
    move(notes, A, moved)

    rewritten = relations.retarget(notes, A, moved, [B, stale, unclosed, "notes/2026-09-05-missing.md"])

    assert rewritten == [B]
    assert read(notes, moved) == "no frontmatter at all\n"
    assert read(notes, B) == note_text("B", frontmatter(related("2026-09-03-renamed.md")))
    assert read(notes, stale) == note_text("Stale", frontmatter(related(B_REFERENCE)))
    assert read(notes, unclosed) == "---\nkind: decision\n"


def test_retarget_ignores_malformed_items_and_a_non_list_related(notes: Path) -> None:
    write(notes, A, note_text("A", frontmatter("related: nothing\n")))
    write(
        notes, B, note_text("B", frontmatter("related:\n  - plain text\n  - relation: child\n  - note: ../../out.md\n"))
    )
    moved = "notes/2026-09-03-renamed.md"
    move(notes, A, moved)

    assert relations.retarget(notes, A, moved, [B]) == []
    assert read(notes, moved) == note_text("A", frontmatter("related: nothing\n"))
    assert read(notes, B) == note_text(
        "B", frontmatter("related:\n  - plain text\n  - relation: child\n  - note: ../../out.md\n")
    )
