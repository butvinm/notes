---
kind: decision
status: active
paths: [~/Dev/exampleco/project-atlas]
tags: [ATLAS-27, project-atlas]
keywords: [WebSocket, push, Project Atlas]
references: []
---

# Project Atlas task updates over WebSocket

**Decision:** The backend pushes task updates to Project Atlas over a WebSocket connection.

**Rationale:** One connection per client is simple and needs no broker.
