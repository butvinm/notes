---
name: capture
description: Capture a fact or an idea from this session as a note in the personal vault at ~/.notes, drafted by the notes CLI and saved only after the user confirms. Invoke on "save this", "remember this", "note this down", "capture that", "keep this for later", or when the user states something true, reports what happened, or floats a possibility worth keeping beyond the session.
---

# Capture

Turn what the session established into one note in the vault at `~/.notes`, through the `notes` CLI. The CLI owns the prompts, the validation, and the files; this skill gathers the facts, drives the draft, and gets the user's confirmation. Nothing is saved without that confirmation: a draft lives under `~/.notes/drafts/` and never enters search or the recall hook until `notes draft save` runs.

## Procedure

1. **Pick the kind** from the content, not from the wording of the request:
   - `fact`: something that is the case - true about a system, a process, or a preference, or something that happened - with a source; the default
   - `idea`: a possibility that is neither decided nor scheduled
   - a choice between alternatives belongs to `/notes:decision`; anything that must surface at a future moment, including a commitment owed to someone, belongs to `/notes:reminder`
2. **Extract from the session**, never the transcript: the facts in the user's words (what, who, when, where it came from), the project directories (`~/Dev/<project>`, with `~` kept as text), references (files, URLs, tracker issues), tags (the project, an issue ID such as `ATLAS-27`, the vault's existing spellings), and keywords (synonyms, abbreviations, alternative names).
3. **Ask one targeted clarification question only if critical context is missing**: a fact without a source, something that happened without a date. Otherwise do not ask.
4. Run `notes prompt <kind>` and shape the packet to what the kind expects.
5. Write the packet (below) to a temporary file with the Write tool, then run `notes draft create <kind> --json < <packet file>` in the background and continue the conversation; the generator can take a minute or more. The JSON names the draft `id`, `short_name`, and `abs_path`; a failure stores nothing.
6. Read the draft with `notes draft show <draft-id>` and show it to the user through `AskUserQuestion` with three options: **save**, **revise** (free-form feedback), **cancel**.
   - save: `notes draft save <draft-id> --json` (`--name "<short name>"` for a different filename slug); report the link it prints
   - revise: `notes draft revise <draft-id> "<feedback>" --json`, then show the new draft the same way
   - cancel: `notes draft discard <draft-id>`
7. After a save, when the note references a tracker issue and holds durable team-relevant information (an agreed interface, a deadline, the cause of an incident), offer to draft a concise comment for that issue. Never write to a tracker without the user's explicit approval, and never suggest publishing personal reflections, opinions about people, or private plans.

## Packet

A JSON object with what the kind's prompt asks for. Conventional keys, all optional:

```json
{
  "title": "Kafka topic retention is 7 days on the ExampleCo cluster",
  "facts": [
    "Retention is 7 days for every topic",
    "Set in server.properties, not per topic"
  ],
  "source": "~/Dev/exampleco/infra/kafka/server.properties, checked 2026-09-02",
  "when": "2026-09-02",
  "people": ["Alex (platform team)"],
  "paths": ["~/Dev/exampleco"],
  "tags": ["exampleco", "kafka", "ATLAS-31"],
  "keywords": ["retention", "topic TTL", "log.retention.hours"],
  "references": ["~/Dev/exampleco/infra/kafka/server.properties", "ATLAS-31"],
  "cwd": "/home/user/Dev/exampleco",
  "language": "en"
}
```

Leave out what the session did not establish; the generator uses only the packet and never invents. Neither kind carries a `schedule`: a note that must surface at a future moment is a reminder, so use `/notes:reminder` instead.

## When something fails

- `notes` is not found or reports no vault: tell the user to install the CLI (`uv tool install .` in the notes repository) and run `notes init`; do not write notes by hand.
- `notes draft create` fails: nothing was stored; fix the packet or report the generator's error.
- `notes draft save` reports validation errors: revise the draft with the errors as feedback, or fix the draft file at the path it names and save again.
