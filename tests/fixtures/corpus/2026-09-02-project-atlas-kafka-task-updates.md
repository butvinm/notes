---
kind: decision
status: active
paths: [~/Dev/exampleco/project-atlas]
tags: [ATLAS-27, project-atlas, sync-worker]
keywords: [Kafka, tasks, runs, launches, jobs, Atlas, Project Atlas, replay]
references:
  - CLAUDE.md
  - https://tracker.example/browse/ATLAS-27
related:
  - relation: supersedes
    note: 2026-08-27-project-atlas-websocket.md
---

# Project Atlas task updates over Kafka

**Decision:** Task, run, launch, and job updates flow from the backend to Project Atlas over Kafka instead of WebSocket. Tracked in ATLAS-27.

**Rationale:** The proxy terminates WebSocket connections and drops them under load; Kafka gives durable delivery.
