"""Shared fixtures for the test suite."""

import json
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes import clock, proc
from notes import init as init_flow
from notes.cli import cli
from notes.config import GeneratorConfig
from notes.proc import CommandNotFound, CommandResult

REAL_RUNNER = proc.runner
"""The subprocess runner as imported, before any test replaces `proc.runner`; `vault` runs `notes init` with it."""

FROZEN_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone(timedelta(hours=3)))
"""The moment `frozen_now` fixes the clock at: noon on 2026-09-02 in UTC+3, so new notes are dated 2026-09-02."""

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "corpus"
"""The search corpus: eleven valid notes in English and Russian with tags, issue IDs, keywords, a superseded pair,
and an archived one, for the search and recall tests."""

# Em dash, en dash, curly quotes, ellipsis, arrows, non-breaking space: never in a file of this repository.
BANNED_GLYPHS = "".join(
    chr(code) for code in (0x2014, 0x2013, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x2192, 0x2190, 0x00A0)
)


class Corpus:
    """The IDs of the corpus notes and what each one is for."""

    KAFKA = "notes/2026-09-02-project-atlas-kafka-task-updates.md"
    """Active decision tagged ATLAS-27 and sync-worker, keyword `replay`, supersedes WEBSOCKET."""
    WEBSOCKET = "notes/2026-08-27-project-atlas-websocket.md"
    """The superseded decision, also tagged ATLAS-27."""
    RUSSIAN_KAFKA = "notes/2026-09-01-обновления-задач-через-kafka.md"
    """Russian fact tagged ATLAS-31 and kafka; its body mentions `соединения`."""
    LEMMAS = "notes/2026-08-05-поиск-по-леммам.md"
    """Russian idea with a Cyrillic tag; its title mentions `словоформам`."""
    LEGACY = "notes/2026-08-30-legacy-proxy-timeouts.md"
    """Archived fact tagged legacy and proxy, about WebSocket connections."""
    PLANTS = "notes/2026-09-02-water-the-plants.md"
    """Reminder scheduled at 2026-09-09T10:00:00+03:00."""
    ORION = "notes/2026-08-15-orion-batch-scheduler.md"
    """Idea with the path ~/Dev/orion."""
    LIPS = "notes/2026-08-10-ship-lips-release.md"
    """Promise tagged LIPS-12, due 2026-09-15T18:00:00+03:00."""
    FTS = "notes/2026-07-20-fts5-porter-stemming.md"
    """Fact about the porter tokenizer."""
    STANDUP = "notes/2026-07-01-standup-moved.md"
    """Event without paths."""
    RETENTION = "notes/2026-06-15-kafka-topic-retention.md"
    """Fact tagged kafka; its body mentions `replay`."""

    ALL = (KAFKA, WEBSOCKET, RUSSIAN_KAFKA, LEMMAS, LEGACY, PLANTS, ORION, LIPS, FTS, STANDUP, RETENTION)


def git(cwd: Path, *args: str) -> str:
    """Run `git` in `cwd` for test setup and assertions, outside the program's own `run_command` seam."""
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return completed.stdout


def indexed_paths(vault: Path) -> list[str]:
    """The paths in the `notes` table of the vault's index, for tests that check what a command indexed."""
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        return [row[0] for row in conn.execute("SELECT path FROM notes ORDER BY path")]
    finally:
        conn.close()


def deliver(
    vault: Path, path: str, occurrence: str, *, delivered_at: str | None = None, read_at: str | None = None
) -> None:
    """Insert one delivery for the note at `path` straight into the index, the stand-in for `notes tick` in tests of
    unread state: unread unless `read_at` is given, delivered at the occurrence unless `delivered_at` says otherwise."""
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        conn.execute(
            "INSERT INTO deliveries (path, occurrence_at, delivered_at, read_at) VALUES (?, ?, ?, ?)",
            (path, occurrence, delivered_at or occurrence, read_at),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def git_cmd() -> Callable[..., str]:
    """The `git(cwd, *args) -> stdout` helper for tests that inspect or prepare a repository directly."""
    return git


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at a temporary directory so ~/.notes resolves inside the test.

    Git identity is fixed through environment variables, and XDG_CONFIG_HOME is redirected too,
    so neither the user's global Git configuration nor their systemd user units leak into a test.
    """
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home_dir / ".config"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test User")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test User")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    return home_dir


@pytest.fixture
def bare_vault(home: Path) -> Path:
    """The vault directory layout under HOME without Git or SQLite: the packaged config.toml, notes/, and types/.

    Enough for tests of vault resolution, configuration, and indexing that do not need a repository.
    """
    vault = home / ".notes"
    (vault / "notes").mkdir(parents=True)
    (vault / "types").mkdir()
    packaged = resources.files("notes").joinpath("defaults/config.toml").read_text(encoding="utf-8")
    (vault / "config.toml").write_text(packaged, encoding="utf-8")
    return vault


@pytest.fixture
def vault(home: Path) -> Path:
    """A working vault made by the real `notes init`: the packaged defaults, a Git repository on `master` with the
    initial commit, and the index. No remote, no auto-push, no timer.

    The flow runs on the real subprocess runner even when `recorded_commands` is active, so the repository exists
    whatever a test scripts afterwards, and none of its Git calls show up in the recording.
    """
    previous = proc.runner
    proc.runner = REAL_RUNNER
    try:
        outcome = init_flow.run(remote=None, auto_push=False, enable_notifications=False)
    finally:
        proc.runner = previous
    return outcome.vault


@pytest.fixture
def search_corpus(runner: CliRunner, vault: Path) -> Path:
    """The vault with every note of `CORPUS_DIR` copied into `notes/`, then indexed and committed by `notes sync`."""
    for file in sorted(CORPUS_DIR.glob("*.md")):
        shutil.copyfile(file, vault / "notes" / file.name)
    result = runner.invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    return vault


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """An empty bare repository on `master` to serve as the vault's remote; not yet configured in the vault."""
    path = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "master", str(path))
    return path


@pytest.fixture
def runner() -> CliRunner:
    """A click test runner for invoking the CLI in-process."""
    return CliRunner()


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Freeze `clock.now` at `FROZEN_NOW`, the time `new`, `tick`, and the drafts read; returns that moment."""
    monkeypatch.setattr(clock, "now", lambda: FROZEN_NOW)
    return FROZEN_NOW


class FakeEditor:
    """A stand-in `$EDITOR`: a shell script that overwrites the file it is given and exits with a chosen status.

    `write(text)` makes every later run replace the edited file's content with `text` (until then the file is left
    as it is), `fail(status)` sets the exit status, and `paths` lists the files the editor was opened on, in order.
    The file is the last argument, so the script also works behind extra arguments such as `--wait`.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.script = directory / "editor"
        self._text = directory / "text"
        self._status = directory / "status"
        self._log = directory / "log"
        self.script.write_text(self._script(), encoding="utf-8")
        self.script.chmod(0o755)

    def write(self, text: str) -> None:
        """Replace the content of every file edited from now on with `text`."""
        self._text.write_text(text, encoding="utf-8")

    def fail(self, status: int) -> None:
        """Exit with `status` from now on (after writing the text, when one was set)."""
        self._status.write_text(str(status), encoding="utf-8")

    @property
    def paths(self) -> list[str]:
        return self._log.read_text(encoding="utf-8").splitlines() if self._log.exists() else []

    def _script(self) -> str:
        text, status, log = (shlex.quote(str(path)) for path in (self._text, self._status, self._log))
        return "\n".join(
            [
                "#!/bin/sh",
                "# The fake editor of the test suite: log the file, write the text when one was set, exit as told.",
                "for file; do :; done",
                f'printf "%s\\n" "$file" >> {log}',
                f'if [ -f {text} ]; then cat {text} > "$file"; fi',
                f'if [ -f {status} ]; then exit "$(cat {status})"; fi',
                "exit 0",
                "",
            ]
        )


@pytest.fixture
def fake_editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeEditor:
    """Install the fake editor as `$EDITOR` (with `$VISUAL` unset); it runs for real through `run_command`."""
    fake = FakeEditor(tmp_path / "editor")
    monkeypatch.setenv("EDITOR", str(fake.script))
    monkeypatch.delenv("VISUAL", raising=False)
    return fake


FAKE_GENERATOR_SCRIPT = Path(__file__).resolve().parent / "fixtures" / "fake_generator.py"


class FakeGenerator:
    """A stand-in generator: `tests/fixtures/fake_generator.py` run by the test interpreter with a state directory.

    `command` is the argument array to put in `[generator] command` (`config` wraps it in a `GeneratorConfig`).
    `reply(short_name, markdown)` scripts the envelope it prints, `print(text)` any other stdout, `fail(status,
    stderr)` a non-zero exit with optional stderr, `stderr(text)` stderr on its own, and `sleep(seconds)` a delay
    before it answers. `prompts` lists every prompt it received on stdin, in order, and `args` the arguments after
    the state directory of its last run. Until a reply is scripted it exits with status 3 and says so on stderr.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.state = directory
        self.command: tuple[str, ...] = (sys.executable, str(FAKE_GENERATOR_SCRIPT), str(directory))

    @property
    def config(self) -> GeneratorConfig:
        return GeneratorConfig(command=self.command)

    def reply(self, short_name: str, markdown: str) -> None:
        """Print the envelope for `short_name` and `markdown` on every later run."""
        self.print(json.dumps({"short_name": short_name, "markdown": markdown}, ensure_ascii=False))

    def print(self, stdout: str) -> None:
        """Print `stdout` as is on every later run."""
        (self.state / "output").write_text(stdout, encoding="utf-8")

    def stderr(self, text: str) -> None:
        """Print `text` on stderr on every later run, before the stdout."""
        (self.state / "stderr").write_text(text, encoding="utf-8")

    def fail(self, status: int, stderr: str = "") -> None:
        """Exit with `status` on every later run (after printing `stderr` when given, and nothing on stdout unless
        a reply was scripted)."""
        (self.state / "status").write_text(str(status), encoding="utf-8")
        if stderr:
            self.stderr(stderr)
        if not (self.state / "output").is_file():
            self.print("")

    def sleep(self, seconds: float) -> None:
        """Wait `seconds` before answering on every later run, for timeout tests."""
        (self.state / "sleep").write_text(str(seconds), encoding="utf-8")

    @property
    def prompts(self) -> list[str]:
        directory = self.state / "prompts"
        if not directory.is_dir():
            return []
        return [file.read_text(encoding="utf-8") for file in sorted(directory.iterdir())]

    @property
    def args(self) -> list[str] | None:
        file = self.state / "args.json"
        return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None

    def install(self, vault: Path) -> None:
        """Point the `[generator] command` line of `<vault>/config.toml` at this generator; other lines stay."""
        path = vault / "config.toml"
        line = "command = [" + ", ".join(json.dumps(arg) for arg in self.command) + "]"
        text, count = re.subn(r"^command = .*$", lambda _: line, path.read_text(encoding="utf-8"), count=1, flags=re.M)
        if count != 1:
            raise AssertionError(f"{path} has no `command = ...` line to replace")
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def fake_generator(tmp_path: Path, vault: Path) -> FakeGenerator:
    """Install the fake generator as the vault's `[generator] command` and commit that change, so the next sync of
    the test sees a clean tree; the generator runs for real through `run_command`."""
    fake = FakeGenerator(tmp_path / "generator")
    fake.install(vault)
    git(vault, "add", "--", "config.toml")
    git(vault, "commit", "-q", "-m", "test: use the fake generator")
    return fake


@dataclass(frozen=True)
class RecordedCall:
    """One call that went through `run_command` while `recorded_commands` was active."""

    command: tuple[str, ...]
    cwd: Path | None
    input: str | None
    timeout: float | None
    capture: bool
    env: Mapping[str, str] | None


Responder = Callable[[tuple[str, ...]], CommandResult]


class RecordedCommands:
    """A `proc.runner` replacement that records every call and answers from scripted results.

    Nothing is executed unless its program was passed through explicitly, so a test can never reach the real
    `systemctl` or `notify-send`. An unscripted command succeeds silently with empty output; `script` sets the
    answer for every command starting with the given words (the longest matching prefix wins, the latest among
    equals), `respond` scripts a callable, `missing` makes a program raise `CommandNotFound`, and `passthrough`
    forwards a program to the real runner (Git, in tests that also drive the real repository).
    """

    def __init__(self, real: proc.Runner) -> None:
        self.real = real
        self.calls: list[RecordedCall] = []
        self._scripts: list[tuple[tuple[str, ...], Responder]] = []
        self._passthrough: set[str] = set()

    def script(self, *prefix: str, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        """Answer every command starting with `prefix` with a fixed result."""
        self.respond(*prefix, using=lambda command: CommandResult(command, returncode, stdout, stderr))

    def respond(self, *prefix: str, using: Responder) -> None:
        """Answer every command starting with `prefix` by calling `using(command)`, which may raise."""
        self._scripts.append((prefix, using))

    def missing(self, program: str) -> None:
        """Make `program` raise `CommandNotFound`, as `run_command` does for a binary that is not on `PATH`."""

        def raise_not_found(command: tuple[str, ...]) -> CommandResult:
            raise CommandNotFound(command)

        self.respond(program, using=raise_not_found)

    def passthrough(self, *programs: str) -> None:
        """Run the given programs for real (still recorded)."""
        self._passthrough.update(programs)

    def commands(self, *prefix: str) -> list[tuple[str, ...]]:
        """The recorded commands, in order, limited to those starting with `prefix` when one is given."""
        return [call.command for call in self.calls if call.command[: len(prefix)] == prefix]

    def __call__(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        input: str | None = None,
        timeout: float | None = None,
        capture: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        self.calls.append(RecordedCall(command, cwd, input, timeout, capture, env))
        if command[0] in self._passthrough:
            return self.real(args, cwd=cwd, input=input, timeout=timeout, capture=capture, env=env)
        responder = self._responder(command)
        return responder(command) if responder is not None else CommandResult(command, 0)

    def _responder(self, command: tuple[str, ...]) -> Responder | None:
        best: Responder | None = None
        best_length = -1
        for prefix, responder in self._scripts:
            if len(prefix) >= best_length and command[: len(prefix)] == prefix:
                best, best_length = responder, len(prefix)
        return best


@pytest.fixture
def recorded_commands(monkeypatch: pytest.MonkeyPatch) -> RecordedCommands:
    """Replace `run_command` with a recorder that answers from scripted results and runs nothing by default."""
    recorded = RecordedCommands(proc.runner)
    monkeypatch.setattr(proc, "runner", recorded)
    return recorded
