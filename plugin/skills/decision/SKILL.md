---
name: decision
description: Record a decision made in this session as a decision note in the personal vault at ~/.notes - what was chosen, why, and which alternatives lost - drafted by the notes CLI and saved only after the user confirms. Invoke on "record this decision", "we decided", "let's go with", "note the decision", "write down why", or right after a choice between alternatives has been settled.
---

# Decision

Record a choice made in this session as a `decision` note in the vault at `~/.notes`, through the `notes` CLI: what was chosen, why, and which alternatives lost, so the reasoning survives the conversation and the rejected options are not reopened by accident. The CLI owns the prompts, the validation, and the files; this skill gathers the facts, drives the draft, and gets the user's confirmation. Nothing is saved without that confirmation: a draft lives under `~/.notes/drafts/` and never enters search or the recall hook until `notes draft save` runs.

## Procedure

1. The kind is `decision`. When the session settled several unrelated choices, make one note per decision.
2. **Extract from the session**, never the transcript: the decision as a fact ("Task updates flow over Kafka"), every reason it won (the constraint, the failure of the previous approach, the trade-off accepted), each rejected alternative and why it lost, who decided and with whom, the scope (project, service, component), and the consequences (what changes, what must be migrated, what is now off the table). Add the project directories (`~/Dev/<project>`, with `~` kept as text), references (files, URLs, tracker issues), tags (the project, an issue ID such as `ATLAS-27`, the vault's existing spellings), and keywords (the components and the rejected options, so a search for the option that lost finds this note).
3. **Ask one targeted clarification question only if critical context is missing**: the decision itself or at least one reason. Otherwise do not ask.
4. Run `notes prompt decision` and shape the packet to what it expects.
5. Write the packet (below) to a temporary file with the Write tool, then run `notes draft create decision --json < <packet file>` in the background and continue the conversation; the generator can take a minute or more. The JSON names the draft `id`, `short_name`, and `abs_path`; a failure stores nothing.
6. Read the draft with `notes draft show <draft-id>` and show it to the user through `AskUserQuestion` with three options: **save**, **revise** (free-form feedback), **cancel**.
   - save: `notes draft save <draft-id> --json` (`--name "<short name>"` for a different filename slug); report the link it prints
   - revise: `notes draft revise <draft-id> "<feedback>" --json`, then show the new draft the same way
   - cancel: `notes draft discard <draft-id>`
7. After a save:
   - when the decision replaces an earlier one, find it with `notes search --json "<its terms>"` and offer `notes relate <new-id> supersedes <old-id>`; the old note keeps its content and gets the effective status `superseded`
   - when the note references a tracker issue and the decision matters to the team, offer to draft a concise comment for that issue; never write to a tracker without the user's explicit approval, and never suggest publishing personal reflections, opinions about people, or private plans

## Packet

A JSON object with what the decision prompt asks for. Conventional keys, all optional:

```json
{
  "title": "Project Atlas task updates over Kafka",
  "decision": "Task updates flow from the backend to Project Atlas over Kafka, not over WebSocket",
  "rationale": [
    "The WebSocket proxy dropped connections after 60 seconds",
    "Kafka already carries the run events"
  ],
  "rejected": [
    {
      "option": "WebSocket through the legacy proxy",
      "why": "timeouts, no replay"
    }
  ],
  "decided_by": "Sam with Alex, 2026-09-02 sync",
  "scope": "project-atlas service",
  "consequences": [
    "Migrate the task listener",
    "The proxy config is no longer needed"
  ],
  "supersedes": "the WebSocket decision of 2026-08-27",
  "paths": ["~/Dev/exampleco/project-atlas"],
  "tags": ["ATLAS-27", "project-atlas", "sync-worker"],
  "keywords": ["Kafka", "WebSocket", "proxy", "Atlas", "Project Atlas"],
  "references": ["CLAUDE.md", "ATLAS-27"],
  "cwd": "/home/user/Dev/exampleco/project-atlas",
  "language": "en"
}
```

Leave out what the session did not establish; the generator uses only the packet and never invents.

## When something fails

- `notes` is not found or reports no vault: tell the user to install the CLI (`uv tool install .` in the notes repository) and run `notes init`; do not write notes by hand.
- `notes draft create` fails: nothing was stored; fix the packet or report the generator's error.
- `notes draft save` reports validation errors: revise the draft with the errors as feedback, or fix the draft file at the path it names and save again.
