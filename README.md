# notes

`notes` is a local-first knowledge base for decisions, facts, ideas, commitments, events, and reminders. Notes stay as ordinary Markdown files, Git keeps their history, and a disposable SQLite index makes them searchable from the command line or Claude Code.

## Features

- Plain Markdown is the source of truth. The SQLite search index can always be rebuilt.
- Fast full-text search with exact tag and issue matching, English stemming, and Russian lemmatization.
- One-shot and recurring reminders delivered through desktop notifications.
- Automatic Git commits, with optional push to a private remote.
- Optional AI-assisted drafting with an explicit review step before anything is saved.
- A Claude Code plugin that recalls relevant notes and provides capture, decision, reminder, and recall skills.

Everything except AI-assisted drafting works offline.

## Requirements

- Python 3.12 or newer
- uv
- Git

Desktop notifications additionally require Linux with `systemd`, `notify-send`, and optionally `canberra-gtk-play`. The Claude Code hook requires `jq`.

## Installation

```shell
git clone https://github.com/butvinm/notes.git
cd notes
uv tool install .
notes --version
```

After upgrading the repository, run `uv tool install --reinstall .`.

## Quick start

Create a vault:

```shell
notes init
```

`notes init` uses `~/.notes/`. In an interactive terminal it can also configure a private Git remote and desktop notifications. The same options are available non-interactively:

```shell
notes init --remote <git-url> --auto-push --enable-notifications
```

Create and find a note:

```shell
notes new decision "Use PostgreSQL for the event log"
notes search "event storage"
notes list --kind decision
```

Create a reminder and enable delivery:

```shell
notes new reminder "Review backup restore procedure"
notes notifications enable
notes notifications status
```

## Note format

A note ID is its path relative to the vault. Files use the name `notes/YYYY-MM-DD-<slug>.md`; the first H1 is the title.

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

`kind` and `status` are required. The built-in kinds are `decision`, `fact`, `promise`, `event`, `reminder`, and `idea`. A `promise` or `reminder` also requires a `schedule`. Unknown frontmatter keys are preserved, so the format can be extended without changing the CLI.

Relations can be `supersedes`, `child`, or `related`. Superseded notes remain unchanged on disk but are hidden from normal search unless an exact tag or issue ID matches.

## Schedules and reminders

Schedules are stored in one of two canonical forms:

```yaml
schedule: at 2026-09-09T10:00:00+03:00
schedule: every 3 days from 2026-09-02T10:00:00+03:00
```

Interactive commands and draft saving also accept `in 3 days`, `in a week`, `tomorrow`, `tomorrow 09:30`, `today 18:00`, `2026-10-01`, `2026-10-01 10:00`, and `every 2 weeks`. Dates without a time use 09:00 in the local time zone.

`notes notifications enable` installs `notes-tick.timer`, which runs `notes tick` every minute. Each occurrence is delivered once and stays unread until `notes read <id>`. Delivery and read state live only in `index.sqlite`; deleting the index loses that state but not the notes.

## Commands

All commands support `--json` for machine-readable output.

- Vault: `notes init`, `notes sync`, `notes check`, `notes push`, `notes pull`
- Create and organize: `notes new`, `notes edit`, `notes relate`, `notes move`
- Read and search: `notes show`, `notes list`, `notes search`, `notes recall`
- Reminders: `notes tick`, `notes read`, `notes notifications enable`, `notes notifications disable`, `notes notifications status`
- Drafting: `notes prompt`, `notes draft create`, `notes draft list`, `notes draft show`, `notes draft revise`, `notes draft save`, `notes draft discard`

Run `notes <command> --help` for options and examples.

## Search and recall

`notes search <query>` searches titles, tags, keywords, and bodies. Exact tags and tracker issue IDs rank above text matches. Archived and superseded notes are excluded by default; add `--all` to include them.

`notes recall [<query>]` produces a small context block for tools and agents. It lists unread reminders first, then exact matches, then the strongest text matches. It emits titles, paths, and match reasons, never note bodies.

## Sync and backup

Each vault command indexes changed Markdown and commits valid changes to the vault's Git repository. Invalid notes stay out of the index and Git until corrected; `notes check` reports each problem with its line number.

Enable `git.auto_push` during initialization or in `config.toml` to push commits automatically. Auto-push failures are warnings and are retried later. `notes recall`, `notes tick`, and `notes read` never access the network.

To use an existing vault on another device or restore one from a remote:

```shell
notes init --remote <git-url>
```

The command inspects the remote before creating anything and clones it when it contains a vault. Delivery history is device-local and is not restored.

## AI-assisted drafting

Drafting is optional and provider-independent. `notes draft create <kind>` accepts a JSON context object on standard input, invokes the configured generator, and stores the result outside the index. Review it with `notes draft show`, refine it with `notes draft revise`, then use `notes draft save` or `notes draft discard`.

The generator receives a prompt on standard input and must return a JSON object:

```json
{
  "short_name": "postgresql-event-log",
  "markdown": "---\nkind: decision\n..."
}
```

No generated note becomes searchable, committed, or eligible for reminders until it is saved.

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

## Claude Code plugin

The plugin in `plugin/` adds a prompt hook for automatic recall and the `/notes:capture`, `/notes:decision`, `/notes:reminder`, and `/notes:recall` skills. The hook does nothing when the CLI, vault, or `jq` is unavailable.

This repository is also the plugin marketplace. Add it once, then install the plugin, both from inside Claude Code:

```
/plugin marketplace add butvinm/notes
/plugin install notes@notes
```

A local checkout works the same way: `/plugin marketplace add <path-to-repository>`.

The `notes` executable must be installed and the vault initialized; see [Installation](#installation) above. To try the plugin from a checkout without installing it:

```shell
claude --plugin-dir ./plugin
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
