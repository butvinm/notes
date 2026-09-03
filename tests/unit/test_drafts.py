"""Tests for the draft store: ids, the three files of a draft, loading, listing, revising, and deleting."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from notes import drafts
from notes.drafts import Draft
from notes.errors import UsageError

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone(timedelta(hours=3)))
LATER = NOW + timedelta(hours=1, minutes=30)
STAMP = "2026-09-02T12:00:00+03:00"
LATER_STAMP = "2026-09-02T13:30:00+03:00"
DRAFT_ID = "20260902-120000-decision"
MARKDOWN = "---\nkind: decision\nstatus: active\n---\n\n# Kafka task updates\n\n**Decision:** Kafka.\n"
CONTEXT = {"facts": ["Task updates go over Kafka"], "заметка": "по-русски"}


def make(vault: Path, kind: str = "decision", now: datetime = NOW) -> Draft:
    return drafts.create(vault, kind, "kafka task updates", MARKDOWN, CONTEXT, now)


# new_id


def test_new_id_is_the_timestamp_and_the_kind(bare_vault: Path) -> None:
    assert drafts.new_id(bare_vault, "decision", NOW) == DRAFT_ID
    assert drafts.new_id(bare_vault, "fact", LATER) == "20260902-133000-fact"


def test_new_id_gets_a_numeric_suffix_when_the_directory_exists(bare_vault: Path) -> None:
    (bare_vault / "drafts" / DRAFT_ID).mkdir(parents=True)
    assert drafts.new_id(bare_vault, "decision", NOW) == f"{DRAFT_ID}-2"

    (bare_vault / "drafts" / f"{DRAFT_ID}-2").mkdir()
    assert drafts.new_id(bare_vault, "decision", NOW) == f"{DRAFT_ID}-3"


# create


def test_create_writes_the_three_files_and_returns_the_draft(bare_vault: Path) -> None:
    draft = make(bare_vault)

    directory = bare_vault / "drafts" / DRAFT_ID
    assert draft == Draft(DRAFT_ID, "decision", "kafka task updates", STAMP, STAMP, ())
    assert sorted(child.name for child in directory.iterdir()) == ["context.json", "draft.json", "draft.md"]
    assert (directory / "draft.md").read_text(encoding="utf-8") == MARKDOWN
    assert json.loads((directory / "context.json").read_text(encoding="utf-8")) == CONTEXT
    assert json.loads((directory / "draft.json").read_text(encoding="utf-8")) == {
        "id": DRAFT_ID,
        "kind": "decision",
        "short_name": "kafka task updates",
        "created_at": STAMP,
        "updated_at": STAMP,
        "revisions": [],
    }


def test_create_keeps_non_ascii_text_readable_in_the_json_files(bare_vault: Path) -> None:
    make(bare_vault)

    context = (bare_vault / "drafts" / DRAFT_ID / "context.json").read_text(encoding="utf-8")
    assert '"заметка": "по-русски"' in context
    assert context.endswith("\n")


def test_create_twice_at_the_same_moment_makes_two_drafts(bare_vault: Path) -> None:
    first = make(bare_vault)
    second = make(bare_vault)

    assert (first.id, second.id) == (DRAFT_ID, f"{DRAFT_ID}-2")
    assert [draft.id for draft in drafts.list_all(bare_vault)] == [DRAFT_ID, f"{DRAFT_ID}-2"]


def test_create_leaves_nothing_behind_when_a_write_fails(bare_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(drafts, "_write_meta", broken)

    with pytest.raises(OSError, match="disk full"):
        make(bare_vault)

    assert not (bare_vault / "drafts" / DRAFT_ID).exists()


# paths


def test_paths_of_a_draft(bare_vault: Path) -> None:
    directory = bare_vault / "drafts" / DRAFT_ID
    assert drafts.draft_dir(bare_vault, DRAFT_ID) == directory
    assert drafts.markdown_path(bare_vault, DRAFT_ID) == directory / "draft.md"
    assert drafts.context_path(bare_vault, DRAFT_ID) == directory / "context.json"
    assert drafts.relative_markdown_path(bare_vault, DRAFT_ID) == f"drafts/{DRAFT_ID}/draft.md"


@pytest.mark.parametrize("draft_id", ["", ".", "..", "a/b", "../x", "notes/x.md", "a\\b"])
def test_a_draft_id_is_one_path_segment(bare_vault: Path, draft_id: str) -> None:
    with pytest.raises(UsageError, match=r"no draft `.*` \(see `notes draft list`\)"):
        drafts.draft_dir(bare_vault, draft_id)


# load


def test_load_returns_what_create_stored(bare_vault: Path) -> None:
    created = make(bare_vault)

    assert drafts.load(bare_vault, DRAFT_ID) == created
    assert drafts.read_markdown(bare_vault, DRAFT_ID) == MARKDOWN
    assert drafts.read_context(bare_vault, DRAFT_ID) == CONTEXT


def test_load_takes_the_id_from_the_directory_name(bare_vault: Path) -> None:
    make(bare_vault)
    (bare_vault / "drafts" / DRAFT_ID).rename(bare_vault / "drafts" / "renamed")

    assert drafts.load(bare_vault, "renamed").id == "renamed"


def test_load_of_an_unknown_draft_fails(bare_vault: Path) -> None:
    with pytest.raises(UsageError, match=r"^no draft `nope` \(see `notes draft list`\)$"):
        drafts.load(bare_vault, "nope")


def test_load_of_a_directory_without_metadata_fails(bare_vault: Path) -> None:
    (bare_vault / "drafts" / "stray").mkdir(parents=True)
    (bare_vault / "drafts" / "stray" / "draft.md").write_text("# Stray\n", encoding="utf-8")

    with pytest.raises(UsageError, match="no draft `stray`"):
        drafts.load(bare_vault, "stray")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("{not json", "Expecting property name"),
        ("[1, 2]", "not a JSON object"),
        ('{"short_name": "x"}', "`kind` and `short_name` must be non-empty strings"),
        ('{"kind": "decision", "short_name": ""}', "`kind` and `short_name` must be non-empty strings"),
        ('{"kind": 5, "short_name": "x"}', "`kind` and `short_name` must be non-empty strings"),
        ('{"kind": "decision", "short_name": "x", "revisions": "no"}', "`revisions` must be a list of strings"),
        ('{"kind": "decision", "short_name": "x", "revisions": [1]}', "`revisions` must be a list of strings"),
    ],
)
def test_load_of_a_malformed_metadata_file_fails(bare_vault: Path, text: str, problem: str) -> None:
    make(bare_vault)
    meta = bare_vault / "drafts" / DRAFT_ID / "draft.json"
    meta.write_text(text, encoding="utf-8")

    with pytest.raises(UsageError) as info:
        drafts.load(bare_vault, DRAFT_ID)

    assert info.value.message.startswith(f"draft `{DRAFT_ID}` is unreadable: {meta}: ")
    assert problem in info.value.message


def test_load_tolerates_missing_timestamps_and_revisions(bare_vault: Path) -> None:
    make(bare_vault)
    meta = bare_vault / "drafts" / DRAFT_ID / "draft.json"
    meta.write_text('{"kind": "fact", "short_name": "bare"}', encoding="utf-8")

    assert drafts.load(bare_vault, DRAFT_ID) == Draft(DRAFT_ID, "fact", "bare", "", "", ())


def test_read_context_rejects_a_packet_that_is_not_an_object(bare_vault: Path) -> None:
    make(bare_vault)
    context = bare_vault / "drafts" / DRAFT_ID / "context.json"
    context.write_text("[1]", encoding="utf-8")

    with pytest.raises(UsageError, match="the context packet is not a JSON object"):
        drafts.read_context(bare_vault, DRAFT_ID)

    context.unlink()
    with pytest.raises(UsageError, match=f"draft `{DRAFT_ID}` is unreadable"):
        drafts.read_context(bare_vault, DRAFT_ID)


# list_all


def test_list_all_is_empty_without_a_drafts_directory(bare_vault: Path) -> None:
    assert drafts.list_all(bare_vault) == []


def test_list_all_returns_drafts_in_id_order_and_skips_what_is_not_a_draft(bare_vault: Path) -> None:
    later = make(bare_vault, "fact", LATER)
    earlier = make(bare_vault, "decision", NOW)
    (bare_vault / "drafts" / "stray-file").write_text("x", encoding="utf-8")
    (bare_vault / "drafts" / "00-empty-directory").mkdir()

    assert drafts.list_all(bare_vault) == [earlier, later]


# save_markdown


def test_save_markdown_records_a_revision(bare_vault: Path) -> None:
    draft = make(bare_vault)

    revised = drafts.save_markdown(
        bare_vault, draft, "# Revised\n", now=LATER, short_name="revised name", feedback="Shorter."
    )

    assert revised == Draft(DRAFT_ID, "decision", "revised name", STAMP, LATER_STAMP, ("Shorter.",))
    assert drafts.load(bare_vault, DRAFT_ID) == revised
    assert drafts.read_markdown(bare_vault, DRAFT_ID) == "# Revised\n"
    assert drafts.read_context(bare_vault, DRAFT_ID) == CONTEXT


def test_save_markdown_accumulates_revisions(bare_vault: Path) -> None:
    draft = make(bare_vault)

    first = drafts.save_markdown(bare_vault, draft, "# One\n", now=LATER, feedback="one")
    second = drafts.save_markdown(bare_vault, first, "# Two\n", now=LATER, feedback="two")

    assert second.revisions == ("one", "two")
    assert drafts.load(bare_vault, DRAFT_ID).revisions == ("one", "two")


def test_save_markdown_without_feedback_or_short_name_keeps_them(bare_vault: Path) -> None:
    draft = make(bare_vault)

    updated = drafts.save_markdown(bare_vault, draft, "# Same\n", now=LATER)

    assert updated == Draft(DRAFT_ID, "decision", "kafka task updates", STAMP, LATER_STAMP, ())
    assert drafts.read_markdown(bare_vault, DRAFT_ID) == "# Same\n"


# delete


def test_delete_removes_the_directory(bare_vault: Path) -> None:
    make(bare_vault)
    make(bare_vault, "fact", LATER)

    drafts.delete(bare_vault, DRAFT_ID)

    assert not (bare_vault / "drafts" / DRAFT_ID).exists()
    assert [draft.id for draft in drafts.list_all(bare_vault)] == ["20260902-133000-fact"]


def test_delete_of_an_unknown_draft_fails(bare_vault: Path) -> None:
    (bare_vault / "drafts" / "stray").mkdir(parents=True)

    with pytest.raises(UsageError, match="no draft `nope`"):
        drafts.delete(bare_vault, "nope")
    with pytest.raises(UsageError, match="no draft `stray`"):
        drafts.delete(bare_vault, "stray")

    assert (bare_vault / "drafts" / "stray").is_dir()
