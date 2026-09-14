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


# Colour


def test_colour_follows_no_color_force_color_then_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout", io.StringIO())
    assert output.colors_enabled() is False

    monkeypatch.setenv("FORCE_COLOR", "1")
    assert output.colors_enabled() is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert output.colors_enabled() is False


def test_style_helpers_are_plain_without_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    assert output.style("x", fg="red") == "x"
    assert output.style_kind("idea") == "idea"
    assert output.style_status("active") == "active"
    assert output.style_unread("[unread]") == "[unread]"
    assert output.style_reasons("[text]") == "[text]"
    assert output.style_label("delivered:") == "delivered:"
    assert output.render_link(Path("/h"), "notes/a.md", "T") == "[notes/a.md](/h/notes/a.md) - T"


def test_style_helpers_colour_each_field_under_force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert output.style_kind("idea") == click.style("idea", fg="cyan")
    assert output.style_status("active") == click.style("active", fg="green")
    assert output.style_status("archived  ") == click.style("archived  ", fg="bright_black")
    assert output.style_status("superseded") == click.style("superseded", fg="magenta")
    assert output.style_status("unknown") == "unknown"
    assert output.style_unread("[unread]") == click.style("[unread]", fg="yellow", bold=True)
    assert output.style_reasons("[text]") == click.style("[text]", fg="blue")
    assert output.style_label("delivered:") == click.style("delivered:", fg="green", bold=True)


def test_render_link_on_a_terminal_is_title_then_dim_id_hyperlinked_to_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path is carried by an OSC 8 hyperlink rather than printed, so the line stays short and the title leads."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    link = output.render_link(Path("/h/.notes"), "notes/2026-09-02-задачи.md", "Обновления задач")

    uri = "file:///h/.notes/notes/2026-09-02-%D0%B7%D0%B0%D0%B4%D0%B0%D1%87%D0%B8.md"
    assert link == (
        f"\x1b]8;;{uri}\x1b\\{click.style('Обновления задач', bold=True)}\x1b]8;;\x1b\\"
        f"  \x1b]8;;{uri}\x1b\\{click.style('notes/2026-09-02-задачи.md', fg='bright_black')}\x1b]8;;\x1b\\"
    )
    assert "/h/.notes/notes/2026-09-02-задачи.md" not in link


def test_hyperlink_wraps_the_text_in_osc_8() -> None:
    assert output.hyperlink("x", "file:///a") == "\x1b]8;;file:///a\x1b\\x\x1b]8;;\x1b\\"


def test_emit_keeps_the_colour_under_force_color_even_off_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    output.emit(context(False), output.style_kind("idea"), {})

    assert capsys.readouterr().out == click.style("idea", fg="cyan") + "\n"


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
