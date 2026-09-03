"""Tests for output rendering: links, human versus JSON emission, and the error envelope."""

import io
import json
from pathlib import Path

import click
import pytest

from notes import output
from notes.errors import GeneratorError, GitError, NotesError, NoVaultError, UsageError, ValidationFailed


def context(json_flag: bool | None) -> click.Context:
    obj = None if json_flag is None else {"json": json_flag}
    return click.Context(click.Command("probe"), obj=obj)


def test_render_link_is_id_absolute_path_and_title() -> None:
    link = output.render_link(Path("/home/user/.notes"), "notes/2026-09-02-x.md", "Kafka task updates")

    assert link == "[notes/2026-09-02-x.md](/home/user/.notes/notes/2026-09-02-x.md) - Kafka task updates"


def test_render_link_keeps_unicode() -> None:
    link = output.render_link(Path("/h/.notes"), "notes/2026-09-02-задачи.md", "Обновления задач")

    assert link == "[notes/2026-09-02-задачи.md](/h/.notes/notes/2026-09-02-задачи.md) - Обновления задач"


def test_emit_prints_human_text_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    output.emit(context(False), "hello", {"greeting": "hello"})

    assert capsys.readouterr().out == "hello\n"


def test_emit_without_flag_object_is_human(capsys: pytest.CaptureFixture[str]) -> None:
    output.emit(context(None), "hello", {"greeting": "hello"})

    assert capsys.readouterr().out == "hello\n"


def test_emit_prints_json_when_enabled(capsys: pytest.CaptureFixture[str]) -> None:
    output.emit(context(True), "hello", {"greeting": "привет", "n": 1})

    out = capsys.readouterr().out
    assert json.loads(out) == {"greeting": "привет", "n": 1}
    assert "привет" in out


def test_emit_empty_human_text_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    output.emit(context(False), "", [])

    assert capsys.readouterr().out == ""


def test_emit_json_prints_empty_collections(capsys: pytest.CaptureFixture[str]) -> None:
    output.emit(context(True), "", [])

    assert capsys.readouterr().out == "[]\n"


def test_emit_json_serializes_paths(capsys: pytest.CaptureFixture[str]) -> None:
    output.emit(context(True), "", {"path": Path("/x/y.md")})

    assert json.loads(capsys.readouterr().out) == {"path": "/x/y.md"}


def test_json_enabled_reads_parent_context() -> None:
    parent = click.Context(click.Group("notes"), obj={"json": True})
    child = click.Context(click.Command("probe"), parent=parent)

    assert output.json_enabled(child)
    assert not output.json_enabled(context(None))
    assert not output.json_enabled(context(False))


def test_emit_error_human_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    output.emit_error(NoVaultError("no vault at ~/.notes, run `notes init`"), as_json=False)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Error: no vault at ~/.notes, run `notes init`\n"


def test_emit_error_json_envelope_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    output.emit_error(NoVaultError("no vault"), as_json=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": {"type": "no_vault", "message": "no vault"}}


def test_emit_error_writes_to_given_file() -> None:
    buffer = io.StringIO()

    output.emit_error(NotesError("boom"), as_json=True, file=buffer)

    assert json.loads(buffer.getvalue()) == {"error": {"type": "error", "message": "boom"}}


@pytest.mark.parametrize(
    ("error_class", "type_name"),
    [
        (NotesError, "error"),
        (NoVaultError, "no_vault"),
        (ValidationFailed, "validation_failed"),
        (GeneratorError, "generator_error"),
        (GitError, "git_error"),
        (UsageError, "usage_error"),
    ],
)
def test_error_types(error_class: type[NotesError], type_name: str) -> None:
    error = error_class("what went wrong")

    assert isinstance(error, NotesError)
    assert error.type == type_name
    assert error.message == "what went wrong"
    assert str(error) == "what went wrong"
