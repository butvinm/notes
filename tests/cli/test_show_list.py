"""Tests for `notes show` and `notes list`: one note verbatim or as JSON, and the filtered, ordered listing.

The notes are written by hand under `notes/` and picked up by the pre-command sync; unread state is made by inserting
rows into `deliveries` directly, since `notes tick` arrives in a later task.
"""

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes.cli import cli
from tests.conftest import deliver

Git = Callable[..., str]

KAFKA = "notes/2026-09-02-kafka.md"
PLANTS = "notes/2026-09-02-plants.md"
OLD = "notes/2026-09-01-old-kafka.md"
LEGACY = "notes/2026-08-30-legacy.md"
IDEA = "notes/2026-08-15-idea.md"
NO_KIND = "---\nstatus: active\n---\n\n# No kind\n"


def note_text(
    title: str,
    *,
    kind: str = "decision",
    status: str = "active",
    paths: Sequence[str] = (),
    tags: Sequence[str] = (),
    keywords: Sequence[str] = (),
    schedule: str | None = None,
    supersedes: str | None = None,
    body: str = "Some text.",
) -> str:
    lines = [f"kind: {kind}", f"status: {status}"]
    for field, items in (("paths", paths), ("tags", tags), ("keywords", keywords)):
        if items:
            lines.append(f"{field}: [{', '.join(items)}]")
    if schedule is not None:
        lines.append(f"schedule: {schedule}")
    if supersedes is not None:
        lines.append(f"related:\n  - relation: supersedes\n    note: {supersedes}")
    return "---\n" + "\n".join(lines) + f"\n---\n\n# {title}\n\n{body}\n"


def write(vault: Path, name: str, text: str) -> str:
    target = vault / "notes" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return f"notes/{name}"


def listed_paths(result_output: str) -> list[str]:
    """The IDs in the order `notes list` printed them."""
    return [line.split("[", 2)[-1].split("]", 1)[0] for line in result_output.splitlines()]


@pytest.fixture
def corpus(runner: CliRunner, vault: Path) -> dict[str, str]:
    """Five hand-written notes across every kind of status, synced once: an active decision superseding an older one,
    an archived fact, a scheduled reminder, and an idea; returns their texts by ID."""
    texts = {
        OLD: note_text("Kafka over WebSocket", tags=["ATLAS-27"], paths=["~/Dev/exampleco/project-atlas"]),
        KAFKA: note_text(
            "Project Atlas task updates over Kafka",
            paths=["~/Dev/exampleco"],
            tags=["ATLAS-27", "sync-worker"],
            keywords=["Atlas", "Project Atlas"],
            supersedes="2026-09-01-old-kafka.md",
            body="**Decision:** Kafka.\n\n**Rationale:** Fewer moving parts.",
        ),
        LEGACY: note_text("Legacy fact", kind="fact", status="archived", tags=["legacy"]),
        PLANTS: note_text("Water the plants", kind="reminder", schedule="at 2026-09-09T10:00:00+03:00", tags=["home"]),
        IDEA: note_text("An idea", kind="idea", paths=["~/Dev/orion"]),
    }
    for path, text in texts.items():
        (vault / path).write_text(text, encoding="utf-8")
    result = runner.invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    return texts


# notes show


def test_show_prints_the_file_verbatim(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    result = runner.invoke(cli, ["show", KAFKA])

    assert result.exit_code == 0, result.output
    assert result.stdout == corpus[KAFKA]
    assert result.stderr == ""


@pytest.mark.parametrize("form", ["2026-09-02-kafka.md", "absolute"])
def test_show_accepts_other_id_forms(runner: CliRunner, vault: Path, corpus: dict[str, str], form: str) -> None:
    note_id = str(vault / KAFKA) if form == "absolute" else form

    result = runner.invoke(cli, ["show", note_id])

    assert result.exit_code == 0, result.output
    assert result.stdout == corpus[KAFKA]


def test_show_json(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    result = runner.invoke(cli, ["show", KAFKA, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "path": KAFKA,
        "abs_path": str(vault / KAFKA),
        "kind": "decision",
        "status": "active",
        "effective_status": "active",
        "title": "Project Atlas task updates over Kafka",
        "created_date": "2026-09-02",
        "schedule": None,
        "paths": ["~/Dev/exampleco"],
        "tags": ["ATLAS-27", "sync-worker"],
        "keywords": ["Atlas", "Project Atlas"],
        "references": [],
        "unread": 0,
        "related": [{"relation": "supersedes", "note": OLD}],
        "body": (
            "\n# Project Atlas task updates over Kafka\n\n**Decision:** Kafka.\n\n**Rationale:** Fewer moving parts.\n"
        ),
    }


def test_show_json_reports_the_derived_status_the_schedule_and_unread_deliveries(
    runner: CliRunner, vault: Path, corpus: dict[str, str]
) -> None:
    deliver(vault, PLANTS, "2026-09-09T10:00:00+03:00")
    deliver(vault, PLANTS, "2026-09-12T10:00:00+03:00")
    deliver(vault, PLANTS, "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")

    old = json.loads(runner.invoke(cli, ["show", OLD, "--json"]).stdout)
    plants = json.loads(runner.invoke(cli, ["show", PLANTS, "--json"]).stdout)

    assert (old["status"], old["effective_status"], old["related"]) == ("active", "superseded", [])
    assert (plants["schedule"], plants["unread"]) == ("at 2026-09-09T10:00:00+03:00", 2)


def test_show_unknown_id_fails(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["show", "nope.md"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: no note at notes/nope.md\n"


def test_show_unknown_id_fails_as_json(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["show", "nope.md", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stderr) == {"error": {"type": "usage_error", "message": "no note at notes/nope.md"}}


def test_show_prints_an_invalid_file_verbatim_but_has_no_json_for_it(runner: CliRunner, vault: Path) -> None:
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)

    verbatim = runner.invoke(cli, ["show", bad])
    as_json = runner.invoke(cli, ["show", bad, "--json"])

    assert verbatim.exit_code == 0, verbatim.output
    assert verbatim.stdout == NO_KIND
    assert as_json.exit_code == 1
    assert json.loads(as_json.stderr) == {
        "error": {
            "type": "validation_failed",
            "message": f"{bad} is not a valid note, so it has no metadata; run `notes check`",
        }
    }


def test_show_prints_bytes_that_are_not_utf8(runner: CliRunner, vault: Path) -> None:
    raw = b"---\nkind: decision\nstatus: active\n---\n\n# Caf\xe9\n"
    (vault / "notes" / "2026-09-02-latin1.md").write_bytes(raw)

    result = runner.invoke(cli, ["show", "2026-09-02-latin1.md"])

    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == raw


# notes list: ordering and output


def test_list_orders_newest_first_then_by_path(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"decision active [{KAFKA}]({vault / KAFKA}) - Project Atlas task updates over Kafka",
        f"reminder active [{PLANTS}]({vault / PLANTS}) - Water the plants",
        f"decision superseded [{OLD}]({vault / OLD}) - Kafka over WebSocket",
        f"fact archived [{LEGACY}]({vault / LEGACY}) - Legacy fact",
        f"idea active [{IDEA}]({vault / IDEA}) - An idea",
    ]
    assert result.stderr == ""


def test_list_puts_unread_notes_first(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    deliver(vault, IDEA, "2026-09-01T09:00:00+03:00")
    deliver(vault, OLD, "2026-09-01T09:00:00+03:00")
    deliver(vault, LEGACY, "2026-09-01T09:00:00+03:00", read_at="2026-09-01T09:30:00+03:00")

    result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert listed_paths(result.stdout) == [OLD, IDEA, KAFKA, PLANTS, LEGACY]
    assert lines[0] == f"[unread] decision superseded [{OLD}]({vault / OLD}) - Kafka over WebSocket"
    assert lines[1] == f"[unread] idea active [{IDEA}]({vault / IDEA}) - An idea"
    assert "[unread]" not in "\n".join(lines[2:])


def test_list_of_an_empty_vault_prints_nothing(runner: CliRunner, vault: Path) -> None:
    human = runner.invoke(cli, ["list"])
    as_json = runner.invoke(cli, ["list", "--json"])

    assert (human.exit_code, human.stdout, human.stderr) == (0, "", "")
    assert as_json.exit_code == 0
    assert json.loads(as_json.stdout) == []


def test_list_json(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    deliver(vault, IDEA, "2026-09-01T09:00:00+03:00")

    result = runner.invoke(cli, ["list", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [item["path"] for item in data] == [IDEA, KAFKA, PLANTS, OLD, LEGACY]
    assert data[0] == {
        "path": IDEA,
        "abs_path": str(vault / IDEA),
        "kind": "idea",
        "status": "active",
        "effective_status": "active",
        "title": "An idea",
        "created_date": "2026-08-15",
        "schedule": None,
        "paths": ["~/Dev/orion"],
        "tags": [],
        "keywords": [],
        "references": [],
        "unread": 1,
    }
    assert (data[3]["status"], data[3]["effective_status"]) == ("active", "superseded")
    assert data[2]["schedule"] == "at 2026-09-09T10:00:00+03:00"


def test_list_syncs_and_commits_a_hand_written_note_first(
    runner: CliRunner, vault: Path, git_cmd: Git, corpus: dict[str, str]
) -> None:
    fresh = write(vault, "2026-09-03-fresh.md", note_text("Fresh", kind="event"))
    bad = write(vault, "2026-09-03-bad.md", NO_KIND)

    result = runner.invoke(cli, ["list", "--kind", "event"])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"event active [{fresh}]({vault / fresh}) - Fresh\n"
    assert git_cmd(vault, "log", "--format=%s", "-1").strip() == f"notes: update {fresh}"
    assert git_cmd(vault, "status", "--porcelain").splitlines() == [f"?? {bad}"]


# notes list: filters


def test_list_unread_filter(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    deliver(vault, PLANTS, "2026-09-09T10:00:00+03:00")
    deliver(vault, LEGACY, "2026-09-01T09:00:00+03:00", read_at="2026-09-01T09:30:00+03:00")

    result = runner.invoke(cli, ["list", "--unread"])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"[unread] reminder active [{PLANTS}]({vault / PLANTS}) - Water the plants\n"


def test_list_kind_filter(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    result = runner.invoke(cli, ["list", "--kind", "decision"])

    assert result.exit_code == 0, result.output
    assert listed_paths(result.stdout) == [KAFKA, OLD]


def test_list_unknown_kind_fails(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    result = runner.invoke(cli, ["list", "--kind", "recipe"])

    assert result.exit_code == 1
    assert result.stderr == (
        "Error: unknown kind `recipe` (known kinds: decision, event, fact, idea, promise, reminder)\n"
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [("active", [KAFKA, PLANTS, IDEA]), ("archived", [LEGACY]), ("superseded", [OLD])],
)
def test_list_status_filter_uses_the_effective_status(
    runner: CliRunner, vault: Path, corpus: dict[str, str], status: str, expected: list[str]
) -> None:
    result = runner.invoke(cli, ["list", "--status", status])

    assert result.exit_code == 0, result.output
    assert listed_paths(result.stdout) == expected


def test_list_archived_wins_over_superseded(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    newer = write(vault, "2026-09-03-newer.md", note_text("Newer", supersedes="2026-08-30-legacy.md"))

    superseded = runner.invoke(cli, ["list", "--status", "superseded"])
    archived = runner.invoke(cli, ["list", "--status", "archived"])

    assert listed_paths(superseded.stdout) == [OLD]
    assert listed_paths(archived.stdout) == [LEGACY]
    assert newer in listed_paths(runner.invoke(cli, ["list", "--status", "active"]).stdout)


def test_list_rejects_an_unknown_status(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["list", "--status", "unread"])

    assert result.exit_code == 2
    assert "Invalid value for '--status'" in result.stderr


def test_list_tag_filter_is_case_insensitive(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    exact = runner.invoke(cli, ["list", "--tag", "ATLAS-27"])
    lower = runner.invoke(cli, ["list", "--tag", "atlas-27"])
    none = runner.invoke(cli, ["list", "--tag", "ATLAS-28"])

    assert listed_paths(exact.stdout) == [KAFKA, OLD]
    assert listed_paths(lower.stdout) == [KAFKA, OLD]
    assert (none.exit_code, none.stdout) == (0, "")


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("~/Dev/exampleco/project-atlas", [KAFKA, OLD]),
        ("~/Dev/exampleco/project-atlas/src/deep", [KAFKA, OLD]),
        ("~/Dev/exampleco", [KAFKA]),
        ("~/Dev/exampleco/other", [KAFKA]),
        ("~/Dev", []),
        ("~/Dev/orion", [IDEA]),
        ("~/Dev/orion-2", []),
    ],
)
def test_list_path_filter_expands_home_and_matches_subdirectories(
    runner: CliRunner, vault: Path, corpus: dict[str, str], given: str, expected: list[str]
) -> None:
    result = runner.invoke(cli, ["list", "--path", given])

    assert result.exit_code == 0, result.output
    assert listed_paths(result.stdout) == expected


def test_list_path_filter_accepts_absolute_and_relative_paths(
    runner: CliRunner, home: Path, vault: Path, corpus: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = home / "Dev" / "exampleco" / "project-atlas" / "src"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)

    absolute = runner.invoke(cli, ["list", "--path", str(project)])
    relative = runner.invoke(cli, ["list", "--path", "."])
    parent = runner.invoke(cli, ["list", "--path", "../.."])

    assert listed_paths(absolute.stdout) == [KAFKA, OLD]
    assert listed_paths(relative.stdout) == [KAFKA, OLD]
    assert listed_paths(parent.stdout) == [KAFKA]


def test_list_filters_combine_with_and(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    deliver(vault, OLD, "2026-09-01T09:00:00+03:00")
    deliver(vault, KAFKA, "2026-09-01T09:00:00+03:00")

    every_filter = ["--unread", "--kind", "decision", "--status", "superseded", "--tag", "atlas-27"]
    result = runner.invoke(cli, ["list", *every_filter, "--path", "~/Dev/exampleco/project-atlas"])
    without_unread = runner.invoke(cli, ["list", "--kind", "decision", "--status", "active", "--tag", "sync-worker"])
    contradiction = runner.invoke(cli, ["list", "--kind", "fact", "--status", "active"])

    assert listed_paths(result.stdout) == [OLD]
    assert listed_paths(without_unread.stdout) == [KAFKA]
    assert (contradiction.exit_code, contradiction.stdout) == (0, "")


def test_list_includes_notes_in_subdirectories(runner: CliRunner, vault: Path, corpus: dict[str, str]) -> None:
    nested = write(vault, "archive/2026-07-01-nested.md", note_text("Nested", kind="fact"))

    result = runner.invoke(cli, ["list", "--kind", "fact"])

    assert result.exit_code == 0, result.output
    assert listed_paths(result.stdout) == [LEGACY, nested]


# Registration


def test_show_and_list_are_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "  show " in result.output
    assert "  list " in result.output


@pytest.mark.parametrize("command", ["show", "list"])
def test_help_needs_no_vault(runner: CliRunner, home: Path, command: str) -> None:
    result = runner.invoke(cli, [command, "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert not (home / ".notes").exists()


@pytest.mark.parametrize("args", [["show", "x.md"], ["list"]])
def test_commands_fail_without_a_vault(runner: CliRunner, home: Path, args: list[str]) -> None:
    result = runner.invoke(cli, args)

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"
