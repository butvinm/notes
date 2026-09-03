"""Tests for `notes recall` on the corpus: the unread, exact, and text sections in order, the path boost, the score
threshold, the cap, the JSON shape, and the guarantees the hook relies on (no push, no `pymorphy3` for Latin).

Unread state is made by inserting rows into `deliveries` directly, since `notes tick` arrives in a later task."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes import textnorm
from notes.cli import cli
from tests.conftest import Corpus, RecordedCommands, deliver

Git = Callable[..., str]

QUESTION = "why doesn't Project Atlas communicate with the backend over WebSocket"
UNREAD = "Unread notes:"
RELATED = "Related notes:"

SEPTEMBER_9 = "2026-09-09T10:00:00+03:00"


def line(vault: Path, reasons: str, path: str, title: str) -> str:
    """One recall line as printed: `- [reasons] [id](abs) - Title`."""
    return f"- [{reasons}] [{path}]({vault / path}) - {title}"


def found(output: str) -> list[str]:
    """The IDs in the order `notes recall` printed them, headings skipped."""
    return [entry.split("] [", 1)[1].split("]", 1)[0] for entry in output.splitlines() if entry.startswith("- [")]


def reasons_by_id(output: str) -> dict[str, str]:
    """The bracketed reasons of every printed line, by ID."""
    entries = [entry for entry in output.splitlines() if entry.startswith("- [")]
    return dict(zip(found(output), (entry.split("] [", 1)[0].removeprefix("- [") for entry in entries), strict=True))


def headings(output: str) -> list[str]:
    return [entry for entry in output.splitlines() if not entry.startswith("- [")]


# Sections and their order


def test_recall_prints_the_unread_block_then_the_related_block(runner: CliRunner, search_corpus: Path) -> None:
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9)

    result = runner.invoke(cli, ["recall", "ATLAS-27"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines[:3] == [UNREAD, line(search_corpus, "unread", Corpus.PLANTS, "Water the plants"), RELATED]
    assert set(found(result.stdout)[1:3]) == {Corpus.KAFKA, Corpus.WEBSOCKET}
    assert found(result.stdout)[3:] == [Corpus.RUSSIAN_KAFKA]
    assert reasons_by_id(result.stdout) == {
        Corpus.PLANTS: "unread",
        Corpus.KAFKA: "issue: ATLAS-27, text",
        Corpus.WEBSOCKET: "issue: ATLAS-27, text, superseded",
        Corpus.RUSSIAN_KAFKA: "text",
    }
    assert line(search_corpus, "issue: ATLAS-27, text", Corpus.KAFKA, "Project Atlas task updates over Kafka") in lines


def test_recall_lists_unread_notes_first_whatever_the_query(runner: CliRunner, search_corpus: Path) -> None:
    deliver(search_corpus, Corpus.STANDUP, "2026-07-01T10:30:00+03:00")
    expected = [UNREAD, line(search_corpus, "unread", Corpus.STANDUP, "Daily standup moved to 10:30")]

    with_matches = runner.invoke(cli, ["recall", "ATLAS-27"])
    unrelated = runner.invoke(cli, ["recall", "quantum entanglement"])
    without_query = runner.invoke(cli, ["recall"])

    for result in (with_matches, unrelated, without_query):
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[:2] == expected
    assert headings(with_matches.stdout) == [UNREAD, RELATED]
    assert unrelated.stdout == without_query.stdout == "\n".join(expected) + "\n"


def test_recall_orders_unread_notes_by_newest_delivery_and_lists_each_once(
    runner: CliRunner, search_corpus: Path
) -> None:
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9)
    deliver(search_corpus, Corpus.PLANTS, "2026-09-12T10:00:00+03:00")
    deliver(search_corpus, Corpus.LIPS, "2026-09-15T18:00:00+03:00")
    deliver(search_corpus, Corpus.ORION, "2026-09-01T09:00:00+03:00")
    deliver(search_corpus, Corpus.STANDUP, "2026-09-16T09:00:00+03:00", read_at="2026-09-16T09:30:00+03:00")
    deliver(search_corpus, "notes/2026-01-01-gone.md", "2026-09-17T09:00:00+03:00")

    result = runner.invoke(cli, ["recall"])

    assert result.exit_code == 0, result.output
    assert found(result.stdout) == [Corpus.LIPS, Corpus.PLANTS, Corpus.ORION]


def test_recall_compares_delivery_times_as_instants_not_text(runner: CliRunner, search_corpus: Path) -> None:
    # 16:00 UTC is 19:00 in UTC+3, later than 18:00+03:00 although it sorts earlier as text.
    deliver(search_corpus, Corpus.LIPS, "2026-09-15T18:00:00+03:00")
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9, delivered_at="2026-09-15T16:00:00+00:00")

    result = runner.invoke(cli, ["recall"])

    assert found(result.stdout) == [Corpus.PLANTS, Corpus.LIPS]


def test_recall_marks_the_status_of_an_unread_note_that_is_no_longer_active(
    runner: CliRunner, search_corpus: Path
) -> None:
    deliver(search_corpus, Corpus.WEBSOCKET, "2026-09-01T09:00:00+03:00")
    deliver(search_corpus, Corpus.LEGACY, "2026-09-01T08:00:00+03:00")

    result = runner.invoke(cli, ["recall"])

    assert reasons_by_id(result.stdout) == {Corpus.WEBSOCKET: "unread, superseded", Corpus.LEGACY: "unread, archived"}


def test_recall_never_repeats_an_unread_note_among_the_related_ones(runner: CliRunner, search_corpus: Path) -> None:
    deliver(search_corpus, Corpus.KAFKA, "2026-09-01T09:00:00+03:00")

    result = runner.invoke(cli, ["recall", "ATLAS-27"])

    assert found(result.stdout).count(Corpus.KAFKA) == 1
    assert found(result.stdout)[0] == Corpus.KAFKA
    assert reasons_by_id(result.stdout)[Corpus.KAFKA] == "unread"
    assert set(found(result.stdout)[1:]) == {Corpus.WEBSOCKET, Corpus.RUSSIAN_KAFKA}


# Exact matches


def test_recall_lists_an_exact_issue_match_on_a_superseded_note_with_both_reasons(
    runner: CliRunner, search_corpus: Path
) -> None:
    result = runner.invoke(cli, ["recall", "ATLAS-27"])

    assert result.exit_code == 0, result.output
    assert headings(result.stdout) == [RELATED]
    assert reasons_by_id(result.stdout)[Corpus.WEBSOCKET] == "issue: ATLAS-27, text, superseded"


def test_recall_lists_an_exact_tag_match_on_an_archived_note(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["recall", "legacy"])

    assert result.stdout == (
        f"{RELATED}\n"
        + line(
            search_corpus, "tag: legacy, text, archived", Corpus.LEGACY, "Legacy proxy drops idle WebSocket connections"
        )
        + "\n"
    )


def test_recall_puts_exact_matches_before_better_scoring_text_matches(runner: CliRunner, search_corpus: Path) -> None:
    # By text alone the Kafka decision scores highest for `Kafka updates`; the two notes tagged `kafka` come first.
    result = runner.invoke(cli, ["recall", "Kafka updates"])

    assert found(result.stdout) == [Corpus.RETENTION, Corpus.RUSSIAN_KAFKA, Corpus.KAFKA]
    assert reasons_by_id(result.stdout) == {
        Corpus.RETENTION: "tag: kafka, text",
        Corpus.RUSSIAN_KAFKA: "tag: kafka, text",
        Corpus.KAFKA: "text",
    }


# Text matches: threshold and status


def test_recall_omits_a_text_match_below_min_score(runner: CliRunner, search_corpus: Path) -> None:
    # `Kafka updates` scores the FTS5 fact at about 0.67 (`updates` stems to `update`), below the default 1.0.
    recalled = runner.invoke(cli, ["recall", "Kafka updates"])
    searched = runner.invoke(cli, ["search", "Kafka updates"])

    assert Corpus.FTS not in found(recalled.stdout)
    assert Corpus.FTS in searched.stdout


def test_recall_reads_min_score_from_the_config(runner: CliRunner, search_corpus: Path) -> None:
    (search_corpus / "config.toml").write_text("[recall]\nmin_score = 3.0\n", encoding="utf-8")

    text_only = runner.invoke(cli, ["recall", "replay"])
    exact = runner.invoke(cli, ["recall", "ATLAS-27"])

    assert (text_only.exit_code, text_only.stdout) == (0, "")
    assert set(found(exact.stdout)) == {Corpus.KAFKA, Corpus.WEBSOCKET}


def test_recall_excludes_archived_and_superseded_notes_from_text_matches(
    runner: CliRunner, search_corpus: Path
) -> None:
    websocket = runner.invoke(cli, ["recall", "WebSocket"])
    nginx = runner.invoke(cli, ["recall", "nginx"])

    assert found(websocket.stdout) == [Corpus.KAFKA]
    assert (nginx.exit_code, nginx.stdout, nginx.stderr) == (0, "", "")


# --cwd


def test_recall_cwd_inside_a_paths_entry_boosts_a_text_match(runner: CliRunner, search_corpus: Path) -> None:
    # Text ranks Kafka first; only the retention fact covers `~/Dev/exampleco`, so the path boost reverses them.
    plain = runner.invoke(cli, ["recall", "replay"])
    boosted = runner.invoke(cli, ["recall", "replay", "--cwd", "~/Dev/exampleco"])

    assert found(plain.stdout) == [Corpus.KAFKA, Corpus.RETENTION]
    assert reasons_by_id(plain.stdout) == {Corpus.KAFKA: "text", Corpus.RETENTION: "text"}
    assert found(boosted.stdout) == [Corpus.RETENTION, Corpus.KAFKA]
    assert reasons_by_id(boosted.stdout) == {Corpus.RETENTION: "text, path", Corpus.KAFKA: "text"}


def test_recall_cwd_boost_lifts_a_text_match_over_min_score(runner: CliRunner, search_corpus: Path) -> None:
    (search_corpus / "config.toml").write_text("[recall]\nmin_score = 1.5\n", encoding="utf-8")

    plain = runner.invoke(cli, ["recall", "replay"])
    boosted = runner.invoke(cli, ["recall", "replay", "--cwd", "~/Dev/exampleco/deep/inside"])

    assert found(plain.stdout) == [Corpus.KAFKA]
    assert found(boosted.stdout) == [Corpus.RETENTION, Corpus.KAFKA]


def test_recall_cwd_alone_never_makes_a_note_a_candidate(runner: CliRunner, search_corpus: Path) -> None:
    with_query = runner.invoke(cli, ["recall", "kafka", "--cwd", "~/Dev/orion"])
    without_query = runner.invoke(cli, ["recall", "--cwd", "~/Dev/orion"])

    assert Corpus.ORION not in found(with_query.stdout)
    assert len(found(with_query.stdout)) == 3
    assert "path" not in "".join(reasons_by_id(with_query.stdout).values())
    assert (without_query.exit_code, without_query.stdout) == (0, "")


def test_recall_cwd_defaults_to_the_current_directory(
    runner: CliRunner, home: Path, search_corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = home / "Dev" / "exampleco"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)

    result = runner.invoke(cli, ["recall", "replay"])

    assert found(result.stdout) == [Corpus.RETENTION, Corpus.KAFKA]
    assert reasons_by_id(result.stdout)[Corpus.RETENTION] == "text, path"


# The cap


def test_recall_caps_the_block_at_the_configured_limit_deterministically(
    runner: CliRunner, search_corpus: Path
) -> None:
    (search_corpus / "config.toml").write_text("[recall]\nlimit = 2\n", encoding="utf-8")
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9)

    first = runner.invoke(cli, ["recall", "kafka"])
    second = runner.invoke(cli, ["recall", "kafka"])
    best_by_search = found(runner.invoke(cli, ["search", "kafka", "--limit", "1"]).stdout.replace("[", "- [", 1))

    assert first.exit_code == 0, first.output
    assert found(first.stdout) == [Corpus.PLANTS, *best_by_search]
    assert first.stdout == second.stdout


def test_recall_limit_spent_on_unread_notes_leaves_no_room_for_related_ones(
    runner: CliRunner, search_corpus: Path
) -> None:
    (search_corpus / "config.toml").write_text("[recall]\nlimit = 2\n", encoding="utf-8")
    deliver(search_corpus, Corpus.PLANTS, "2026-09-03T09:00:00+03:00")
    deliver(search_corpus, Corpus.LIPS, "2026-09-02T09:00:00+03:00")
    deliver(search_corpus, Corpus.STANDUP, "2026-09-01T09:00:00+03:00")

    result = runner.invoke(cli, ["recall", "kafka"])

    assert found(result.stdout) == [Corpus.PLANTS, Corpus.LIPS]
    assert headings(result.stdout) == [UNREAD]


# Empty output


def test_recall_prints_nothing_for_an_unrelated_prompt(runner: CliRunner, search_corpus: Path) -> None:
    human = runner.invoke(cli, ["recall", "quantum entanglement"])
    as_json = runner.invoke(cli, ["recall", "quantum entanglement", "--json"])

    assert (human.exit_code, human.stdout, human.stderr) == (0, "", "")
    assert as_json.exit_code == 0
    assert json.loads(as_json.stdout) == []


@pytest.mark.parametrize("args", [[], [""], ["the of and"]])
def test_recall_prints_nothing_without_a_query_or_an_unread_note(
    runner: CliRunner, search_corpus: Path, args: list[str]
) -> None:
    result = runner.invoke(cli, ["recall", *args])

    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")


def test_recall_of_an_empty_vault_prints_nothing(runner: CliRunner, vault: Path) -> None:
    human = runner.invoke(cli, ["recall", "kafka"])
    as_json = runner.invoke(cli, ["recall", "--json"])

    assert (human.exit_code, human.stdout, human.stderr) == (0, "", "")
    assert json.loads(as_json.stdout) == []


# Prompts as the hook sends them


def test_recall_handles_a_long_natural_language_prompt_with_stopwords(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["recall", "--cwd", "~/Dev/notes", "--", QUESTION])

    assert result.exit_code == 0, result.output
    assert found(result.stdout) == [Corpus.KAFKA, Corpus.RUSSIAN_KAFKA]
    assert reasons_by_id(result.stdout) == {Corpus.KAFKA: "text", Corpus.RUSSIAN_KAFKA: "text"}


def test_recall_accepts_a_prompt_starting_with_a_dash_after_the_separator(
    runner: CliRunner, search_corpus: Path
) -> None:
    result = runner.invoke(cli, ["recall", "--", "- what about the standup?"])

    assert result.exit_code == 0, result.output
    assert found(result.stdout) == [Corpus.STANDUP]


def test_recall_finds_a_russian_prompt_through_another_inflection(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["recall", "что мы решили про словоформы?"])

    assert found(result.stdout) == [Corpus.LEMMAS]


def test_recall_with_a_latin_query_never_loads_pymorphy3(
    runner: CliRunner, search_corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> None:
        raise AssertionError("pymorphy3 was loaded for a Latin-only query")

    monkeypatch.setattr(textnorm, "analyzer", refuse)

    latin = runner.invoke(cli, ["recall", "ATLAS-27 replay", "--cwd", "~/Dev/exampleco"])
    cyrillic = runner.invoke(cli, ["recall", "невиданноеслово"])

    assert latin.exit_code == 0, latin.output
    assert latin.exception is None
    assert set(found(latin.stdout)[:2]) == {Corpus.KAFKA, Corpus.WEBSOCKET}
    assert isinstance(cyrillic.exception, AssertionError)


# Git: commit, never push


def test_recall_commits_a_pending_edit_but_never_pushes(
    runner: CliRunner, vault: Path, remote: Path, git_cmd: Git, recorded_commands: RecordedCommands
) -> None:
    recorded_commands.passthrough("git")
    git_cmd(vault, "remote", "add", "origin", str(remote))
    (vault / "config.toml").write_text("[git]\nauto_push = true\n", encoding="utf-8")
    fresh = "notes/2026-09-02-fresh.md"
    (vault / fresh).write_text(
        "---\nkind: fact\nstatus: active\ntags: [PROJ-1]\n---\n\n# A fresh unicorn fact\n\nUnicorns are rare.\n",
        encoding="utf-8",
    )

    result = runner.invoke(cli, ["recall", "PROJ-1"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert found(result.stdout) == [fresh]
    assert git_cmd(vault, "log", "--format=%s", "-1").strip() == "notes: sync 2 files"
    assert fresh in git_cmd(vault, "ls-files").splitlines()
    assert recorded_commands.commands("git", "push") == []
    assert git_cmd(remote, "for-each-ref", "refs/heads/master") == ""

    later = runner.invoke(cli, ["sync"])

    assert later.exit_code == 0, later.output
    assert len(recorded_commands.commands("git", "push")) == 1
    assert git_cmd(remote, "for-each-ref", "refs/heads/master") != ""


# JSON


def test_recall_json(runner: CliRunner, search_corpus: Path) -> None:
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9)

    result = runner.invoke(cli, ["recall", "legacy", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "section": "unread",
            "reasons": ["unread"],
            "path": Corpus.PLANTS,
            "abs_path": str(search_corpus / Corpus.PLANTS),
            "title": "Water the plants",
        },
        {
            "section": "related",
            "reasons": ["tag: legacy", "text", "archived"],
            "path": Corpus.LEGACY,
            "abs_path": str(search_corpus / Corpus.LEGACY),
            "title": "Legacy proxy drops idle WebSocket connections",
        },
    ]


# Registration


def test_recall_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "  recall " in result.output


def test_recall_help_needs_no_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["recall", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert "--cwd PATH" in result.output
    assert "[QUERY]" in result.output
    assert not (home / ".notes").exists()


def test_recall_fails_without_a_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["recall", "kafka"])

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"


def test_recall_takes_at_most_one_query_argument(runner: CliRunner, search_corpus: Path) -> None:
    result = runner.invoke(cli, ["recall", "a", "b"])

    assert result.exit_code == 2
