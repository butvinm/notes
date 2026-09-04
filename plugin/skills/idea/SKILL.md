---
name: idea
description: Record an unsettled possibility as an idea note in the personal vault at ~/.notes, drafted by the notes CLI and saved only after the user confirms. Invoke on "what if we", "idea:", "we could maybe", "worth trying", "park this", or when the user floats an option nobody has chosen or scheduled yet.
---

# Idea

Record a possibility as an `idea` note in the vault at `~/.notes`, through the `notes` CLI: what it is, why it might matter, and what is still open. The CLI owns the prompts, the validation, and the files; this skill gathers the facts, drives the draft, and gets the user's confirmation. Nothing is saved without that confirmation: a draft lives under `~/.notes/drafts/` and never enters search or the recall hook until `notes draft save` runs.

## Procedure

1. The kind is `idea`, for something nobody has chosen or scheduled. Once it is chosen it belongs to `/notes:decision`; once it must surface at a moment it belongs to `/notes:reminder`; something already true belongs to `/notes:fact`.
2. **Extract from the session**, never the transcript: the idea itself, stated so it can be picked up cold; why it might matter (the problem it would solve, what it would make possible, what it would cost or break); the open questions that keep it from being a decision; and where it came from. Add the project directories (`~/Dev/<project>`, with `~` kept as text), references (files, URLs, tracker issues), tags (the project, an issue ID such as `ATLAS-27`, the vault's existing spellings), and keywords (synonyms, abbreviations, alternative names).
3. **Ask one targeted clarification question only if critical context is missing**: the problem the idea addresses, when the session leaves it unclear. Otherwise do not ask; an idea is allowed to be unfinished.
4. Run `notes prompt idea` and shape the packet to what it expects.
5. Write the packet (below) to a temporary file with the Write tool, then run `notes draft create idea --json < <packet file>` in the background and continue the conversation; the generator can take a minute or more. The JSON names the draft `id`, `short_name`, and `abs_path`; a failure stores nothing.
6. Read the draft with `notes draft show <draft-id>` and show it to the user through `AskUserQuestion` with three options: **save**, **revise** (free-form feedback), **cancel**.
   - save: `notes draft save <draft-id> --json` (`--name "<short name>"` for a different filename slug); report the link it prints
   - revise: `notes draft revise <draft-id> "<feedback>" --json`, then show the new draft the same way
   - cancel: `notes draft discard <draft-id>`
7. After a save, when the idea reopens something the vault already settled, find that note with `notes search --json "<its terms>"` and name it, so the user can decide whether the decision is being revisited.

## Packet

A JSON object with what the idea prompt asks for. Conventional keys, all optional:

```json
{
  "title": "Lemma search for Russian queries",
  "idea": "Index a lemmatized copy of every note so a Russian query matches other inflections",
  "why": [
    "FTS5 porter stemming only covers English",
    "Half the vault is written in Russian"
  ],
  "open_questions": [
    "Which lemmatizer runs offline without a heavy dependency?",
    "Does the index stay small enough to rebuild in a second?"
  ],
  "cost": "A second FTS table and a rebuild step in sync",
  "origin": "Noticed while searching for an old note in Russian",
  "paths": ["~/Dev/notes"],
  "tags": ["notes", "search"],
  "keywords": ["lemma", "lemmatization", "morphology", "FTS5"],
  "references": ["~/Dev/notes/src/notes/search.py"],
  "cwd": "/home/user/Dev/notes",
  "language": "en"
}
```

Leave out what the session did not establish; the generator uses only the packet and never invents. An idea carries no `schedule`: a note that must surface at a future moment is a reminder.

## When something fails

- `notes` is not found or reports no vault: tell the user to install the CLI (`uv tool install .` in the notes repository) and run `notes init`; do not write notes by hand.
- `notes draft create` fails: nothing was stored; fix the packet or report the generator's error.
- `notes draft save` reports validation errors: revise the draft with the errors as feedback, or fix the draft file at the path it names and save again.
