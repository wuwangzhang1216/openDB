# OpenDB Agent Protocol

This protocol adapts the useful part of gbrain's "brain-first" habit to
OpenDB without importing gbrain's heavier runtime. The goal is simple: agents
should use OpenDB as local context before spending time or money elsewhere.

## Brain-First Lookup

When a user asks about a topic, project, person, file, decision, or prior task:

1. Call `opendb_info` if the active workspace is unclear.
2. Call `opendb_memory_recall` for durable facts, preferences, decisions, and
   previous outcomes.
3. Call `opendb_search` for indexed files and documents.
4. Call `opendb_read` on the best matching files, with page or line limits when
   possible.
5. Use external search only after OpenDB has no useful local context, or when
   the user explicitly asks for current public information.

This keeps answers grounded in the user's own workspace. External data can fill
gaps, but it should not replace local context.

## Memory Write Policy

Store a memory when it is likely to matter in a future session.

- `semantic`: stable facts, preferences, project decisions, constraints.
- `episodic`: dated events, task outcomes, incidents, releases, meetings.
- `procedural`: reusable workflows, repo-specific instructions, recurring fixes.

Use `source` honestly:

- `user_explicit`: the user directly stated it should be remembered.
- `ai_inference`: inferred from work, but not directly commanded.
- `tool_extraction`: derived from files, tools, or benchmark runs.
- `unknown`: fallback only when provenance is genuinely unclear.

Pin sparingly. Pinned memories are for context that should reliably win recall,
such as project identity, durable user preferences, or critical constraints.

Never store secrets, credentials, one-off logs, or speculation that would be
harmful if repeated as fact.

## Workspace Research Flow

For repository or document research:

1. Start with `opendb_info` and `opendb_glob` to learn what exists.
2. Use `opendb_search` broadly to locate candidate files.
3. Use `opendb_read` narrowly to inspect exact pages, lines, or sheets.
4. Summarize with citations to file names, page numbers, or line numbers.
5. Store only durable conclusions as memories, not every intermediate finding.

## What Not To Add By Default

OpenDB's current strength is that it works locally, quickly, and without API
keys. New ideas should preserve that.

- Do not add vector search just because a neighboring project uses it.
- Do not add a graph database for links that can live in SQLite/Postgres tables.
- Do not add background workers for work that can happen during indexing.
- Do not add a large skill runtime when a short agent protocol is enough.

Good additions should look like small deterministic surfaces: provenance fields,
query capture, file links, backlink counts, export commands, and focused tests.
