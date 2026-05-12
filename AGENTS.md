# OpenDB Agent Protocol

OpenDB is an AI-native local database for files, search, and long-term memory.
Keep the runtime simple: prefer the existing MCP tools and SQLite/Postgres FTS
before adding new infrastructure.

## Read First

- Use `opendb_info` to understand the active workspace when context is unclear.
- Use `opendb_search` before external search whenever the question might be
  answered by indexed files or stored memories.
- Use `opendb_read` after search finds a relevant file; read the smallest useful
  page or line range.
- Use `opendb_memory_recall` before answering questions about user preferences,
  project decisions, past work, or recurring workflows.

## Write Carefully

- Store memories only when the information should survive future sessions.
- Use `semantic` for stable facts, `episodic` for dated events or task outcomes,
  and `procedural` for reusable workflows.
- Pin only critical context that should reliably surface later.
- Do not store secrets, credentials, or transient observations as memories.

## Design Bias

- Keep OpenDB zero-config and useful without embedding APIs.
- Prefer deterministic indexing, FTS, timestamps, provenance, and small schemas.
- Avoid adding graph databases, background job systems, OAuth dashboards, or
  agent orchestration unless the simpler path has clearly failed.

For the fuller workflow, see `docs/agent-protocol.md`.
