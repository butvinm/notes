---
name: recall
description: Find and read notes from the personal vault at ~/.notes before answering - search by issue ID, tag, or text, list unread reminders, and read the chosen notes in full. Invoke on "what did we decide about", "did I note", "check my notes", "what do my notes say", "recall", "look it up in notes", or when a past decision, fact, idea, or reminder could change the answer.
---

# Recall

Answer from the vault, not from memory of the conversation: find the candidate notes, read the ones that matter in full, then reason. The hook already injects a bounded block (unread notes, then exact and text matches) on every prompt; this skill is for going deeper than that block.

## Procedure

1. Build the queries from the question: issue IDs (`ATLAS-27`), tags, project names, and the distinctive terms in the user's language (Russian queries are lemmatized, so a different inflection still matches).
2. Run, in parallel when several apply:
   - `notes recall --json --cwd "<project directory>" -- "<query>"`: unread deliveries, exact issue-ID and tag matches (any status), and the best text matches, notes with a `paths` entry covering the directory ranked higher
   - `notes search --json "<query>"` for a wider ranked list (`--limit 30`); add `--all` when the question is about history, since archived and superseded notes are hidden by default unless matched exactly
   - `notes list --json` with `--tag`, `--kind`, `--path`, `--status`, or `--unread` when the question is a listing rather than a search
3. Read every note you intend to rely on with `notes show <id>` before reasoning about it; titles and reasons are not enough. Check the status: a `superseded` note tells what was decided before, not what holds now, and its successor is the note whose `related` list `supersedes` it; an `archived` note is kept for history.
4. Answer with the notes as links (`[notes/<file>](<absolute path>) - Title`), say which are superseded or archived, and quote the note rather than paraphrase when the wording matters (an exact limit, a config key, a command).
5. When the user acts on an unread reminder, offer `notes read <id>`; showing a note never marks it read. When nothing matches, say so and offer `/notes:capture` to record what the user tells you now.

## Notes

- Nothing here writes a note: the commands sync the index and may auto-commit (and push, when configured) edits made by hand in the vault, but never change a note's content. Use `/notes:capture`, `/notes:decision`, or `/notes:reminder` to add one.
- `notes show <id>` prints the file verbatim; `notes show --json <id>` gives the metadata, the effective status, the unread count, the outgoing relations, and the body.
- `notes` not found or no vault: tell the user to install the CLI (`uv tool install .` in the notes repository) and run `notes init`.
