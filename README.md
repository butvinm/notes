# notes

`notes` is a local, Git-backed memory for Claude Code and Codex. Ask your agent to remember something once, then recall it in a later conversation.

## Try it in one minute

### Install

Requirements: Python 3.12 or newer, `uv`, and `Git`. Desktop notifications additionally require Linux with `systemd`, `notify-send`, and optionally `canberra-gtk-play`. The agent hook requires `jq`.

Install the latest version directly from GitHub and initialize your vault:

```shell
uv tool install git+https://github.com/butvinm/notes
notes init
```

For a local checkout or development install, use `uv tool install .`; after upgrading it, run `uv tool install --reinstall .`. The default vault is `~/.notes/`.

Install the plugin for the agent you use.

Claude Code:

```text
/plugin marketplace add butvinm/notes
/plugin install notes@notes
```

Codex:

```text
codex plugin marketplace add butvinm/notes
codex plugin add notes@notes
```

To try a local checkout without installing the plugin, use `claude --plugin-dir ./plugin`.

### Ask your agent

Ask your agent to save project decisions, facts, ideas, and reminders. The plugin drafts each note and asks for confirmation before saving it.

Here are a few software-project memory examples:

### Record a decision

```text
You: Remember that we chose PostgreSQL for the event log.

Claude: I'll save this as a decision note:

Use PostgreSQL for the event log

Decision: Store application events in PostgreSQL.

Save this note?

1. Save
2. Revise
3. Cancel
```

### Recall a decision

```text
You: What did we decide about authentication?

Claude: You decided to use short-lived access tokens with refresh-token rotation. The decision was recorded in `2026-08-28-authentication.md`.
```

### Schedule follow-up work

```text
You: Remind me to review the migration plan next Friday.

Claude: Save this reminder for Friday at 09:00?

Review the migration plan

1. Save
2. Revise
3. Cancel
```

### Capture an observation

```text
You: This API behavior is surprising.

Claude: I can save this as a project fact or idea. What should I record about the behavior?
```

## Agent integration

The plugin connects the agent to the vault in two directions: it recalls existing notes and provides skills for creating new ones.

Before each user prompt, the hook runs `notes recall` with the prompt and current working directory. It injects a small context block containing unread reminders and the most relevant note titles, paths, and match reasons. It does not inject full note bodies. If the CLI, `jq`, or an initialized vault is unavailable, it injects nothing.

Use `/notes:recall` when the automatic context is not enough. The recall skill searches by issue ID, tag, project, or text, then reads the notes needed to answer. Use `/notes:fact`, `/notes:idea`, `/notes:decision`, and `/notes:reminder` to create notes from the current conversation, one skill per kind, and `/notes:note` for a kind you added yourself.

For Claude Code, consider pairing this plugin with the [`skill-reminder` hook](https://github.com/butvinm/cc-toys#skill-reminder-hook). It reminds the agent to activate relevant skills before each prompt, making it more reliable at loading and using the notes skills.

## Command-line interface

The plugin uses the `notes` command-line interface. Use it directly for scripts, inspection, and automation. Every command supports `--json` for machine-readable output.

```shell
notes --help
notes <command> --help
```

Human output adapts to the reader. On a terminal, a note is shown as its title followed by its dimmed ID, both hyperlinked to the file (OSC 8, as `ls --hyperlink` does), kinds, statuses, the `[unread]` marker, and match reasons are coloured, and `notes list` aligns its columns. In a pipe, a note is `[<id>](<absolute path>) - <title>` with no escape codes, the form the hook and the skills parse. Set `NO_COLOR` to force the plain form on a terminal, or `FORCE_COLOR` to force the terminal form when piping.

The sections below cover the main CLI workflows.

## Create and save notes

Create a note directly with `notes new`, revise it with `notes edit`, and organize existing notes with `notes move` and `notes relate`.

Notes can also be drafted with the configured generator. `notes prompt` shows the context expected for a kind; `notes draft create <kind>` creates a draft from JSON input, `notes draft show` displays it, and `notes draft revise` sends feedback for another generation. Nothing is indexed or committed until `notes draft save` validates and saves it. Use `notes draft list` to find drafts and `notes draft discard` to remove one without saving it.

The generator receives a prompt on standard input and returns JSON with a short name and Markdown:

```json
{ "short_name": "postgresql-event-log", "markdown": "---\nkind: decision\n..." }
```

## Note format

A note ID is its path relative to the vault. Files use `notes/YYYY-MM-DD-<slug>.md`; the first H1 is the title.

```markdown
---
kind: decision
status: active
paths: [~/src/example-app]
tags: [architecture, EXAMPLE-42]
keywords: [event log, recovery, retention]
references:
  - docs/architecture.md
related:
  - relation: supersedes
    note: 2026-08-20-store-events-in-files.md
---

# Use PostgreSQL for the event log

**Decision:** Store application events in PostgreSQL.

**Rationale:** It provides transactional writes, retention controls, and familiar recovery tooling.
```

`kind` and `status` are required. The built-in kinds are `decision`, `fact`, `reminder`, and `idea`. A `reminder` also requires a `schedule`. Unknown frontmatter keys are preserved.

Relations can be `supersedes`, `child`, or `related`. Superseded notes remain unchanged on disk but are hidden from normal search unless an exact tag or issue ID matches.

## Find and use notes

Use `notes show <id>` to read one note, `notes list` to browse notes, and `notes search <query>` to search titles, tags, keywords, and bodies. Exact tags and tracker issue IDs rank above text matches. Archived and superseded notes are excluded by default; add `--all` to include them.

`notes recall [<query>]` produces a small context block for tools and agents. It lists unread reminders first, then exact matches, then the strongest text matches. It emits titles, paths, and match reasons, never note bodies.

## Reminders

Schedules are stored in one of two canonical forms:

```yaml
schedule: at 2026-09-09T10:00:00+03:00
schedule: every 3 days from 2026-09-02T10:00:00+03:00
```

Interactive commands and draft saving also accept `in 3 days`, `in a week`, `tomorrow`, `tomorrow 09:30`, `today 18:00`, `2026-10-01`, `2026-10-01 10:00`, and `every 2 weeks`. Dates without a time use 09:00 in the local time zone.

`notes notifications enable` installs `notes-tick.timer`, which runs `notes tick` every minute. Each occurrence is delivered once and stays unread until `notes read <id>`. Check or change notification setup with `notes notifications status` and `notes notifications disable`.

## Sync and backup

Notes are stored as ordinary Markdown files. A derived SQLite index makes them fast to search and can always be rebuilt. The `deliveries` table is the exception: it stores device-local reminder delivery and read state.

Vault commands sync changed Markdown and commit valid changes to Git. Use `notes sync` to repair or refresh the index, and `notes check` to see validation problems with line numbers. Use `notes push` and `notes pull` for explicit remote synchronization. Auto-push is controlled by `git.auto_push`; failures are warnings and are retried later. `notes recall`, `notes tick`, and `notes read` never access the network.

To use an existing vault on another device or restore one from a remote, run `notes init --remote <git-url>`. The command inspects the remote before creating anything and clones it when it contains a vault. Delivery history is device-local and is not restored.

## Configuration

`~/.notes/config.toml` is created once and remains user-owned. These are the defaults:

```toml
[generator]
command = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "--color", "never", "-"]
timeout_seconds = 300

[git]
auto_push = false
remote = "origin"

[notifications]
sound = true
sound_id = "message-new-instant"

[recall]
limit = 10
min_score = 1.0
path_boost = 2.0
```

## Development

```shell
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run ty check
```

Implementation details and design decisions are recorded in [`docs/plans/completed/20260902-notes-v1.md`](docs/plans/completed/20260902-notes-v1.md). Repository conventions are in [`CLAUDE.md`](CLAUDE.md).
