"""Tests for note document parsing, validation, frontmatter rewriting, and template rendering."""

import difflib
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from notes import document
from notes.document import Document, Heading, Relation, ValidationError
from notes.errors import ValidationFailed

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "notes"
KINDS = ("decision", "fact", "idea", "reminder")
KAFKA = "2026-09-02-project-atlas-kafka-task-updates.md"
WEBSOCKET = "2026-08-27-project-atlas-websocket.md"
CYRILLIC = "2026-09-01-обновления-задач-через-kafka.md"
# A line separator in Unicode but not in Markdown; built with chr() so the source stays keyboard-typeable.
UNICODE_LINE_SEPARATOR = chr(0x2028)


def install(vault: Path, name: str, *, as_name: str | None = None, text: str | None = None) -> Document:
    """Copy a fixture (or the given `text`) into `<vault>/notes/<as_name or name>` and parse it under that ID."""
    content = (FIXTURES / name).read_text(encoding="utf-8") if text is None else text
    relative = as_name or name
    target = vault / "notes" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return document.parse(f"notes/{relative}", content)


def note_text(frontmatter: str, body: str = "\n# A title\n") -> str:
    return f"---\n{frontmatter}\n---\n{body}"


def messages(vault: Path, doc: Document) -> list[str]:
    return [error.message for error in document.validate(doc, KINDS, vault)]


@pytest.fixture
def vault(bare_vault: Path) -> Path:
    """The bare vault with the WebSocket decision installed, so relations pointing at it resolve."""
    install(bare_vault, WEBSOCKET)
    return bare_vault


# Parsing valid notes


def test_valid_decision_parses_every_field(vault: Path) -> None:
    doc = install(vault, KAFKA)

    assert document.validate(doc, KINDS, vault) == []
    note = doc.to_note()
    assert note.path == f"notes/{KAFKA}"
    assert note.kind == "decision"
    assert note.status == "active"
    assert note.title == "Project Atlas task updates over Kafka"
    assert note.paths == ("~/Dev/exampleco/project-atlas",)
    assert note.tags == ("ATLAS-27", "project-atlas", "sync-worker")
    assert note.keywords == ("Kafka", "tasks", "runs", "launches", "jobs", "Atlas", "Project Atlas", "proxy")
    assert note.references == ("CLAUDE.md", "https://tracker.example/browse/ATLAS-27")
    assert note.schedule == "at 2026-09-09T10:00:00+03:00"
    assert note.related == (Relation("supersedes", f"notes/{WEBSOCKET}"),)
    assert note.created_date == date(2026, 9, 2)
    assert note.body.startswith("\n# Project Atlas task updates over Kafka\n")
    assert note.body.rstrip().endswith("durable delivery and replay.")
    assert note.content_hash == document.content_hash((FIXTURES / KAFKA).read_text(encoding="utf-8"))
    assert len(note.content_hash) == 64


def test_body_and_headings_carry_file_line_numbers(vault: Path) -> None:
    doc = install(vault, KAFKA)

    assert doc.body_start == 15
    assert doc.headings == (Heading(16, "Project Atlas task updates over Kafka"),)
    assert doc.title == "Project Atlas task updates over Kafka"


def test_cyrillic_title_tags_and_content(vault: Path) -> None:
    doc = install(vault, CYRILLIC)

    assert messages(vault, doc) == []
    note = doc.to_note()
    assert note.title == "Обновления задач Project Atlas идут через Kafka"
    assert note.tags == ("ATLAS-27", "решение", "kafka")
    assert note.keywords == ("Кафка", "очередь", "задачи", "Project Atlas")
    assert "Прокси обрывает долгие WebSocket-соединения" in note.body
    assert note.created_date == date(2026, 9, 1)


def test_h1_inside_code_fence_is_ignored(vault: Path) -> None:
    doc = install(vault, "2026-09-02-h1-in-code-fence.md")

    assert messages(vault, doc) == []
    assert doc.title == "Fenced headings are not titles"


def test_tilde_fence_and_closing_hashes(vault: Path) -> None:
    body = "\n~~~\n# not a title\n```\n# still fenced\n~~~\n\n# Real title ##\n\n```python\nprint('# comment')\n```\n"
    doc = install(vault, "", as_name="2026-09-02-fences.md", text=note_text("kind: fact\nstatus: active", body))

    assert doc.headings == (Heading(12, "Real title"),)
    assert messages(vault, doc) == []


def test_unknown_frontmatter_keys_are_allowed(vault: Path) -> None:
    doc = install(vault, "2026-09-02-extra-keys.md")

    assert messages(vault, doc) == []
    assert doc.get("project") == "exampleco"
    assert doc.get("labels") == {"team": "data", "area": "lineage"}
    assert doc.to_note().status == "archived"


def test_load_reads_the_file_from_the_vault(vault: Path) -> None:
    install(vault, KAFKA)

    doc = document.load(vault, f"notes/{KAFKA}")

    assert doc.title == "Project Atlas task updates over Kafka"
    assert doc.path == f"notes/{KAFKA}"


def test_load_keeps_line_endings_in_the_hash(vault: Path) -> None:
    crlf = "---\r\nkind: fact\r\nstatus: active\r\n---\r\n\r\n# Title\r\n"
    (vault / "notes" / "2026-09-02-crlf.md").write_bytes(crlf.encode())

    doc = document.load(vault, "notes/2026-09-02-crlf.md")

    assert doc.content_hash == document.content_hash(crlf)
    assert doc.title == "Title"


def test_content_hash_is_sha256_of_the_text() -> None:
    assert document.content_hash("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert document.content_hash("Кафка") != document.content_hash("Kafka")


# Invalid notes


@pytest.mark.parametrize(
    ("name", "expected", "line"),
    [
        ("2026-09-02-missing-kind.md", "kind is required", 1),
        ("2026-09-02-bad-status.md", "status must be active or archived", 3),
        ("2026-09-02-no-h1.md", "missing H1 title: the body needs exactly one line starting with `# `", 5),
        ("2026-09-02-two-h1.md", "more than one H1: a note has exactly one title", 8),
        (
            "2026-09-02-dangling-target.md",
            "related[0]: target `2026-01-01-gone.md` does not exist (resolved to notes/2026-01-01-gone.md)",
            5,
        ),
        ("2026-09-02-self-relation.md", "related[0]: a note cannot relate to itself", 5),
        (
            "2026-09-02-duplicate-relation.md",
            "related[2]: duplicate relation `related ./2026-08-27-project-atlas-websocket.md`",
            9,
        ),
        (
            "2026-09-02-unknown-relation.md",
            "related[0]: unknown relation `duplicate` (expected supersedes, child, or related)",
            5,
        ),
        ("2026-09-02-tags-not-list.md", "tags must be a list of strings", 4),
    ],
)
def test_invalid_fixture_reports_exactly_one_error(vault: Path, name: str, expected: str, line: int) -> None:
    doc = install(vault, name)

    assert document.validate(doc, KINDS, vault) == [ValidationError(line, expected)]


def test_undated_filename_is_rejected_with_a_hint_to_move(vault: Path) -> None:
    doc = install(vault, WEBSOCKET, as_name="kafka.md")

    assert document.validate(doc, KINDS, vault) == [
        ValidationError(None, "filename `kafka.md` must look like YYYY-MM-DD-<slug>.md; rename it with `notes move`")
    ]


def test_impossible_date_in_filename_is_rejected(vault: Path) -> None:
    doc = install(vault, WEBSOCKET, as_name="2026-13-40-x.md")

    assert document.validate(doc, KINDS, vault) == [
        ValidationError(
            None, "filename `2026-13-40-x.md` has an impossible date 2026-13-40; rename it with `notes move`"
        )
    ]


def test_dated_filename_in_a_subdirectory_is_valid(vault: Path) -> None:
    doc = install(vault, WEBSOCKET, as_name="archive/2025-01-01-old.md")

    assert messages(vault, doc) == []
    assert doc.to_note().created_date == date(2025, 1, 1)


def test_missing_frontmatter(vault: Path) -> None:
    doc = install(vault, "", as_name="2026-09-02-plain.md", text="# Just a title\n\nBody.\n")

    assert doc.frontmatter is None
    assert doc.body == "# Just a title\n\nBody.\n"
    assert doc.headings == (Heading(1, "Just a title"),)
    assert messages(vault, doc) == ["missing frontmatter: the file must start with a `---` line"]


def test_unterminated_frontmatter(vault: Path) -> None:
    doc = install(vault, "", as_name="2026-09-02-open.md", text="---\nkind: fact\nstatus: active\n\n# Title\n")

    assert doc.frontmatter is None
    assert doc.headings == ()
    assert messages(vault, doc) == [
        "frontmatter is not closed by a `---` line",
        "missing H1 title: the body needs exactly one line starting with `# `",
    ]


def test_malformed_yaml_reports_the_offending_line(vault: Path) -> None:
    doc = install(vault, "", as_name="2026-09-02-dup.md", text=note_text("kind: fact\nkind: decision\nstatus: active"))

    assert doc.frontmatter is None
    [problem] = doc.problems
    assert problem.line == 3
    assert problem.message.startswith("invalid YAML in frontmatter: found duplicate key")
    assert messages(vault, doc) == [problem.message]


def test_non_mapping_frontmatter(vault: Path) -> None:
    doc = install(vault, "", as_name="2026-09-02-list.md", text=note_text("- decision\n- active"))

    assert messages(vault, doc) == ["frontmatter must be a mapping of keys to values"]
    assert document.validate(doc, KINDS, vault)[0].line == 2


def test_empty_frontmatter_reports_required_keys(vault: Path) -> None:
    doc = install(vault, "", as_name="2026-09-02-empty.md", text="---\n---\n\n# Title\n")

    assert doc.frontmatter == {}
    assert messages(vault, doc) == ["kind is required", "status is required"]


def test_unknown_kind_lists_the_known_kinds(vault: Path) -> None:
    doc = install(vault, "", as_name="2026-09-02-memo.md", text=note_text("kind: memo\nstatus: active"))

    assert document.validate(doc, KINDS, vault) == [
        ValidationError(2, "unknown kind `memo` (known kinds: decision, fact, idea, reminder)")
    ]
    assert document.validate(doc, [], vault)[0].message.endswith("(known kinds: none installed under types/)")


def test_kind_and_status_must_be_strings(vault: Path) -> None:
    doc = install(vault, "", as_name="2026-09-02-types.md", text=note_text("kind: 3\nstatus: [active]"))

    assert document.validate(doc, KINDS, vault) == [
        ValidationError(2, "kind must be a string"),
        ValidationError(3, "status must be active or archived"),
    ]


def test_list_items_must_be_non_empty_strings(vault: Path) -> None:
    frontmatter = "kind: fact\nstatus: active\ntags: [ok, 2026-09-02, '']\nkeywords: 42"
    doc = install(vault, "", as_name="2026-09-02-items.md", text=note_text(frontmatter))

    assert document.validate(doc, KINDS, vault) == [
        ValidationError(4, "tags[1] must be a non-empty string (quote values that look like numbers or dates)"),
        ValidationError(4, "tags[2] must be a non-empty string"),
        ValidationError(5, "keywords must be a list of strings"),
    ]


def test_null_list_fields_count_as_empty(vault: Path) -> None:
    doc = install(vault, "", as_name="2026-09-02-nulls.md", text=note_text("kind: fact\nstatus: active\ntags:\npaths:"))

    assert messages(vault, doc) == []
    assert doc.to_note().tags == ()
    assert doc.to_note().paths == ()


def test_reminder_requires_a_schedule(vault: Path) -> None:
    without = install(vault, "", as_name="2026-09-02-r1.md", text=note_text("kind: reminder\nstatus: active"))
    null = install(vault, "", as_name="2026-09-02-r2.md", text=note_text("kind: reminder\nstatus: active\nschedule:"))

    assert document.validate(without, KINDS, vault) == [ValidationError(1, "schedule is required for a reminder")]
    assert document.validate(null, KINDS, vault) == [ValidationError(4, "schedule is required for a reminder")]


def test_schedule_must_be_a_non_empty_string(vault: Path) -> None:
    as_date = install(
        vault, "", as_name="2026-09-02-s1.md", text=note_text("kind: fact\nstatus: active\nschedule: 2026-09-09")
    )
    empty = install(vault, "", as_name="2026-09-02-s2.md", text=note_text("kind: fact\nstatus: active\nschedule: ''"))

    assert document.validate(as_date, KINDS, vault) == [ValidationError(4, "schedule must be a non-empty string")]
    assert document.validate(empty, KINDS, vault) == [ValidationError(4, "schedule must be a non-empty string")]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("in 3 days", "schedule: `in 3 days` is not a schedule: write `at <timestamp>`"),
        ("at 2026-09-09T10:00:00", "schedule: timestamp `2026-09-09T10:00:00` needs a UTC offset"),
        ("every 0 days from 2026-09-02T10:00:00+03:00", "schedule: count `0` must be a positive whole number"),
        # An unrepresentable period keeps the note out of the index, where `tick` would raise on it every minute.
        (
            "every 1000000000 days from 2026-09-02T10:00:00+03:00",
            "schedule: `1000000000 days` is a longer period than a schedule can name",
        ),
    ],
)
def test_schedule_must_follow_the_grammar(vault: Path, value: str, expected: str) -> None:
    text = note_text(f"kind: reminder\nstatus: active\nschedule: {value}")
    doc = install(vault, "", as_name="2026-09-02-grammar.md", text=text)

    [error] = document.validate(doc, KINDS, vault)
    assert error.line == 4
    assert error.message.startswith(expected)


def test_reminder_accepts_both_schedule_forms(vault: Path) -> None:
    recurring = "kind: reminder\nstatus: active\nschedule: every 1 week from 2026-09-02T10:00:00+03:00"
    one_shot = "kind: reminder\nstatus: active\nschedule: at 2026-09-09T10:00:00Z"

    assert messages(vault, install(vault, "", as_name="2026-09-02-r3.md", text=note_text(recurring))) == []
    assert messages(vault, install(vault, "", as_name="2026-09-02-r4.md", text=note_text(one_shot))) == []


def test_to_note_stores_the_canonical_schedule(vault: Path) -> None:
    text = note_text("kind: reminder\nstatus: active\nschedule: every 1 day from 2026-09-02T10:00Z")
    doc = install(vault, "", as_name="2026-09-02-canon.md", text=text)

    assert messages(vault, doc) == []
    assert doc.to_note().schedule == "every 1 days from 2026-09-02T10:00:00+00:00"


@pytest.mark.parametrize(
    ("related", "expected"),
    [
        ("related: supersedes", "related must be a list of `relation` and `note` pairs"),
        (f"related:\n  - {WEBSOCKET}", "related[0]: must be a mapping with `relation` and `note`"),
        ("related:\n  - relation: child", "related[0]: note must be the path of another note, relative to this file"),
        (f"related:\n  - note: {WEBSOCKET}", "related[0]: relation is required"),
        (
            "related:\n  - relation: child\n    note: ../config.toml",
            "related[0]: relation target `../config.toml` is outside notes/",
        ),
    ],
)
def test_related_shape_errors(vault: Path, related: str, expected: str) -> None:
    doc = install(vault, "", as_name="2026-09-02-rel.md", text=note_text(f"kind: fact\nstatus: active\n{related}"))

    assert messages(vault, doc) == [expected]


def test_relation_target_resolves_relative_to_the_note_directory(vault: Path) -> None:
    frontmatter = f"kind: decision\nstatus: active\nrelated:\n  - relation: child\n    note: ../{WEBSOCKET}"
    doc = install(vault, "", as_name="archive/2025-01-01-child.md", text=note_text(frontmatter))

    assert messages(vault, doc) == []
    assert doc.to_note().related == (Relation("child", f"notes/{WEBSOCKET}"),)


def test_errors_are_ordered_by_line_with_filename_first(vault: Path) -> None:
    text = "---\nkind: decision\nstatus: done\n---\n\n# First\n\n# Second\n"
    doc = install(vault, "", as_name="undated.md", text=text)

    assert [error.line for error in document.validate(doc, KINDS, vault)] == [None, 3, 8]


def test_to_note_refuses_documents_without_structure(vault: Path) -> None:
    with pytest.raises(ValidationFailed, match="not a valid note"):
        document.parse("notes/2026-09-02-x.md", "# Title only\n").to_note()
    with pytest.raises(ValidationFailed, match="not a valid note"):
        document.parse("notes/2026-09-02-x.md", "---\nkind: fact\nstatus: active\n---\nno title\n").to_note()
    with pytest.raises(ValidationFailed, match="must look like YYYY-MM-DD"):
        document.parse("notes/x.md", note_text("kind: fact\nstatus: active")).to_note()


def test_to_note_drops_malformed_optional_fields() -> None:
    frontmatter = (
        "kind: fact\nstatus: active\ntags: [ok, 3]\n"
        "related:\n  - relation: child\n  - relation: child\n    note: ../x.md"
    )
    note = document.parse("notes/2026-09-02-x.md", note_text(frontmatter)).to_note()

    assert note.tags == ("ok",)
    assert note.related == ()
    assert note.schedule is None


# Target normalization


@pytest.mark.parametrize(
    ("from_path", "target", "reference"),
    [
        ("notes/2026-09-02-a.md", "notes/2026-08-27-b.md", "2026-08-27-b.md"),
        ("notes/archive/2025-01-01-a.md", "notes/2026-08-27-b.md", "../2026-08-27-b.md"),
        ("notes/2026-09-02-a.md", "notes/archive/2025-01-01-b.md", "archive/2025-01-01-b.md"),
        ("notes/a/2026-09-02-a.md", "notes/b/2026-08-27-b.md", "../b/2026-08-27-b.md"),
    ],
)
def test_target_normalization_both_directions(from_path: str, target: str, reference: str) -> None:
    assert document.relative_target(from_path, target) == reference
    assert document.resolve_target(from_path, reference) == target


def test_resolve_target_normalizes_dot_segments() -> None:
    assert document.resolve_target("notes/2026-09-02-a.md", "./archive/../2026-08-27-b.md") == "notes/2026-08-27-b.md"


@pytest.mark.parametrize("reference", ["../config.toml", "/etc/hostname", "../../x.md", "", "."])
def test_resolve_target_rejects_references_leaving_notes(reference: str) -> None:
    with pytest.raises(ValueError, match="outside notes/"):
        document.resolve_target("notes/2026-09-02-a.md", reference)


# Frontmatter rewriting


def changed_lines(before: str, after: str) -> list[str]:
    diff = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=0)
    return [line for line in diff if line.startswith(("+ ", "- "))]


def append_relation(relation: str, reference: str):
    def mutate(mapping: CommentedMap) -> None:
        if "related" not in mapping:
            mapping["related"] = CommentedSeq()
        mapping["related"].append(CommentedMap([("relation", relation), ("note", reference)]))

    return mutate


def test_rewrite_appends_a_relation_and_changes_only_those_lines(tmp_path: Path) -> None:
    original = (FIXTURES / "2026-09-02-rewrite-original.md").read_text(encoding="utf-8")
    expected = (FIXTURES / "2026-09-02-rewrite-expected.md").read_text(encoding="utf-8")
    target = tmp_path / "note.md"
    target.write_text(original, encoding="utf-8")

    document.rewrite_frontmatter(target, append_relation("related", "archive/2025-01-01-old.md"))

    rewritten = target.read_text(encoding="utf-8")
    assert rewritten == expected
    assert changed_lines(original, rewritten) == ["+  - relation: related\n", "+    note: archive/2025-01-01-old.md\n"]
    assert document.parse("notes/x.md", rewritten).body == document.parse("notes/x.md", original).body


def test_rewrite_preserves_comments_quotes_and_flow_lists(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text(
        "---\nkind: fact # kind\n# standalone\nstatus: active\ntags: [a, 'b', \"c\"]\n---\n\n# T\n", encoding="utf-8"
    )

    document.rewrite_frontmatter(target, append_relation("child", "2026-08-27-x.md"))

    assert target.read_text(encoding="utf-8") == (
        "---\nkind: fact # kind\n# standalone\nstatus: active\ntags: [a, 'b', \"c\"]\n"
        "related:\n  - relation: child\n    note: 2026-08-27-x.md\n---\n\n# T\n"
    )


def test_rewrite_keeps_the_body_byte_for_byte(tmp_path: Path) -> None:
    prose = f"odd   spacing  \r\n\ttab{UNICODE_LINE_SEPARATOR}separator"
    body = f"\n# T   \n\n{prose}\n\n```\n---\n```\nno trailing newline"
    target = tmp_path / "note.md"
    target.write_bytes(f"---\nkind: fact\n---{body}".encode())

    def set_status(mapping: CommentedMap) -> None:
        mapping["status"] = "active"

    document.rewrite_frontmatter(target, set_status)

    assert target.read_bytes() == f"---\nkind: fact\nstatus: active\n---{body}".encode()


def test_rewrite_keeps_column_zero_sequence_style(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("---\nkind: fact\nstatus: active\nreferences:\n- a\n- b\n---\n# T\n", encoding="utf-8")

    def add_reference(mapping: CommentedMap) -> None:
        mapping["references"].append("c")

    document.rewrite_frontmatter(target, add_reference)

    assert (
        target.read_text(encoding="utf-8") == "---\nkind: fact\nstatus: active\nreferences:\n- a\n- b\n- c\n---\n# T\n"
    )


def test_rewrite_does_not_wrap_long_flow_lists(tmp_path: Path) -> None:
    keywords = "keywords: [" + ", ".join(f"keyword-number-{index}" for index in range(20)) + "]\n"
    target = tmp_path / "note.md"
    target.write_text(f"---\nkind: fact\n{keywords}---\n# T\n", encoding="utf-8")

    def set_status(mapping: CommentedMap) -> None:
        mapping["status"] = "active"

    document.rewrite_frontmatter(target, set_status)

    assert target.read_text(encoding="utf-8") == f"---\nkind: fact\n{keywords}status: active\n---\n# T\n"


def test_rewrite_of_empty_frontmatter(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("---\n---\n# T\n", encoding="utf-8")

    def set_kind(mapping: CommentedMap) -> None:
        mapping["kind"] = "fact"

    document.rewrite_frontmatter(target, set_kind)

    assert target.read_text(encoding="utf-8") == "---\nkind: fact\n---\n# T\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# T\n", "missing frontmatter"),
        ("---\nkind: fact\n# T\n", "not closed"),
        ("---\n- x\n---\n# T\n", "must be a mapping"),
        ("---\nkind: [\n---\n# T\n", "invalid YAML"),
    ],
)
def test_rewrite_refuses_files_without_a_frontmatter_mapping(tmp_path: Path, text: str, expected: str) -> None:
    target = tmp_path / "note.md"
    target.write_text(text, encoding="utf-8")

    with pytest.raises(ValidationFailed, match=expected):
        document.rewrite_frontmatter(target, lambda mapping: None)

    assert target.read_text(encoding="utf-8") == text


def test_rewrite_is_skipped_when_mutate_returns_false(tmp_path: Path) -> None:
    text = "---\nkind:    fact\nstatus: active\n---\n# T\n"
    target = tmp_path / "note.md"
    target.write_text(text, encoding="utf-8")

    def refuse(mapping: CommentedMap) -> bool:
        mapping["status"] = "archived"
        return False

    def set_status(mapping: CommentedMap) -> bool:
        mapping["status"] = "archived"
        return True

    assert document.rewrite_frontmatter(target, refuse) is False
    assert target.read_text(encoding="utf-8") == text
    assert document.rewrite_frontmatter(target, set_status) is True
    assert target.read_text(encoding="utf-8") == "---\nkind: fact\nstatus: archived\n---\n# T\n"


def test_rewrite_leaves_the_note_whole_when_the_replacement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "---\nkind: fact\nstatus: active\n---\n\n# T\n\nbody\n"
    target = tmp_path / "note.md"
    target.write_text(text, encoding="utf-8")

    def fail(source: object, destination: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(document.os, "replace", fail)

    with pytest.raises(OSError, match="no space left"):
        document.rewrite_frontmatter(target, lambda mapping: mapping.update(status="archived"))

    assert target.read_text(encoding="utf-8") == text
    assert [child.name for child in tmp_path.iterdir()] == ["note.md"]


def test_rewrite_keeps_the_mode_of_the_note_it_replaces(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("---\nkind: fact\n---\n# T\n", encoding="utf-8")
    target.chmod(0o640)

    document.rewrite_frontmatter(target, lambda mapping: mapping.update(status="active"))

    assert stat.S_IMODE(target.stat().st_mode) == 0o640


# Schedule normalization


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone(timedelta(hours=3)))


def reminder(schedule_line: str) -> str:
    return f"---\nkind: reminder\nstatus: active\n{schedule_line}\ntags: [a, 'b']  # keep\n---\n\n# T\n"


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("in 3 days", "at 2026-09-05T12:00:00+03:00"),
        ("tomorrow 09:30", "at 2026-09-03T09:30:00+03:00"),
        ("today 18:00", "at 2026-09-02T18:00:00+03:00"),
        ("2026-10-01 18:00", "at 2026-10-01T18:00:00+03:00"),
        ("every 2 weeks", "every 2 weeks from 2026-09-02T12:00:00+03:00"),
        ("at 2026-09-09T10:00Z", "at 2026-09-09T10:00:00+00:00"),
        ("every 1 day from 2026-09-09T10:00:00+03:00", "every 1 days from 2026-09-09T10:00:00+03:00"),
        # YAML loads these two without quotes as a date and as an aware datetime, not as strings.
        ("2026-10-01", "at 2026-10-01T09:00:00+03:00"),
        ("2026-10-01T10:00:00+03:00", "at 2026-10-01T10:00:00+03:00"),
    ],
)
def test_normalize_schedule_rewrites_relative_and_lenient_forms(tmp_path: Path, written: str, canonical: str) -> None:
    target = tmp_path / "note.md"
    target.write_text(reminder(f"schedule: {written}"), encoding="utf-8")

    assert document.normalize_schedule(target, NOW) == canonical

    assert target.read_text(encoding="utf-8") == reminder(f"schedule: {canonical}")


def test_normalize_schedule_leaves_a_canonical_file_untouched(tmp_path: Path) -> None:
    text = reminder("schedule:    at 2026-09-09T10:00:00+03:00    # spacing a rewrite would change")
    target = tmp_path / "note.md"
    target.write_text(text, encoding="utf-8")

    assert document.normalize_schedule(target, NOW) is None

    assert target.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    "value",
    ["whenever", "at 2026-09-09T10:00:00", "At 2026-09-09T10:00:00+03:00", "", "~", "[1, 2]", "2026-10-01T10:00:00"],
)
def test_normalize_schedule_leaves_unknown_forms_for_validation(tmp_path: Path, value: str) -> None:
    text = reminder(f"schedule: {value}")
    target = tmp_path / "note.md"
    target.write_text(text, encoding="utf-8")

    assert document.normalize_schedule(target, NOW) is None

    assert target.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text", ["---\nkind: fact\nstatus: active\n---\n# T\n", "# T\n", "---\nkind: [\n---\n# T\n"])
def test_normalize_schedule_without_a_schedule_or_a_frontmatter_changes_nothing(tmp_path: Path, text: str) -> None:
    target = tmp_path / "note.md"
    target.write_text(text, encoding="utf-8")

    assert document.normalize_schedule(target, NOW) is None

    assert target.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("at 2026-09-09T10:00:00+03:00", None),
        ("every 3 days from 2026-09-02T10:00:00+03:00", None),
        ("soon", "schedule: `soon` is not a schedule"),
        ("at 2026-09-09T10:00:00", "schedule: timestamp `2026-09-09T10:00:00` needs a UTC offset"),
    ],
)
def test_schedule_problem(text: str, expected: str | None) -> None:
    problem = document.schedule_problem(text)

    if expected is None:
        assert problem is None
    else:
        assert problem is not None and problem.startswith(expected)


# Template rendering


TEMPLATE = (
    "---\nkind: reminder\nstatus: active\ntags: []\nschedule: {schedule}\n---\n\n# {title}\n\n**Remind me:** {other}\n"
)


def test_render_template_replaces_title_and_schedule() -> None:
    rendered = document.render_template(TEMPLATE, "Call {schedule} back", "at 2026-09-09T10:00:00+03:00")

    assert "schedule: at 2026-09-09T10:00:00+03:00\n" in rendered
    assert "# Call {schedule} back\n" in rendered
    assert "{other}" in rendered


def test_render_template_without_schedule_leaves_a_null_schedule(vault: Path) -> None:
    rendered = document.render_template(TEMPLATE, "Water the plants")
    doc = install(vault, "", as_name="2026-09-02-plants.md", text=rendered)

    assert "schedule: \n" in rendered
    assert document.validate(doc, KINDS, vault) == [ValidationError(5, "schedule is required for a reminder")]


def test_rendered_template_with_schedule_is_valid(vault: Path) -> None:
    rendered = document.render_template(TEMPLATE, "Water the plants", "every 3 days from 2026-09-02T10:00:00+03:00")
    doc = install(vault, "", as_name="2026-09-02-plants.md", text=rendered)

    assert messages(vault, doc) == []
    assert doc.to_note().schedule == "every 3 days from 2026-09-02T10:00:00+03:00"
