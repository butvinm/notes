"""Tests for `notes search` on the corpus: ranking, reasons, status rules, `--limit`, `--all`, and the JSON shape."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes.cli import cli
from tests.conftest import Corpus

Git = Callable[..., str]

QUESTION = "why doesn't Project Atlas communicate with the backend over WebSocket"


def found(output: str) -> list[str]:
    """The IDs in the order `notes search` printed them."""
    return [line.split("] [", 1)[1].split("]", 1)[0] for line in output.splitlines()]


def reasons(output: str) -> list[str]:
    """The bracketed reasons of every printed line, in order."""
    return [line.split("] [", 1)[0].removeprefix("[") for line in output.splitlines()]


def reasons_by_id(output: str) -> dict[str, str]:
    return dict(zip(found(output), reasons(output), strict=True))


# Ranking and reasons


def test_search_finds_a_russian_note_through_another_inflection(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "словоформами"])

    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"[text] [{Corpus.LEMMAS}]({search_corpus / Corpus.LEMMAS}) - Искать заметки по леммам, а не по словоформам\n"
    )
    assert result.stderr == ""


def test_search_ranks_a_keyword_match_above_a_body_match(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "replay"])

    assert result.exit_code == 0, result.output
    assert found(result.stdout) == [Corpus.KAFKA, Corpus.RETENTION]
    assert reasons(result.stdout) == ["text", "text"]


def test_search_ranks_the_notes_tagged_with_the_issue_first(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "ATLAS-27"])

    assert result.exit_code == 0, result.output
    assert set(found(result.stdout)[:2]) == {Corpus.KAFKA, Corpus.WEBSOCKET}
    assert found(result.stdout)[2:] == [Corpus.RUSSIAN_KAFKA]
    assert reasons_by_id(result.stdout) == {
        Corpus.KAFKA: "issue: ATLAS-27, text",
        Corpus.WEBSOCKET: "issue: ATLAS-27, text, superseded",
        Corpus.RUSSIAN_KAFKA: "text",
    }
    assert (
        f"[issue: ATLAS-27, text] [{Corpus.KAFKA}]({search_corpus / Corpus.KAFKA}) - "
        "Project Atlas task updates over Kafka\n"
    ) in result.stdout


def test_search_matches_tags_and_issues_case_insensitively(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "atlas-27 SYNC-WORKER"])

    assert reasons_by_id(result.stdout)[Corpus.KAFKA] == "issue: ATLAS-27, tag: sync-worker, text"


def test_search_answers_the_question_from_the_design(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", QUESTION])

    assert result.exit_code == 0, result.output
    assert found(result.stdout) == [Corpus.KAFKA, Corpus.RUSSIAN_KAFKA]


# Status rules


def test_search_omits_superseded_and_archived_notes_by_default(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "WebSocket"])

    assert result.exit_code == 0, result.output
    assert found(result.stdout) == [Corpus.KAFKA]


def test_search_all_includes_them_with_their_status(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "WebSocket", "--all"])

    assert result.exit_code == 0, result.output
    assert reasons_by_id(result.stdout) == {
        Corpus.WEBSOCKET: "text, superseded",
        Corpus.LEGACY: "text, archived",
        Corpus.KAFKA: "text",
    }


def test_search_shows_an_archived_note_matched_by_a_tag(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "legacy"])

    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"[tag: legacy, text, archived] [{Corpus.LEGACY}]({search_corpus / Corpus.LEGACY}) - "
        "Legacy proxy drops idle WebSocket connections\n"
    )


def test_search_shows_a_superseded_note_matched_by_a_tag(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "project-atlas"])

    assert result.exit_code == 0, result.output
    assert reasons_by_id(result.stdout)[Corpus.WEBSOCKET] == "tag: project-atlas, text, superseded"
    assert reasons_by_id(result.stdout)[Corpus.KAFKA] == "tag: project-atlas, text"


# --limit


def test_search_limit(runner: CliRunner, search_corpus: Path) -> None:
    everything = runner.invoke(cli, ["search", "kafka"])
    limited = runner.invoke(cli, ["search", "kafka", "--limit", "2"])

    assert len(found(everything.stdout)) == 3
    assert found(limited.stdout) == found(everything.stdout)[:2]


def test_search_limit_counts_printed_results_not_candidates(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "WebSocket", "--limit", "1"])

    assert found(result.stdout) == [Corpus.KAFKA]


def test_search_rejects_a_limit_below_one(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "kafka", "--limit", "0"])

    assert result.exit_code == 2
    assert "Invalid value for '--limit'" in result.stderr


# Empty output


def test_search_prints_nothing_for_an_empty_query(runner: CliRunner, search_corpus: Path) -> None:
    human = runner.invoke(cli, ["search", ""])
    as_json = runner.invoke(cli, ["search", "", "--json"])

    assert (human.exit_code, human.stdout, human.stderr) == (0, "", "")
    assert as_json.exit_code == 0
    assert json.loads(as_json.stdout) == []


def test_search_prints_nothing_when_nothing_matches(runner: CliRunner, search_corpus: Path) -> None:
    human = runner.invoke(cli, ["search", "quantum entanglement"])
    as_json = runner.invoke(cli, ["search", "quantum entanglement", "--json"])

    assert (human.exit_code, human.stdout, human.stderr) == (0, "", "")
    assert json.loads(as_json.stdout) == []


def test_search_of_an_empty_vault_prints_nothing(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["search", "kafka"])

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")


# JSON


def test_search_json(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["search", "ATLAS-27", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert {item["path"] for item in data[:2]} == {Corpus.KAFKA, Corpus.WEBSOCKET}
    assert [item["path"] for item in data[2:]] == [Corpus.RUSSIAN_KAFKA]
    by_path = {item["path"]: item for item in data}
    assert by_path[Corpus.KAFKA] == {
        "path": Corpus.KAFKA,
        "abs_path": str(search_corpus / Corpus.KAFKA),
        "title": "Project Atlas task updates over Kafka",
        "effective_status": "active",
        "score": by_path[Corpus.KAFKA]["score"],
        "reasons": ["issue: ATLAS-27", "text"],
    }
    assert by_path[Corpus.WEBSOCKET]["effective_status"] == "superseded"
    assert by_path[Corpus.WEBSOCKET]["reasons"] == ["issue: ATLAS-27", "text", "superseded"]
    assert by_path[Corpus.RUSSIAN_KAFKA]["reasons"] == ["text"]
    scores = [item["score"] for item in data]
    assert all(isinstance(score, float) for score in scores)
    assert scores[0] > scores[1] > 10.0 > scores[2] > 0


# Sync before the command


def test_search_indexes_and_commits_a_hand_written_note_first(runner: CliRunner, vault: Path, git_cmd: Git) -> None:
    fresh = "notes/2026-09-03-fresh.md"
    (vault / fresh).write_text(
        "---\nkind: fact\nstatus: active\ntags: [PROJ-1]\n---\n\n# A fresh unicorn fact\n\nUnicorns are rare.\n",
        encoding="utf-8",
    )

    by_text = runner.invoke(cli, ["search", "unicorns"])
    by_issue = runner.invoke(cli, ["search", "PROJ-1"])

    assert found(by_text.stdout) == [fresh]
    assert reasons(by_issue.stdout) == ["issue: PROJ-1, text"]
    assert git_cmd(vault, "log", "--format=%s", "-1").strip() == f"notes: update {fresh}"


# Registration


def test_search_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "  search " in result.output


def test_search_help_needs_no_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["search", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert "--all" in result.output
    assert "[default: 20; x>=1]" in result.output
    assert not (home / ".notes").exists()


def test_search_fails_without_a_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["search", "kafka"])

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"


@pytest.mark.parametrize("args", [["search"], ["search", "a", "b"]])
def test_search_takes_exactly_one_query_argument(runner: CliRunner, search_corpus: Path, args: list[str]) -> None:
    result = runner.invoke(cli, args)

    assert result.exit_code == 2
