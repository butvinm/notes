---
name: note
description: Record a note of a kind the user added to the vault at ~/.notes themselves, drafted by the notes CLI and saved only after the user confirms. Invoke when the user names a kind explicitly ("save this as a recipe note", "add a meeting note", "make it a retro note") and that kind is not fact, idea, decision, or reminder.
---

# Note

Record a note of any kind the vault has, through the `notes` CLI. The four built-in kinds have their own skills; this one covers the kinds the user added under `~/.notes/types/`, whose prompt and template only the vault knows. The CLI owns the prompts, the validation, and the files; this skill gathers the facts, drives the draft, and gets the user's confirmation. Nothing is saved without that confirmation: a draft lives under `~/.notes/drafts/` and never enters search or the recall hook until `notes draft save` runs.

## Procedure

1. **Settle the kind.** List what the vault has with `ls ~/.notes/types/`; the kind must be one of those directories. A kind the user named that is not there is an error worth reporting, not a kind to invent. When the wording points at a built-in kind, hand over instead: `/notes:fact`, `/notes:idea`, `/notes:decision`, `/notes:reminder`.
2. Run `notes prompt <kind>` before extracting anything. It prints the shared prompt, the kind's own prompt (what to capture, what is critical, how to reason), and the template; that prompt, not this skill, defines what the note needs.
3. **Extract from the session**, never the transcript: exactly what the kind's prompt asks for, in the user's words. Add the project directories (`~/Dev/<project>`, with `~` kept as text), references (files, URLs, tracker issues), tags (the project, an issue ID such as `ATLAS-27`, the vault's existing spellings), and keywords (synonyms, abbreviations, alternative names).
4. **Ask one targeted clarification question only if critical context is missing**: something the kind's prompt calls critical that the session did not give. Otherwise do not ask.
5. Write the packet (below) to a temporary file with the Write tool, then run `notes draft create <kind> --json < <packet file>` in the background and continue the conversation; the generator can take a minute or more. The JSON names the draft `id`, `short_name`, and `abs_path`; a failure stores nothing.
6. Read the draft with `notes draft show <draft-id>` and show it to the user through `AskUserQuestion` with three options: **save**, **revise** (free-form feedback), **cancel**.
   - save: `notes draft save <draft-id> --json` (`--name "<short name>"` for a different filename slug); report the link it prints
   - revise: `notes draft revise <draft-id> "<feedback>" --json`, then show the new draft the same way
   - cancel: `notes draft discard <draft-id>`
7. After a save, do what the kind's prompt asks for afterwards, if anything. When the note references a tracker issue and holds durable team-relevant information, offer to draft a concise comment for that issue; never write to a tracker without the user's explicit approval, and never suggest publishing personal reflections, opinions about people, or private plans.

## Packet

A JSON object shaped by what the kind's prompt asks for, so its keys vary by kind. The conventional ones, all optional:

```json
{
  "title": "Weekly retro: the Kafka migration week",
  "facts": [
    "The task listener migrated on Tuesday",
    "Two days were lost to the proxy timeouts"
  ],
  "when": "2026-09-02",
  "people": ["Alex (platform team)"],
  "paths": ["~/Dev/exampleco/project-atlas"],
  "tags": ["project-atlas", "ATLAS-27"],
  "keywords": ["retro", "Kafka", "migration"],
  "references": ["ATLAS-27"],
  "schedule": "2026-10-01 10:00",
  "cwd": "/home/user/Dev/exampleco/project-atlas",
  "language": "en"
}
```

Leave out what the session did not establish; the generator uses only the packet and never invents. Include `schedule` only when the kind's template carries a `schedule` field; it may be relative (`in 3 days`, `tomorrow 09:00`, `2026-10-01 10:00`) and `notes draft save` converts it to the canonical form.

## When something fails

- `notes` is not found or reports no vault: tell the user to install the CLI (`uv tool install .` in the notes repository) and run `notes init`; do not write notes by hand.
- `notes prompt <kind>` reports an unknown kind: list the known ones for the user and offer to create the kind by adding `~/.notes/types/<kind>/template.md` (and a `prompt.md` next to it); do not fall back to a different kind silently.
- `notes draft create` fails: nothing was stored; fix the packet or report the generator's error.
- `notes draft save` reports validation errors: revise the draft with the errors as feedback, or fix the draft file at the path it names and save again.
