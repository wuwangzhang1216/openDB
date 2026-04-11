"""Global workspace registry.

Persists a JSON list of known workspaces at ``~/.opendb/workspaces.json``
(overridable via the ``FILEDB_STATE_DIR`` environment variable).

This module is *pure data* — it does not open any SQLite backends.
It is consumed by ``opendb_core.services.workspace_service`` which layers
the actual backend switching on top.

Entry schema::

    {
      "id": "a3f2b1c8",          # first 8 chars of sha1(absolute root)
      "name": "openDB",          # defaults to root.name
      "root": "D:/work/openDB",  # absolute, forward-slashes
      "backend": "sqlite",
      "created_at": "2026-04-10T12:00:00Z",
      "last_used_at": "2026-04-10T14:22:13Z"
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_REGISTRY_FILENAME = "workspaces.json"
_SCHEMA_VERSION = 1


def state_dir() -> Path:
    """Return the global state directory (holds the workspace registry)."""
    override = os.environ.get("FILEDB_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".opendb").resolve()


def _registry_path() -> Path:
    return state_dir() / _REGISTRY_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_root(root: str | Path) -> Path:
    return Path(root).expanduser().resolve()


def workspace_id(root: str | Path) -> str:
    """Deterministic short id for a workspace root."""
    norm = str(_normalize_root(root)).replace("\\", "/")
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]


@dataclass
class WorkspaceEntry:
    id: str
    name: str
    root: str
    backend: str = "sqlite"
    created_at: str = field(default_factory=_now_iso)
    last_used_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkspaceEntry":
        return cls(
            id=data["id"],
            name=data.get("name") or data["id"],
            root=data["root"],
            backend=data.get("backend", "sqlite"),
            created_at=data.get("created_at", _now_iso()),
            last_used_at=data.get("last_used_at", _now_iso()),
        )


@dataclass
class Registry:
    version: int = _SCHEMA_VERSION
    active_id: str | None = None
    workspaces: list[WorkspaceEntry] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def by_id(self, wid: str) -> WorkspaceEntry | None:
        for w in self.workspaces:
            if w.id == wid:
                return w
        return None

    def by_root(self, root: str | Path) -> WorkspaceEntry | None:
        target = str(_normalize_root(root)).replace("\\", "/")
        for w in self.workspaces:
            if w.root == target:
                return w
        return None

    def get(self, id_or_root: str) -> WorkspaceEntry | None:
        """Resolve by id first, then by root path."""
        hit = self.by_id(id_or_root)
        if hit is not None:
            return hit
        # Try as a path only if it looks like one
        if any(sep in id_or_root for sep in ("/", "\\", ":")) or Path(id_or_root).exists():
            return self.by_root(id_or_root)
        return None

    def active(self) -> WorkspaceEntry | None:
        if self.active_id is None:
            return None
        return self.by_id(self.active_id)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def upsert(self, root: str | Path, name: str | None = None) -> WorkspaceEntry:
        norm = _normalize_root(root)
        root_str = str(norm).replace("\\", "/")
        wid = workspace_id(norm)
        existing = self.by_id(wid)
        if existing is not None:
            if name:
                existing.name = name
            existing.last_used_at = _now_iso()
            return existing
        entry = WorkspaceEntry(
            id=wid,
            name=name or norm.name or wid,
            root=root_str,
        )
        self.workspaces.append(entry)
        return entry

    def remove(self, id_or_root: str) -> WorkspaceEntry | None:
        target = self.get(id_or_root)
        if target is None:
            return None
        self.workspaces = [w for w in self.workspaces if w.id != target.id]
        if self.active_id == target.id:
            self.active_id = self.workspaces[0].id if self.workspaces else None
        return target

    def set_active(self, wid: str) -> WorkspaceEntry:
        entry = self.by_id(wid)
        if entry is None:
            raise KeyError(f"Unknown workspace id: {wid}")
        entry.last_used_at = _now_iso()
        self.active_id = wid
        return entry

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "active_id": self.active_id,
            "workspaces": [w.to_dict() for w in self.workspaces],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Registry":
        return cls(
            version=data.get("version", _SCHEMA_VERSION),
            active_id=data.get("active_id"),
            workspaces=[WorkspaceEntry.from_dict(w) for w in data.get("workspaces", [])],
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load() -> Registry:
    """Load the registry from disk. Returns an empty Registry on any error."""
    path = _registry_path()
    if not path.exists():
        return Registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Registry.from_dict(data)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, KeyError) as e:
        logger.warning("Could not load workspace registry at %s: %s", path, e)
        return Registry()


def save(reg: Registry) -> None:
    """Atomically persist the registry to disk."""
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(reg.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)
