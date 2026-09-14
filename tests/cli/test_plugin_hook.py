"""Tests for the agent plugin in `plugin/`: the `UserPromptSubmit` hook script run for real through
`sh` against the test vault, with the CLI reached through a `notes` wrapper on a controlled `PATH`;
the plugin and marketplace manifests and the hook declaration as JSON;
and the frontmatter and the shared procedure of the four skills."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from notes import __version__
from tests.conftest import BANNED_GLYPHS, Corpus, deliver

REPOSITORY = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPOSITORY / "plugin"
MARKETPLACE = REPOSITORY / ".claude-plugin" / "marketplace.json"
MANIFEST = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
HOOKS = PLUGIN_DIR / "hooks" / "hooks.json"
RECALL_SH = PLUGIN_DIR / "hooks" / "recall.sh"
SKILLS = ("decision", "fact", "idea", "note", "recall", "reminder")
DRAFTING_SKILLS = ("decision", "fact", "idea", "note", "reminder")

UNREAD = "Unread notes:"
RELATED = "Related notes:"
SEPTEMBER_9 = "2026-09-09T10:00:00+03:00"

# What the hook script and the CLI behind it need from PATH: `sh` (the interpreter), `jq` (the payload), and
# `git` (the sync that precedes recall). They are linked into a private bin directory so a test controls whether
# `notes` exists at all, whatever `uv tool install` put on the machine's PATH.
TOOLS = ("sh", "jq", "git")


def line(vault: Path, reasons: str, path: str, title: str) -> str:
    """One recall line as printed: `- [reasons] [id](abs) - Title`."""
    return f"- [{reasons}] [{path}]({vault / path}) - {title}"


def found(output: str) -> list[str]:
    """The IDs in the order the hook printed them, headings skipped."""
    return [entry.split("] [", 1)[1].split("]", 1)[0] for entry in output.splitlines() if entry.startswith("- [")]


def reasons_by_id(output: str) -> dict[str, str]:
    """The bracketed reasons of every printed line, by ID."""
    entries = [entry for entry in output.splitlines() if entry.startswith("- [")]
    return dict(zip(found(output), (entry.split("] [", 1)[0].removeprefix("- [") for entry in entries), strict=True))


class HookRunner:
    """Runs `recall.sh` through `sh` with a private `PATH`: `bin/` holds links to the machine's `sh`, `jq`, and
    `git` and, after `install_notes`, a `notes` wrapper that runs the CLI of this checkout with the test interpreter.
    The script runs in `cwd`, which is what recall falls back to when the payload carries no `cwd`."""

    def __init__(self, directory: Path, cwd: Path) -> None:
        self.bin = directory / "bin"
        self.bin.mkdir(parents=True)
        self.cwd = cwd
        for tool in TOOLS:
            self.link(tool)

    def link(self, tool: str) -> None:
        real = shutil.which(tool)
        assert real is not None, f"the hook tests need `{tool}` on PATH"
        (self.bin / tool).symlink_to(real)

    def unlink(self, tool: str) -> None:
        (self.bin / tool).unlink()

    def install_notes(self) -> None:
        wrapper = self.bin / "notes"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m notes "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)

    def run(self, stdin: str) -> subprocess.CompletedProcess[str]:
        """Run the script with `stdin` as its input, the way Claude Code runs it."""
        env = {**os.environ, "PATH": str(self.bin), "PYTHONUTF8": "1"}
        return subprocess.run(
            [str(self.bin / "sh"), str(RECALL_SH)],
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            env=env,
            cwd=self.cwd,
            check=False,
        )

    def hook(self, prompt: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        """Run the script with a `UserPromptSubmit` payload; `cwd` is left out of the payload when None."""
        payload: dict[str, Any] = {
            "session_id": "test-session",
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        }
        if cwd is not None:
            payload["cwd"] = str(cwd)
        return self.run(json.dumps(payload, ensure_ascii=False))


@pytest.fixture
def hook(tmp_path: Path, home: Path) -> HookRunner:
    """The hook runner with the `notes` wrapper installed, working in the test home."""
    runner = HookRunner(tmp_path / "hook", home)
    runner.install_notes()
    return runner


def silent(result: subprocess.CompletedProcess[str]) -> tuple[int, str, str]:
    return result.returncode, result.stdout, result.stderr


# The hook against the vault


def test_hook_prints_the_unread_block_and_the_related_block(hook: HookRunner, search_corpus: Path) -> None:
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9)

    result = hook.hook("ATLAS-27", cwd=hook.cwd)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines[:3] == [UNREAD, line(search_corpus, "unread", Corpus.PLANTS, "Water the plants"), RELATED]
    assert line(search_corpus, "issue: ATLAS-27, text", Corpus.KAFKA, "Project Atlas task updates over Kafka") in lines
    assert reasons_by_id(result.stdout)[Corpus.WEBSOCKET] == "issue: ATLAS-27, text, superseded"


def test_hook_passes_the_payload_cwd_to_recall(hook: HookRunner, home: Path, search_corpus: Path) -> None:
    # `~/Dev/exampleco` is the retention fact's project and only the parent of the decision's, so the path boost lifts
    # the fact above the decision that outranks it by text alone.
    result = hook.hook("replay", cwd=home / "Dev" / "exampleco")

    assert result.returncode == 0, result.stderr
    assert found(result.stdout) == [Corpus.RETENTION, Corpus.KAFKA]
    assert reasons_by_id(result.stdout) == {Corpus.RETENTION: "text, path", Corpus.KAFKA: "text"}


def test_hook_falls_back_to_its_working_directory_without_a_cwd(
    hook: HookRunner, home: Path, search_corpus: Path
) -> None:
    hook.cwd = home / "Dev" / "exampleco"
    hook.cwd.mkdir(parents=True)

    result = hook.hook("replay")

    assert result.returncode == 0, result.stderr
    assert found(result.stdout) == [Corpus.RETENTION, Corpus.KAFKA]
    assert reasons_by_id(result.stdout)[Corpus.RETENTION] == "text, path"


def test_hook_keeps_a_prompt_starting_with_a_dash_as_the_query(hook: HookRunner, search_corpus: Path) -> None:
    result = hook.hook("--help ATLAS-27", cwd=hook.cwd)

    assert result.returncode == 0, result.stderr
    assert "Usage:" not in result.stdout
    assert RELATED in result.stdout.splitlines()
    assert Corpus.KAFKA in found(result.stdout)


def test_hook_keeps_quotes_cyrillic_newlines_and_shell_syntax_in_the_prompt(
    hook: HookRunner, search_corpus: Path
) -> None:
    prompt = 'Почему "обновления задач" идут через Kafka,\nа не через $HOME `date` \\n; rm -rf ~ ?'

    result = hook.hook(prompt, cwd=hook.cwd)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert Corpus.RUSSIAN_KAFKA in found(result.stdout)
    assert (search_corpus / "config.toml").is_file()


def test_hook_recalls_from_a_prompt_far_larger_than_one_exec_argument(hook: HookRunner, search_corpus: Path) -> None:
    # A pasted file in the prompt: one argument may not exceed 128 KiB on Linux, so the script cuts the query.
    prompt = "ATLAS-27 " + "padding word " * 40_000

    result = hook.hook(prompt, cwd=hook.cwd)

    assert len(prompt) > 128 * 1024
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert Corpus.KAFKA in found(result.stdout)


def test_hook_prints_nothing_for_a_vault_without_notes(hook: HookRunner, vault: Path) -> None:
    assert silent(hook.hook("ATLAS-27", cwd=hook.cwd)) == (0, "", "")


def test_hook_prints_nothing_for_an_unrelated_prompt(hook: HookRunner, search_corpus: Path) -> None:
    assert silent(hook.hook("quantum entanglement", cwd=hook.cwd)) == (0, "", "")


# Silent exits


def test_hook_exits_silently_without_a_vault(hook: HookRunner, home: Path) -> None:
    assert silent(hook.hook("ATLAS-27", cwd=home)) == (0, "", "")


def test_hook_exits_silently_when_the_notes_directory_is_missing(hook: HookRunner, vault: Path) -> None:
    shutil.rmtree(vault / "notes")

    assert silent(hook.hook("ATLAS-27", cwd=hook.cwd)) == (0, "", "")


def test_hook_exits_silently_without_the_notes_binary(tmp_path: Path, home: Path, search_corpus: Path) -> None:
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9)
    runner = HookRunner(tmp_path / "hook", home)

    assert silent(runner.hook("ATLAS-27", cwd=home)) == (0, "", "")


def test_hook_exits_silently_without_jq(hook: HookRunner, search_corpus: Path) -> None:
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9)
    hook.unlink("jq")

    assert silent(hook.hook("ATLAS-27", cwd=hook.cwd)) == (0, "", "")


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '"a string"', "null", '{"prompt": "ATLAS-27"'])
def test_hook_exits_silently_on_a_payload_that_is_not_an_object(
    hook: HookRunner, search_corpus: Path, stdin: str
) -> None:
    deliver(search_corpus, Corpus.PLANTS, SEPTEMBER_9)

    assert silent(hook.run(stdin)) == (0, "", "")


def test_hook_script_is_valid_posix_shell() -> None:
    sh = shutil.which("sh")
    assert sh is not None
    checked = subprocess.run([sh, "-n", str(RECALL_SH)], capture_output=True, text=True, check=False)

    assert checked.returncode == 0, checked.stderr
    assert RECALL_SH.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


# The manifests and the hook declaration


def test_manifest_names_the_plugin() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["name"] == "notes"
    assert manifest["version"] == __version__
    assert manifest["description"].strip()
    assert manifest["author"]["name"].strip()


def test_marketplace_offers_the_plugin_from_the_repository_root() -> None:
    """The repository is its own marketplace: `/plugin marketplace add butvinm/notes`, `/plugin install notes@notes`."""
    marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert marketplace["name"] == "notes"
    assert marketplace["owner"]["name"].strip()
    assert marketplace["description"].strip()
    (entry,) = marketplace["plugins"]
    assert entry["name"] == manifest["name"]
    assert entry["description"].strip()
    assert (REPOSITORY / entry["source"]).resolve() == PLUGIN_DIR
    assert not set(MARKETPLACE.read_text(encoding="utf-8")) & set(BANNED_GLYPHS)


def test_hooks_declare_the_recall_script_on_user_prompt_submit() -> None:
    declared = json.loads(HOOKS.read_text(encoding="utf-8"))

    assert set(declared["hooks"]) == {"UserPromptSubmit"}
    (entry,) = declared["hooks"]["UserPromptSubmit"]
    assert "matcher" not in entry
    (command,) = entry["hooks"]
    assert command == {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/recall.sh"', "timeout": 10}
    assert RECALL_SH.is_file()


# The skills


def skill_text(skill: str) -> str:
    return (PLUGIN_DIR / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """The frontmatter mapping (loaded as YAML, so a malformed description is caught) and the body after it."""
    assert text.startswith("---\n"), "SKILL.md must start with frontmatter"
    end = text.index("\n---\n", 4)
    loaded = YAML(typ="safe").load(text[4:end])
    assert isinstance(loaded, dict), "frontmatter must be a mapping"
    return loaded, text[end + len("\n---\n") :]


@pytest.mark.parametrize("skill", SKILLS)
def test_skill_has_name_and_description_frontmatter(skill: str) -> None:
    meta, body = split_frontmatter(skill_text(skill))

    assert meta["name"] == skill
    assert isinstance(meta["description"], str)
    assert meta["description"].strip()
    assert len(meta["description"]) <= 1024
    assert body.lstrip().startswith("# ")


@pytest.mark.parametrize("skill", DRAFTING_SKILLS)
def test_drafting_skill_follows_the_shared_procedure(skill: str) -> None:
    _, body = split_frontmatter(skill_text(skill))

    for step in (
        "never the transcript",
        "one targeted clarification question",
        "notes prompt ",
        "notes draft create ",
        "in the background",
        "notes draft show <draft-id>",
        "AskUserQuestion",
        "**save**, **revise** (free-form feedback), **cancel**",
        "notes draft save <draft-id> --json",
        'notes draft revise <draft-id> "<feedback>" --json',
        "notes draft discard <draft-id>",
        "Nothing is saved without that confirmation",
    ):
        assert step in body, f"{skill}: {step}"


@pytest.mark.parametrize(
    ("skill", "siblings"),
    [
        ("fact", ("/notes:decision", "/notes:idea", "/notes:reminder")),
        ("idea", ("/notes:decision", "/notes:fact", "/notes:reminder")),
    ],
)
def test_kind_skill_fixes_its_kind_and_hands_the_others_over(skill: str, siblings: tuple[str, ...]) -> None:
    """Every kind skill names its own kind and points at the skills of the kinds it competes with."""
    _, body = split_frontmatter(skill_text(skill))

    assert f"The kind is `{skill}`" in body
    assert f"notes prompt {skill}" in body
    assert f"notes draft create {skill} --json" in body
    for sibling in siblings:
        assert sibling in body, sibling


def test_note_skill_settles_the_kind_from_the_vault() -> None:
    _, body = split_frontmatter(skill_text("note"))

    assert "ls ~/.notes/types/" in body
    assert "notes prompt <kind>" in body
    assert "notes draft create <kind> --json" in body
    for sibling in ("/notes:fact", "/notes:idea", "/notes:decision", "/notes:reminder"):
        assert sibling in body, sibling


def test_decision_skill_fixes_the_kind_and_offers_supersedes() -> None:
    _, body = split_frontmatter(skill_text("decision"))

    assert "notes prompt decision" in body
    assert "notes draft create decision --json" in body
    assert "notes relate <new-id> supersedes <old-id>" in body
    assert "explicit approval" in body


def test_reminder_skill_fixes_the_kind_and_settles_the_schedule() -> None:
    _, body = split_frontmatter(skill_text("reminder"))

    assert "notes prompt reminder" in body
    assert "notes draft create reminder --json" in body
    for form in (
        "`at 2026-09-09T10:00:00+03:00`",
        "`every 3 days from 2026-09-02T10:00:00+03:00`",
        "`in 3 days`",
        "`tomorrow 09:00`",
        "`today 18:00`",
        "`2026-10-01 10:00`",
        "`every 2 weeks`",
    ):
        assert form in body, form
    assert "ask for it" in body
    assert '"schedule": "in 3 days"' in body


def test_recall_skill_searches_and_reads_before_reasoning() -> None:
    _, body = split_frontmatter(skill_text("recall"))

    for command in ("notes recall --json", "notes search --json", "notes show <id>", "--all", "notes read <id>"):
        assert command in body, command
    assert "before reasoning" in body
    assert "notes draft" not in body


def test_plugin_files_use_plain_punctuation() -> None:
    files = sorted(path for path in PLUGIN_DIR.rglob("*") if path.is_file())

    assert [path.relative_to(PLUGIN_DIR).as_posix() for path in files] == [
        ".claude-plugin/plugin.json",
        "hooks/hooks.json",
        "hooks/recall.sh",
        "skills/decision/SKILL.md",
        "skills/fact/SKILL.md",
        "skills/idea/SKILL.md",
        "skills/note/SKILL.md",
        "skills/recall/SKILL.md",
        "skills/reminder/SKILL.md",
    ]
    for file in files:
        assert not set(file.read_text(encoding="utf-8")) & set(BANNED_GLYPHS), str(file)
