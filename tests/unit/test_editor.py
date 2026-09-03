"""Tests for the editor seam: `$VISUAL` then `$EDITOR`, run through `run_command` with the terminal inherited."""

from pathlib import Path

import pytest

from notes import editor
from notes.errors import EditorError
from tests.conftest import RecordedCommands


@pytest.fixture
def no_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)


def test_visual_wins_over_editor(monkeypatch: pytest.MonkeyPatch, no_editor: None) -> None:
    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "nano")

    assert editor.editor_command() == ["code", "--wait"]


def test_editor_is_used_when_visual_is_unset_or_blank(monkeypatch: pytest.MonkeyPatch, no_editor: None) -> None:
    monkeypatch.setenv("EDITOR", "nano")
    assert editor.editor_command() == ["nano"]

    monkeypatch.setenv("VISUAL", "   ")
    assert editor.editor_command() == ["nano"]


def test_quoted_arguments_stay_together(monkeypatch: pytest.MonkeyPatch, no_editor: None) -> None:
    monkeypatch.setenv("EDITOR", "'/opt/my editor/bin/edit' --wait")

    assert editor.editor_command() == ["/opt/my editor/bin/edit", "--wait"]


def test_no_editor_configured(no_editor: None) -> None:
    assert editor.editor_command() is None

    with pytest.raises(EditorError, match=r"no editor configured: set \$VISUAL or \$EDITOR"):
        editor.require_editor()


def test_unbalanced_quotes_are_reported(monkeypatch: pytest.MonkeyPatch, no_editor: None) -> None:
    monkeypatch.setenv("EDITOR", "'oops")

    with pytest.raises(EditorError, match=r"\$EDITOR is not a valid command \(No closing quotation\): 'oops"):
        editor.editor_command()


def test_open_in_editor_runs_the_command_with_the_path_and_the_terminal(
    recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch, no_editor: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("EDITOR", "code --wait")
    note = tmp_path / "2026-09-02-x.md"

    editor.open_in_editor(note)

    assert len(recorded_commands.calls) == 1
    call = recorded_commands.calls[0]
    assert call.command == ("code", "--wait", str(note))
    assert call.capture is False
    assert call.input is None
    assert call.timeout is None
    assert call.cwd is None


def test_missing_program_is_an_error_naming_the_file(
    recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch, no_editor: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("EDITOR", "vim")
    recorded_commands.missing("vim")
    note = tmp_path / "x.md"

    with pytest.raises(EditorError) as info:
        editor.open_in_editor(note)

    assert info.value.message == (
        f"editor `vim` not found; set $VISUAL or $EDITOR to an installed editor ({note} is left as it is)"
    )
    assert info.value.type == "editor_error"


def test_non_zero_exit_is_an_error_naming_the_file(
    recorded_commands: RecordedCommands, monkeypatch: pytest.MonkeyPatch, no_editor: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("EDITOR", "vim")
    recorded_commands.script("vim", returncode=1)
    note = tmp_path / "x.md"

    with pytest.raises(EditorError) as info:
        editor.open_in_editor(note)

    assert info.value.message == f"editor `vim` exited with status 1; {note} is left as it is"


def test_open_in_editor_without_an_editor_runs_nothing(recorded_commands: RecordedCommands, no_editor: None) -> None:
    with pytest.raises(EditorError, match="no editor configured"):
        editor.open_in_editor(Path("/nowhere/x.md"))

    assert recorded_commands.calls == []
