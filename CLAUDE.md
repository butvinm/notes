# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`notes` is a local personal knowledge system: Markdown notes in `~/.notes`, a derived SQLite index with FTS5, schedules delivered as desktop notifications by a systemd user timer, Git auto-commit, agent-assisted drafting through a configurable generator command, and a Claude Code plugin (hook plus skills). `README.md` is the user documentation. `docs/plans/completed/20260902-notes-v1.md` is the implementation plan; it carries every design decision (vault layout, note format, validation rules, schedule grammar, SQLite schema, sync, Git, ranking, deliveries, drafts, plugin) and the per-task notes on how each was implemented. Read the relevant Technical Details section before changing a behavior, and keep the plan in sync when scope changes.

## Commands

```shell
uv sync                                   # create .venv and install the project with the dev group
uv run pytest                             # the whole suite: tests/unit, tests/cli, tests/scenarios
uv run pytest tests/unit/test_schedule.py -k latest_due
uv run pytest --cov                       # coverage for src/notes
uv run ruff check                         # lint (E, F, I, UP, B, SIM; line length 120)
uv run ruff format --check                # formatting; `uv run ruff format` applies it
uv run ty check                           # type checking
uv run notes --help                       # the CLI from the project environment
uv tool install --reinstall .             # refresh the globally installed `notes`
```

All four checks (`pytest`, `ruff check`, `ruff format --check`, `ty check`) must pass before a task is complete. Never install anything into the system Python; `uv` owns the environment.

## Layout

- `src/notes/cli/`: the click commands, one module per command or group. `cli/__init__.py` holds the root group, the shared `--json` option, the mapping of `NotesError` to exit code 1 with a human line or a JSON envelope, and `VaultCommand` / `Session`, the machinery that syncs the vault before every command that operates on it.
- `src/notes/`: one module per concern: `vault` (root at `~/.notes`, layout, ID resolution), `config` (tomllib), `errors`, `output`, `proc` (the subprocess seam), `document` (frontmatter and H1 parsing, validation, round-trip rewriting), `schedule`, `slug`, `db` (schema, `notes_view` with the effective status), `textnorm` and `stopwords`, `sync`, `git`, `search`, `queries`, `deliveries`, `notify`, `systemd`, `generator`, `drafts`, `prompts`, `relations`, `editor`, `clock`, `init`, `defaults_io`.
- `src/notes/defaults/`: the packaged data `notes init` installs into the vault: `config.toml`, `gitignore`, `prompt.md` (the shared drafting prompt), `DEFAULTS_VERSION`, and `types/<kind>/{prompt.md,template.md}` for `decision`, `fact`, `promise`, `event`, `reminder`, `idea`. This is the only place prompts and templates live in the repository. Once installed, the copies under `~/.notes/` are the ones the CLI reads, and it never overwrites them; when a packaged prompt or template changes, bump `DEFAULTS_VERSION` so `notes check` can report that newer defaults exist.
- `plugin/`: the agent plugin: `.claude-plugin/plugin.json` (its version equals `notes.__version__`), `hooks/hooks.json` and `hooks/recall.sh` (the `UserPromptSubmit` hook), `skills/{capture,decision,reminder,recall}/SKILL.md`. The directory is not called `claude-plugin` because Codex reads this same layout, manifests included; one directory serves both agents, so a second one is never needed.
- `.claude-plugin/marketplace.json`: the repository is its own single-plugin marketplace, so users install with `/plugin marketplace add butvinm/notes` and `/plugin install notes@notes` in Claude Code, or `codex plugin marketplace add butvinm/notes` and `codex plugin add notes@notes` in Codex. Its one entry points `source` at `./plugin`; keep its name and description in step with the plugin manifest.
- `tests/`: `conftest.py` with the shared fixtures, `unit/` (one test module per source module), `cli/` (every command through `click.testing.CliRunner`), `scenarios/` (the five acceptance scenarios of the plan end to end), `fixtures/` (sample notes, the search corpus, the fake generator script).

## Architecture rules

- Markdown is the source of truth. SQLite is derived from it, except the `deliveries` table (device-local delivery and read state). A command that changes a note writes the file first and updates the index second; an interrupted command is repaired by the next sync.
- Every command on an existing vault is declared with `@vault_command(...)` and starts with a sync (`Session.sync()`), which indexes the changed files and runs the Git auto-commit step. A command that writes Markdown calls `session.sync()` again afterwards; that second sync validates, indexes, and commits what it wrote. `index_only=True` (`tick`, `read`) syncs without committing or pushing; `offline=True` (`recall`, `push`) commits but never auto-pushes. `init`, `notifications`, `prompt`, and `pull` are plain click commands.
- External programs (`git`, `notify-send`, `canberra-gtk-play`, `systemctl`, `journalctl`, the editor, the generator) run only through `proc.run_command`. Nothing else imports `subprocess`. Tests replace `proc.runner` (the `recorded_commands` fixture) instead of patching `subprocess`.
- The current time comes from `clock.now()`; the `frozen_now` fixture replaces it.
- The vault root comes from `vault.root()` (`Path.home() / ".notes"`); tests redirect it by setting `HOME`. There is no vault registry, no discovery from the current directory, and no multi-vault support.
- User-facing failures are `NotesError` subclasses (`errors.py`); raise them and let the root group turn them into exit code 1. Click usage errors exit 2. Output goes through `output.emit(ctx, human, data)` so `--json` works for every command.
- `config.toml` is read with `tomllib` and never rewritten by the CLI after `init`. Frontmatter is read and rewritten with `ruamel.yaml` in round-trip mode (`document.rewrite_frontmatter`), so a rewrite changes only the intended lines.
- Valid kinds are the directories under `types/` with a `template.md`; validation rules live in `document.validate` and the schedule grammar in `schedule.parse`; keep them the single place a rule is enforced.

## Testing

- Every code change comes with tests in the same change, covering the success path and the failure paths.
- Unit tests cover the pure modules directly; CLI tests invoke `cli` with `CliRunner` against a temporary vault; scenario tests tell the acceptance scenarios of the plan through the CLI alone. pytest runs in `--import-mode=importlib`, so `tests/unit` and `tests/cli` may share module basenames.
- Fixtures in `tests/conftest.py`: `home` (points `HOME` and `XDG_CONFIG_HOME` at `tmp_path` and fixes the Git identity), `bare_vault` (the directory layout without Git or kinds), `vault` (a real `notes init` on the real subprocess runner), `search_corpus` (the eleven notes of `tests/fixtures/corpus/` indexed), `remote` (an empty bare repository), `runner`, `frozen_now`, `fake_editor`, `fake_generator` (the script `tests/fixtures/fake_generator.py`, wired into `config.toml`), `recorded_commands` (a scripted `proc.runner` that records calls and runs nothing unless passed through), `git_cmd`. Helpers: `git`, `indexed_paths`, `deliver`; constants `FROZEN_NOW`, `CORPUS_DIR`, `BANNED_GLYPHS`.
- The packaged defaults, the plugin files, and the documentation are checked for banned glyphs (`BANNED_GLYPHS`); the templates are rendered and validated; the hook script runs under a private `PATH`.
- `tests/unit/test_docs.py` ties the documentation to the code: every command of the root group, every packaged kind, every `config.toml` key, and the schedule forms the README shows must appear there with the value the code implements. Adding a command, a kind, or a config key means updating `README.md` in the same change, or that test fails.

## Conventions

- Never author file content through the shell: no heredocs, no `echo`/`printf` redirects, no `sed -i`, no `python -c` carrying the text. Use the Write and Edit tools (Read first when editing). Reading and searching with `cat`, `sed -n`, `grep`, and `find` is fine. A PreToolUse hook on this machine denies the shell route, so it only wastes a turn.
- Prompts, templates, and other text the code ships are packaged data files under `src/notes/defaults/`, never string literals in Python.
- No em dashes, en dashes, curly quotes, ellipsis characters, arrows, or non-breaking spaces in any file, prose or code. Use `-`, straight quotes, `...`, `->`, and `<-`.
- Never break a prose line at a column limit: Markdown, docstrings, comments, and commit messages keep a sentence on one line however long it runs; a break belongs only at a structural boundary (end of sentence, clause, list item, paragraph).
- Python: `ruff` line length 120, `ruff format`, full type annotations checked by `ty`, docstrings that say what a function is for rather than restate its signature.
- `src/notes/defaults/types/*/template.md` and `tests/fixtures/` are listed in `.prettierignore`: the templates carry `{title}` and `{schedule}` placeholders that a YAML formatter would rewrite, and the fixtures are byte-exact parser input. Do not format them.
- Git: stage files explicitly (never `git add .`), atomic commits, messages in the form `feat: ...` or `docs: ...` describing one change. The repository's default branch is `master`.
- When a task changes scope or takes a decision the plan did not spell out, record it under the task in `docs/plans/20260902-notes-v1.md`.
