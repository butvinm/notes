---
name: fact
description: Record something true or something that happened as a fact note in the personal vault at ~/.notes, drafted by the notes CLI and saved only after the user confirms. Invoke on "remember that", "note that", "turns out", "for the record", or when the user states how a system behaves, reports an outcome or an incident, or gives a preference worth keeping beyond the session.
---

# Fact

Record what is the case as a `fact` note in the vault at `~/.notes`, through the `notes` CLI: the statement, where it came from, and when it holds. The CLI owns the prompts, the validation, and the files; this skill gathers the facts, drives the draft, and gets the user's confirmation. Nothing is saved without that confirmation: a draft lives under `~/.notes/drafts/` and never enters search or the recall hook until `notes draft save` runs.

## Procedure

1. The kind is `fact`, for something true about a system, a process, or a preference, and for something that happened. A choice between alternatives belongs to `/notes:decision`; a possibility nobody has chosen belongs to `/notes:idea`; anything that must surface at a future moment belongs to `/notes:reminder`.
2. **Extract from the session**, never the transcript: the statement in the user's words, precise enough to act on (names, versions, limits, defaults, addresses, exact behavior); where it was learned (a file and line, a command and its output, a document, a person); and when and under which conditions it holds. For something that happened, the occurrence in the past tense, its date, and its consequences. One fact per note; split unrelated facts into separate ones. Add the project directories (`~/Dev/<project>`, with `~` kept as text), references (files, URLs, tracker issues), tags (the project, an issue ID such as `ATLAS-27`, the vault's existing spellings), and keywords (synonyms, abbreviations, alternative names, and for an incident the affected systems and error names).
3. **Ask one targeted clarification question only if critical context is missing**: a fact without a source, something that happened without a date. Otherwise do not ask.
4. Run `notes prompt fact` and shape the packet to what it expects.
5. Write the packet (below) to a temporary file with the Write tool, then run `notes draft create fact --json < <packet file>` in the background and continue the conversation; the generator can take a minute or more. The JSON names the draft `id`, `short_name`, and `abs_path`; a failure stores nothing.
6. Read the draft with `notes draft show <draft-id>` and show it to the user through `AskUserQuestion` with three options: **save**, **revise** (free-form feedback), **cancel**.
   - save: `notes draft save <draft-id> --json` (`--name "<short name>"` for a different filename slug); report the link it prints
   - revise: `notes draft revise <draft-id> "<feedback>" --json`, then show the new draft the same way
   - cancel: `notes draft discard <draft-id>`
7. After a save, when the note references a tracker issue and holds durable team-relevant information (an agreed interface, a deadline, the cause of an incident), offer to draft a concise comment for that issue. Never write to a tracker without the user's explicit approval, and never suggest publishing personal reflections, opinions about people, or private plans.

## Packet

A JSON object with what the fact prompt asks for. Conventional keys, all optional:

```json
{
  "title": "Kafka topic retention is 7 days on the ExampleCo cluster",
  "facts": [
    "Retention is 7 days for every topic",
    "Set in server.properties, not per topic"
  ],
  "source": "~/Dev/exampleco/infra/kafka/server.properties, checked 2026-09-02",
  "when": "2026-09-02",
  "consequences": ["The replay window for the task listener is one week"],
  "people": ["Alex (platform team)"],
  "paths": ["~/Dev/exampleco"],
  "tags": ["exampleco", "kafka", "ATLAS-31"],
  "keywords": ["retention", "topic TTL", "log.retention.hours"],
  "references": ["~/Dev/exampleco/infra/kafka/server.properties", "ATLAS-31"],
  "cwd": "/home/user/Dev/exampleco",
  "language": "en"
}
```

Leave out what the session did not establish; the generator uses only the packet and never invents. A fact carries no `schedule`: a note that must surface at a future moment is a reminder.

## When something fails

- `notes` is not found or reports no vault: tell the user to install the CLI (`uv tool install .` in the notes repository) and run `notes init`; do not write notes by hand.
- `notes draft create` fails: nothing was stored; fix the packet or report the generator's error.
- `notes draft save` reports validation errors: revise the draft with the errors as feedback, or fix the draft file at the path it names and save again.
