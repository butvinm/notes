"""Tests for the generator: prompt composition, running the configured command, and parsing its envelope."""

import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from notes import db, generator, prompts
from notes.config import GeneratorConfig
from notes.errors import GeneratorError, UsageError
from notes.generator import Envelope
from tests.conftest import FakeGenerator, RecordedCommands

MARKDOWN = "---\nkind: decision\nstatus: active\n---\n\n# Kafka task updates\n\n**Decision:** Kafka.\n"
ENVELOPE = Envelope("kafka task updates", MARKDOWN)
ENVELOPE_JSON = json.dumps({"short_name": ENVELOPE.short_name, "markdown": ENVELOPE.markdown})

CORPUS_TAGS = [
    "ATLAS-27",
    "ATLAS-31",
    "home",
    "infra",
    "kafka",
    "legacy",
    "lips",
    "LIPS-12",
    "orion",
    "project-atlas",
    "proxy",
    "scheduler",
    "search",
    "sqlite",
    "sync-worker",
    "team",
    "поиск",
]
KINDS = ["decision", "fact", "idea", "reminder"]


def listing(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


@pytest.fixture
def conn(vault: Path) -> Iterator[sqlite3.Connection]:
    connection = db.open_index(vault)
    yield connection
    connection.close()


# compose_prompt


def test_compose_prompt_orders_every_part_and_lists_existing_tags(
    search_corpus: Path, conn: sqlite3.Connection
) -> None:
    vault = search_corpus
    kind_prompt = (vault / "types" / "decision" / "prompt.md").read_text(encoding="utf-8").strip()
    template = (vault / "types" / "decision" / "template.md").read_text(encoding="utf-8").strip()
    context = {"facts": ["Task updates go over Kafka"], "cwd": "~/Dev/exampleco", "заметка": "по-русски"}

    prompt = generator.compose_prompt(
        vault, conn, "decision", context=context, previous=MARKDOWN, feedback="Shorter, please.\n"
    )

    markers = [
        "# Drafting a note\n",
        kind_prompt,
        f"# Template\n\n```markdown\n{template}\n```",
        f"# Existing tags\n\n{listing(CORPUS_TAGS)}\n\n",
        f"# Existing kinds\n\n{listing(KINDS)}\n\n",
        '# Context\n\n```json\n{\n  "facts": [\n    "Task updates go over Kafka"\n  ],\n  "cwd": "~/Dev/exampleco",\n',
        '  "заметка": "по-русски"\n}\n```\n\n',
        f"# Previous draft\n\n{generator.REVISION_INSTRUCTION}\n\n```markdown\n{MARKDOWN.rstrip()}\n```\n\n",
        "# Feedback\n\nShorter, please.",
    ]
    positions = [prompt.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert prompt.endswith("# Feedback\n\nShorter, please.")


def test_compose_prompt_without_context_previous_or_feedback_ends_with_the_vault_lists(
    vault: Path, conn: sqlite3.Connection
) -> None:
    expected = "\n\n".join(
        [
            prompts.compose(prompts.load_parts(vault, "fact")),
            "# Existing tags\n\nThe vault has no tags yet.",
            f"# Existing kinds\n\n{listing(KINDS)}",
        ]
    )

    assert generator.compose_prompt(vault, conn, "fact") == expected


def test_compose_prompt_skips_blank_feedback(vault: Path, conn: sqlite3.Connection) -> None:
    prompt = generator.compose_prompt(vault, conn, "fact", previous="# Old\n", feedback="  \n")

    assert "# Previous draft" in prompt
    assert "# Feedback" not in prompt


def test_compose_prompt_fences_a_context_that_contains_backticks(vault: Path, conn: sqlite3.Connection) -> None:
    prompt = generator.compose_prompt(vault, conn, "fact", context={"snippet": "```json\n{}\n```"})

    assert '# Context\n\n````json\n{\n  "snippet": "```json\\n{}\\n```"\n}\n````' in prompt


def test_compose_prompt_rejects_an_unknown_kind(vault: Path, conn: sqlite3.Connection) -> None:
    with pytest.raises(UsageError, match=r"unknown kind `recipe`"):
        generator.compose_prompt(vault, conn, "recipe")


# run


def test_run_sends_the_prompt_on_stdin_and_returns_stdout(fake_generator: FakeGenerator) -> None:
    fake_generator.reply(ENVELOPE.short_name, ENVELOPE.markdown)

    stdout = generator.run(fake_generator.config, "the prompt")

    assert generator.parse_envelope(stdout) == ENVELOPE
    assert fake_generator.prompts == ["the prompt"]


def test_run_keeps_every_argument_whole_and_literal(fake_generator: FakeGenerator) -> None:
    fake_generator.reply(ENVELOPE.short_name, ENVELOPE.markdown)
    config = GeneratorConfig(command=(*fake_generator.command, "two words", "$HOME *"))

    generator.run(config, "prompt")

    assert fake_generator.args == ["two words", "$HOME *"]


def test_run_reports_a_non_zero_exit_with_the_stderr_tail(
    fake_generator: FakeGenerator, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_generator.fail(2, "first line\n\n  second line\nthird line\nlast line\n")

    with pytest.raises(GeneratorError) as info:
        generator.run(fake_generator.config, "prompt")

    assert info.value.message == f"generator {sys.executable} exited with status 2: second line third line last line"
    assert capsys.readouterr().err == "first line\n\n  second line\nthird line\nlast line\n"


def test_run_reports_a_failure_that_printed_nothing(fake_generator: FakeGenerator) -> None:
    fake_generator.fail(1)

    with pytest.raises(GeneratorError, match=r"exited with status 1: it printed no error output$"):
        generator.run(fake_generator.config, "prompt")


def test_run_forwards_the_generator_stderr_on_success(
    fake_generator: FakeGenerator, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_generator.stderr("thinking...")
    fake_generator.reply(ENVELOPE.short_name, ENVELOPE.markdown)

    stdout = generator.run(fake_generator.config, "prompt")

    assert generator.parse_envelope(stdout) == ENVELOPE
    assert capsys.readouterr().err == "thinking...\n"


def test_run_times_out(fake_generator: FakeGenerator) -> None:
    fake_generator.sleep(30)
    config = GeneratorConfig(command=fake_generator.command, timeout_seconds=0.3)

    with pytest.raises(GeneratorError, match=rf"generator {sys.executable} timed out after 0\.3 seconds$"):
        generator.run(config, "prompt")


def test_run_reports_a_missing_command() -> None:
    config = GeneratorConfig(command=("notes-no-such-generator", "exec"))

    with pytest.raises(GeneratorError) as info:
        generator.run(config, "prompt")

    assert info.value.message == (
        "generator command not found: notes-no-such-generator (set [generator] command in config.toml)"
    )


def test_run_passes_the_command_prompt_and_timeout_to_run_command(recorded_commands: RecordedCommands) -> None:
    recorded_commands.script("gen", stdout="{}")

    assert generator.run(GeneratorConfig(command=("gen", "--flag"), timeout_seconds=42), "prompt") == "{}"

    (call,) = recorded_commands.calls
    assert call.command == ("gen", "--flag")
    assert call.input == "prompt"
    assert call.timeout == 42
    assert call.capture is True
    assert call.cwd is None
    assert call.env is None


def test_run_without_a_timeout_when_the_configured_one_is_zero(recorded_commands: RecordedCommands) -> None:
    generator.run(GeneratorConfig(command=("gen",), timeout_seconds=0), "prompt")

    assert recorded_commands.calls[0].timeout is None


# parse_envelope


def test_parse_envelope_accepts_the_whole_output_as_json() -> None:
    assert generator.parse_envelope(f"\n{ENVELOPE_JSON}\n\n") == ENVELOPE


def test_parse_envelope_trims_the_short_name_and_ignores_extra_keys() -> None:
    text = json.dumps({"short_name": "  a short name  ", "markdown": "# T\n", "confidence": 0.9})

    assert generator.parse_envelope(text) == Envelope("a short name", "# T\n")


def test_parse_envelope_accepts_a_fenced_json_block_between_chatter() -> None:
    text = f"Here is the note:\n\n```json\n{ENVELOPE_JSON}\n```\n\nLet me know if it needs changes.\n"

    assert generator.parse_envelope(text) == ENVELOPE


def test_parse_envelope_accepts_a_longer_fence() -> None:
    assert generator.parse_envelope(f"````json\n{ENVELOPE_JSON}\n````") == ENVELOPE


def test_parse_envelope_takes_the_first_fenced_block() -> None:
    first = json.dumps({"short_name": "first", "markdown": "# First"})
    second = json.dumps({"short_name": "second", "markdown": "# Second"})

    assert generator.parse_envelope(f"```json\n{first}\n```\n```json\n{second}\n```") == Envelope("first", "# First")


def test_parse_envelope_accepts_json_after_chatter() -> None:
    assert generator.parse_envelope(f"Sure! Here you go: {ENVELOPE_JSON} Done.") == ENVELOPE


def test_parse_envelope_takes_the_last_top_level_object_with_markdown() -> None:
    text = (
        "Use {title} in the template. "
        '{"note": "no markdown here"} '
        '{"short_name": "a", "markdown": "# A"} then '
        '{"short_name": "b", "markdown": "# B with } brace and {\\"nested\\": {}}"}'
    )

    assert generator.parse_envelope(text) == Envelope("b", '# B with } brace and {"nested": {}}')


def test_parse_envelope_falls_through_a_fenced_block_that_is_not_an_object() -> None:
    assert generator.parse_envelope(f"```json\n[1, 2]\n```\n{ENVELOPE_JSON}") == ENVELOPE


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[1, 2]", "the generator returned a JSON array instead of a JSON object"),
        ('"just text"', "the generator returned a JSON string instead of a JSON object"),
        ("null", "the generator returned null instead of a JSON object"),
        ("42", "the generator returned a number instead of a JSON object"),
        ("true", "the generator returned a boolean instead of a JSON object"),
        ("", "the generator printed nothing"),
        ("  \n", "the generator printed nothing"),
        ("I could not draft this note.", "the generator output contains no JSON object with a `markdown` field"),
        ('{"note": "x"} and {"other": "y"}', "the generator output contains no JSON object with a `markdown` field"),
        ('{"markdown": "# T"}', "the generator envelope has no `short_name`"),
        ('{"short_name": "x"}', "the generator envelope has no `markdown`"),
        ('{"short_name": "x", "markdown": ""}', "the generator envelope's `markdown` is empty"),
        ('{"short_name": "x", "markdown": " \\n"}', "the generator envelope's `markdown` is empty"),
        ('{"short_name": "  ", "markdown": "# T"}', "the generator envelope's `short_name` is empty"),
        ('{"short_name": "x", "markdown": ["# T"]}', "the generator envelope's `markdown` must be a string"),
        ('{"short_name": 5, "markdown": "# T"}', "the generator envelope's `short_name` must be a string"),
    ],
)
def test_parse_envelope_rejects_unusable_output(text: str, message: str) -> None:
    with pytest.raises(GeneratorError) as info:
        generator.parse_envelope(text)

    assert info.value.message == message


def test_parse_envelope_does_not_scan_past_a_whole_json_object_without_markdown() -> None:
    with pytest.raises(GeneratorError, match=r"has no `markdown`"):
        generator.parse_envelope('{"short_name": "x", "text": "{\\"markdown\\": \\"# T\\"}"}')
