---
name: reminder
description: Create a reminder note in the personal vault at ~/.notes that fires as a desktop notification at a moment or on a rhythm - the action, the reason, and a schedule in canonical or relative form - drafted by the notes CLI and saved only after the user confirms. Invoke on "remind me", "ping me", "don't let me forget", "in three days check", "every week", or any request to be told something later.
---

# Reminder

Create a `reminder` note in the vault at `~/.notes`, through the `notes` CLI: the action to take, why, and when. The note is delivered as a desktop notification by `notes tick` (the systemd user timer that `notes notifications enable` installs) and stays unread, listed by `notes list --unread` and by the recall hook, until the user runs `notes read <id>`. The CLI owns the prompts, the validation, and the files; this skill gathers the facts, settles the schedule, and gets the user's confirmation. Nothing is saved without that confirmation: a draft lives under `~/.notes/drafts/` and never enters search, the hook, or the timer until `notes draft save` runs.

## Procedure

1. The kind is `reminder`. A commitment with a due date owed to someone is a `promise` instead: use `/notes:capture`.
2. **Settle the schedule**, always. Derive it from the request in one of the forms the CLI accepts, in the user's local time:
   - canonical: `at 2026-09-09T10:00:00+03:00` for one moment, `every 3 days from 2026-09-02T10:00:00+03:00` for a rhythm (units `minutes`, `hours`, `days`, `weeks`)
   - relative: `in 3 days`, `in a week`, `tomorrow 09:00`, `today 18:00`, `2026-10-01 10:00`, `every 2 weeks`
   - a day without a time means 09:00; prefer a single moment over a rhythm, since a recurring reminder fires until the note gets `status: archived`
   - when the request gives no moment and no rhythm, ask for it: the schedule is the critical context, so this is the one targeted clarification question the skill asks
3. **Extract from the session**, never the transcript: the action (what to do, where, what a good outcome looks like), the reason (what happens if it is skipped), the project directories (`~/Dev/<project>`, with `~` kept as text), references (files, URLs, tracker issues), tags (the project, an issue ID such as `ATLAS-27`, the tags of the note the reminder is about), and keywords (synonyms, abbreviations, alternative names).
4. Run `notes prompt reminder` and shape the packet to what it expects.
5. Write the packet (below) to a temporary file with the Write tool, then run `notes draft create reminder --json < <packet file>` in the background and continue the conversation; the generator can take a minute or more. The JSON names the draft `id`, `short_name`, and `abs_path`; a failure stores nothing.
6. Read the draft with `notes draft show <draft-id>` and show it to the user through `AskUserQuestion` with three options: **save**, **revise** (free-form feedback), **cancel**.
   - save: `notes draft save <draft-id> --json` (`--name "<short name>"` for a different filename slug); report the link it prints and the canonical schedule the saved note carries
   - revise: `notes draft revise <draft-id> "<feedback>" --json`, then show the new draft the same way
   - cancel: `notes draft discard <draft-id>`
7. After a save, when the user asks whether the notification will arrive, run `notes notifications status`; when the timer is not enabled, tell the user to run `notes notifications enable`.

## Packet

A JSON object with what the reminder prompt asks for. Conventional keys, `schedule` always present:

```json
{
  "title": "Review the Project Atlas Kafka decision",
  "action": "Reopen the Kafka decision and check the consumer lag dashboards before the sprint review",
  "why": "The migration was rushed; if lag grows the WebSocket path must come back",
  "schedule": "in 3 days",
  "about": "notes/2026-09-02-project-atlas-kafka-task-updates.md",
  "paths": ["~/Dev/exampleco/project-atlas"],
  "tags": ["ATLAS-27", "project-atlas"],
  "keywords": ["Kafka", "consumer lag", "sprint review"],
  "references": ["ATLAS-27"],
  "cwd": "/home/user/Dev/exampleco/project-atlas",
  "language": "en"
}
```

The generator copies a relative `schedule` as given and `notes draft save` converts it to the canonical form; a draft whose schedule is missing or malformed fails to save with a line number, so revise it or fix the draft file and save again. Leave out what the session did not establish; the generator uses only the packet and never invents.

## When something fails

- `notes` is not found or reports no vault: tell the user to install the CLI (`uv tool install .` in the notes repository) and run `notes init`; do not write notes by hand.
- `notes draft create` fails: nothing was stored; fix the packet or report the generator's error.
- `notes draft save` reports validation errors: revise the draft with the errors as feedback, or fix the draft file at the path it names and save again.
