# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.5.0] — 2026-04-11

### Added
- **Runtime workspace management.** An agent (or a human) can now list, add,
  switch, and remove workspaces at runtime without restarting the server.
  Already-opened workspaces switch in sub-millisecond time — the storage-layer
  registry keeps each workspace's SQLite connection warm, so switching is just
  a pointer flip plus a settings patch under an `asyncio.Lock`.
- New global workspace registry persisted at `~/.opendb/workspaces.json`
  (overridable via the `FILEDB_STATE_DIR` environment variable). Each entry
  records `id`, `name`, `root`, `backend`, `created_at`, and `last_used_at`.
- Five new REST endpoints under `/workspaces`:
  - `GET /workspaces` — list all registered workspaces (active one first)
  - `POST /workspaces` — register (and optionally switch to) a new workspace
  - `GET /workspaces/active` — return the currently active workspace
  - `PUT /workspaces/active` — switch by id or root path
  - `DELETE /workspaces/{id}` — unregister a workspace (files untouched;
    use `?force=true` to remove the currently-active one)
- Five new MCP tools exposed by the server:
  - `opendb_list_workspaces`
  - `opendb_current_workspace`
  - `opendb_use_workspace` — accepts either a workspace id or a root path
  - `opendb_add_workspace`
  - `opendb_remove_workspace`
- `opendb_info` / `GET /info` now include a `workspace` block with the active
  workspace's identity (id, name, root, backend, last-used time), so agents
  can answer "which workspace am I in?" in a single call.
- New CLI subcommand group: `opendb workspace list | add | use | current | remove`.

### Changed
- `opendb_core/workspace.py` — factored the settings-patching block and the
  parser registration block out of `Workspace.init()` into reusable helpers
  (`apply_workspace_config`, `_ensure_parsers_registered`) so the runtime
  switch path and the embedded-mode `Workspace` class apply workspace config
  identically.
- `opendb serve` (SQLite mode) now auto-registers the startup workspace into
  the global registry during lifespan setup, so runtime `/workspaces`
  endpoints can see and switch to it from the first request.
- README: workspace management section, updated tool count (7 → 12),
  updated REST endpoint table, new `FILEDB_STATE_DIR` configuration entry.

## [1.4.0] — 2025-10

### Added
- LongMemEval benchmark improvements — **93.6% E2E accuracy**, #3 on the
  leaderboard, beating MemMachine, Vectorize, Emergence AI, Supermemory,
  and Zep.
- Comprehensive benchmarks and improved memory conflict detection.
- GitHub Actions workflows for CI tests and PyPI publishing.

### Changed
- Skip episodic conflict detection during memory store for better
  LongMemEval accuracy.
- Added `skip-existing` flag to the PyPI publish workflow.
- Added return type annotations across the codebase (52% → 100%).

### Fixed
- FastAPI `response_model` for the glob and read endpoints.

### Removed
- Obsolete benchmark experiment results.

## [1.3.0]

### Added
- Custom while-loop agent example in the README.
- `CONTRIBUTING.md` with contributor guidelines.
- Quality improvements: PostgreSQL CJK search, expanded test coverage,
  deduplication, authentication middleware, and more.

### Changed
- Rebranded from museDB to **openDB** with a new logo, banner, and the
  "AI-native database" tagline.
- Rewrote docs: logo brand guide, architecture doc with a Mermaid diagram,
  removed dead tool-definitions link.
- Refactored: removed the legacy `app/` package, narrowed exceptions, split
  the storage layer, added tests.
- License switched from AGPL-3.0 → Apache 2.0 → **MIT**.
- Fixed PyPI package name to `open-db`.

[Unreleased]: https://github.com/wuwangzhang1216/openDB/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/wuwangzhang1216/openDB/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/wuwangzhang1216/openDB/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/wuwangzhang1216/openDB/releases/tag/v1.3.0
