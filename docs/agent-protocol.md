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

Write memories as durable atoms, not transcripts:

- Prefer one complete, context-free sentence over copied chat fragments.
- Skip greetings, small talk, one-off commands, and task-local instructions.
- Store preferences and constraints only when they sound stable beyond the
  current turn.
- Store episodic memories with absolute dates when they are known.
- Merge strongly related facts into one memory instead of creating fragments.

Use `source` honestly:

- `user_explicit`: the user directly stated it should be remembered.
- `ai_inference`: inferred from work, but not directly commanded.
- `tool_extraction`: derived from files, tools, or benchmark runs.
- `unknown`: fallback only when provenance is genuinely unclear.

Use `metadata` for drill-down evidence. Prefer this shape when a memory comes
from a file, tool result, or previous recall:

```json
{
  "date": "2026-05-14",
  "scene": "Release planning for OpenDB memory recall",
  "evidence": {
    "file": "docs/agent-protocol.md",
    "lines": "23-52",
    "tool": "opendb_read"
  },
  "source_message_ids": ["turn-123"]
}
```

`metadata.evidence` may also be a list when several files or tool calls support
the same memory. Keep evidence pointers small: file/path, page or line range,
and the tool/query used to obtain it.

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
6. When storing a conclusion, include evidence metadata that points back to the
   exact file/page/line range when possible.

## Memory Recall Output

Recall results should be treated as a starting point, not unquestioned truth.
Each result includes provenance fields when available:

- `source`: where the memory came from.
- `confidence`: current confidence after recall reinforcement and decay.
- `superseded_id`: the memory this one replaced, when relevant.
- `metadata.evidence`: a deterministic pointer back to files or tool outputs.

For multi-term queries, OpenDB filters weak tail results that only match one
broad token. Prefer specific recall queries with two or more meaningful terms
when you want a narrow answer.

If a recalled memory is important for the answer and evidence is available, use
`opendb_read` to verify the cited file, page, or line range before relying on
the memory.

## What Not To Add By Default

OpenDB's current strength is that it works locally, quickly, and without API
keys. New ideas should preserve that.

- Do not add vector search just because a neighboring project uses it.
- Do not add a graph database for links that can live in SQLite/Postgres tables.
- Do not add background workers for work that can happen during indexing.
- Do not add a large skill runtime when a short agent protocol is enough.

Good additions should look like small deterministic surfaces: provenance fields,
query capture, file links, backlink counts, export commands, and focused tests.
