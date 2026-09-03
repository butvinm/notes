# Decision

A decision records a choice that was made, so the reasoning survives the conversation and the rejected alternatives are not reopened by accident.

## What to capture

- **Decision:** what was chosen, in one or two sentences, stated as a fact ("Task updates flow over Kafka"), not as a plan or a wish.
- **Rationale:** why this option won: the constraint, the failure of the previous approach, the trade-off accepted. Name each rejected alternative and the reason it lost when the packet mentions them.
- Who decided and with whom, when the packet says so (a person, a team, a meeting).
- The scope: the project, service, or component the decision binds.
- The consequences: what changes, what must be migrated, what is now off the table.

## What is critical

The decision itself and at least one reason. Without both, the note is not worth saving and the assistant should ask before drafting. Everything else is optional; leave a part out rather than guess it.

## How to reason

- Tag the note with the project and the tracker issue when the packet gives them.
- Put the components and the rejected alternatives in `keywords` (`WebSocket`, `Kafka`, `proxy`), so a search for the option that lost finds this note.
- When the packet says the decision replaces an earlier one, say so in the body and name the earlier decision; the developer links the notes with `notes relate <new> supersedes <old>`.
