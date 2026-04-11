"""Workspace management service — list, add, remove, and smoothly switch.

The runtime contract:

- At most one workspace is *active* at a time. All routers and services
  implicitly target the active workspace (via ``get_backend()`` and
  ``settings``).
- Multiple workspaces may be *open* simultaneously inside the storage-layer
  registry (``opendb_core.storage._backends``). This makes switching between
  already-opened workspaces a sub-millisecond operation: we only flip the
  backend's ``_active_key`` and patch ``settings``.
- A single module-level ``asyncio.Lock`` serialises all mutations so a
  concurrent request cannot observe a half-swapped state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from opendb_core import workspaces as registry_mod
from opendb_core.workspaces import Registry, WorkspaceEntry
from opendb_core.workspace import (
    WorkspaceConfig,
    apply_workspace_config,
    _ensure_parsers_registered,
)

logger = logging.getLogger(__name__)

_DB_FILE = "metadata.db"
_CONFIG_FILE = "config.json"

_switch_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WorkspaceNotFound(Exception):
    """Raised when the requested workspace id/root is unknown."""


class WorkspaceRootMissing(Exception):
    """Raised when a POST /workspaces root does not exist on disk."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config(opendb_dir: Path) -> WorkspaceConfig:
    config_path = opendb_dir / _CONFIG_FILE
    if not config_path.exists():
        return WorkspaceConfig()
    try:
        return WorkspaceConfig.from_dict(json.loads(config_path.read_text()))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        logger.warning("Could not load workspace config at %s: %s", config_path, e)
        return WorkspaceConfig()


def _entry_to_dict(entry: WorkspaceEntry, active_id: str | None) -> dict:
    d = entry.to_dict()
    d["active"] = entry.id == active_id
    return d


async def _ensure_initialized(root: Path) -> Path:
    """Create the ``.opendb/`` directory layout if missing; return the db path."""
    opendb_dir = root / ".opendb"
    opendb_dir.mkdir(parents=True, exist_ok=True)
    (opendb_dir / "blobs").mkdir(exist_ok=True)
    (opendb_dir / "extracted").mkdir(exist_ok=True)

    config_path = opendb_dir / _CONFIG_FILE
    if not config_path.exists():
        config_path.write_text(
            json.dumps(WorkspaceConfig().to_dict(), indent=2),
            encoding="utf-8",
        )
    return opendb_dir / _DB_FILE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_workspaces() -> dict:
    """Return the full registry with an ``active_id`` field."""
    reg = registry_mod.load()
    active_id = reg.active_id
    items = [_entry_to_dict(w, active_id) for w in reg.workspaces]
    # Sort by last_used_at desc (ISO-8601 is lexicographically sortable),
    # then stable-sort so the active workspace is first.
    items.sort(key=lambda w: w["last_used_at"] or "", reverse=True)
    items.sort(key=lambda w: 0 if w["active"] else 1)
    return {"active_id": active_id, "workspaces": items}


async def current_workspace() -> dict | None:
    """Return the currently active workspace entry as a dict, or None."""
    reg = registry_mod.load()
    active = reg.active()
    if active is None:
        return None
    return _entry_to_dict(active, reg.active_id)


async def add_workspace(
    root: str | Path,
    name: str | None = None,
    switch: bool = False,
) -> dict:
    """Register a workspace. Creates ``.opendb/`` on disk if missing.

    Does *not* switch the active workspace unless ``switch=True``.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists():
        raise WorkspaceRootMissing(f"Workspace root does not exist: {root_path}")
    if not root_path.is_dir():
        raise WorkspaceRootMissing(f"Workspace root is not a directory: {root_path}")

    async with _switch_lock:
        await _ensure_initialized(root_path)

        reg = registry_mod.load()
        entry = reg.upsert(root_path, name=name)
        if reg.active_id is None:
            reg.active_id = entry.id
        registry_mod.save(reg)

        result = _entry_to_dict(entry, reg.active_id)

    if switch:
        return await switch_workspace(entry.id)
    return result


async def remove_workspace(id_or_root: str, force: bool = False) -> dict:
    """Unregister a workspace. Does *not* delete files on disk.

    Refuses to remove the currently-active workspace unless ``force=True``.
    If ``force=True`` and the target is active, the backend is closed and
    the next-most-recent workspace becomes active (if any).
    """
    async with _switch_lock:
        reg = registry_mod.load()
        entry = reg.get(id_or_root)
        if entry is None:
            raise WorkspaceNotFound(f"Unknown workspace: {id_or_root}")

        was_active = reg.active_id == entry.id
        if was_active and not force:
            raise ValueError(
                "Cannot remove the active workspace without force=true. "
                "Switch to another workspace first."
            )

        removed = reg.remove(entry.id)
        registry_mod.save(reg)

        # If we removed the active workspace under force, also close its backend.
        if was_active:
            from opendb_core.storage import close_backend
            db_path = Path(entry.root) / ".opendb" / _DB_FILE
            try:
                await close_backend(key=str(db_path))
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("Failed to close backend for removed workspace: %s", e)

            # If another workspace is now active, activate it properly.
            if reg.active_id is not None:
                # Release the lock before recursing — use inner switch.
                pass  # fall through; caller can switch explicitly.

    result = removed.to_dict() if removed is not None else {}
    result["removed"] = True
    return result


async def switch_workspace(id_or_root: str) -> dict:
    """Switch the active workspace. O(1) for already-opened workspaces.

    Steps (under ``_switch_lock``):
      1. Resolve entry from the registry.
      2. Ensure ``.opendb/`` exists and load the per-workspace config.
      3. ``init_backend("sqlite", db_path=...)`` — no-op if already registered,
         otherwise opens the SQLite file.
      4. Patch ``settings`` via ``apply_workspace_config``.
      5. Update the registry's ``active_id`` and persist.
    """
    from opendb_core.storage import init_backend

    async with _switch_lock:
        reg = registry_mod.load()
        entry = reg.get(id_or_root)
        if entry is None:
            # Allow switching to a path that's not yet registered — auto-add.
            if any(sep in id_or_root for sep in ("/", "\\", ":")) and Path(id_or_root).exists():
                entry = reg.upsert(id_or_root)
            else:
                raise WorkspaceNotFound(f"Unknown workspace: {id_or_root}")

        root_path = Path(entry.root)
        opendb_dir = root_path / ".opendb"
        db_path = await _ensure_initialized(root_path)

        cfg = _load_config(opendb_dir)

        # 1. Open (or reuse) the backend for this workspace.
        await init_backend("sqlite", db_path=db_path)

        # 2. Patch global settings to match this workspace.
        apply_workspace_config(opendb_dir, cfg)

        # 3. Register parsers (idempotent).
        _ensure_parsers_registered()

        # 4. Persist active pointer.
        reg.set_active(entry.id)
        registry_mod.save(reg)

        logger.info("Switched active workspace to %s (%s)", entry.id, entry.root)
        return _entry_to_dict(entry, entry.id)
