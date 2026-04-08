"""Workspace model — represents a local OpenDB embedded workspace.

A workspace is a root directory with a ``.opendb/`` subdirectory that holds
all metadata, indexes, and configuration for embedded mode.

Layout::

    <root>/
      .opendb/
        config.json    # workspace settings (backend type, OCR config, etc.)
        metadata.db    # SQLite database (embedded mode)
        blobs/         # cached copies of indexed files
        extracted/     # parser-output cache (future use)

"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_FILE = "config.json"
_DB_FILE = "metadata.db"


@dataclass
class WorkspaceConfig:
    backend: str = "sqlite"
    ocr_enabled: bool = True
    ocr_languages: str = "eng+chi_sim+chi_tra"
    max_file_size_mb: int = 100
    index_exclude_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "ocr_enabled": self.ocr_enabled,
            "ocr_languages": self.ocr_languages,
            "max_file_size_mb": self.max_file_size_mb,
            "index_exclude_patterns": self.index_exclude_patterns,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkspaceConfig":
        return cls(
            backend=data.get("backend", "sqlite"),
            ocr_enabled=data.get("ocr_enabled", True),
            ocr_languages=data.get("ocr_languages", "eng+chi_sim+chi_tra"),
            max_file_size_mb=data.get("max_file_size_mb", 100),
            index_exclude_patterns=data.get("index_exclude_patterns", []),
        )


class Workspace:
    """A local embedded OpenDB workspace backed by SQLite.

    Usage::

        ws = Workspace.open("./my_project")
        await ws.init()
        await ws.index("./my_project")
        results = await ws.search("quarterly revenue")
        await ws.close()
    """

    def __init__(self, root: Path, config: WorkspaceConfig | None = None) -> None:
        self.root = root.resolve()
        self.opendb_dir = self.root / ".opendb"
        self.config = config or WorkspaceConfig()
        self._backend = None

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> "Workspace":
        """Open (or create) a workspace at *path*.

        Loads existing config from ``.opendb/config.json`` if present.
        Does NOT call ``init()`` — call that separately (it is async).
        """
        root = Path(path).resolve()
        config_path = root / ".opendb" / _CONFIG_FILE
        config = WorkspaceConfig()
        if config_path.exists():
            try:
                config = WorkspaceConfig.from_dict(json.loads(config_path.read_text()))
            except Exception as e:
                logger.warning("Could not load workspace config at %s: %s", config_path, e)
        return cls(root=root, config=config)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Create the ``.opendb/`` directory layout and open the SQLite backend."""
        self.opendb_dir.mkdir(parents=True, exist_ok=True)
        (self.opendb_dir / "blobs").mkdir(exist_ok=True)
        (self.opendb_dir / "extracted").mkdir(exist_ok=True)

        # Write config
        config_path = self.opendb_dir / _CONFIG_FILE
        config_path.write_text(json.dumps(self.config.to_dict(), indent=2))

        # Initialise and register the SQLite backend globally so all services use it
        from app.storage import init_backend
        from app.config import settings

        db_path = self.opendb_dir / _DB_FILE
        await init_backend("sqlite", db_path=db_path)

        # Patch settings to match workspace config
        settings.file_storage_path = self.opendb_dir / "blobs"
        settings.ocr_enabled = self.config.ocr_enabled
        settings.ocr_languages = self.config.ocr_languages
        settings.max_file_size = self.config.max_file_size_mb * 1024 * 1024
        settings.index_exclude_patterns = self.config.index_exclude_patterns

        settings.file_storage_path.mkdir(parents=True, exist_ok=True)

        # Register all parsers (the REST API does this via router imports; we must do it explicitly)
        import app.parsers.text      # noqa: F401
        import app.parsers.pdf       # noqa: F401
        import app.parsers.docx      # noqa: F401
        import app.parsers.pptx      # noqa: F401
        import app.parsers.spreadsheet  # noqa: F401
        import app.parsers.image     # noqa: F401

        logger.info("Workspace initialised at %s", self.root)

    async def close(self) -> None:
        """Close the storage backend."""
        from app.storage import close_backend
        await close_backend()

    # ------------------------------------------------------------------
    # High-level API (mirrors OpenDBClient interface)
    # ------------------------------------------------------------------

    async def index(self, path: str | Path | None = None) -> dict:
        """Index a directory (defaults to workspace root)."""
        from app.services.index_service import index_directory
        target = Path(path).resolve() if path else self.root
        return await index_directory(dir_path=target, tags=[], metadata={})

    async def read(
        self,
        filename: str,
        numbered: bool = False,
        pages: str | None = None,
        lines: str | None = None,
        grep: str | None = None,
        format: str | None = None,
    ) -> str:
        """Read a file by filename."""
        import json as _json
        from app.services.read_service import (
            resolve_filename,
            read_file_text,
            read_structured_spreadsheet,
        )
        from app.utils.text import format_with_line_numbers

        file_id = await resolve_filename(filename)

        if format == "json":
            data = await read_structured_spreadsheet(file_id, pages=pages)
            return _json.dumps(data, indent=2, ensure_ascii=False)

        text, info = await read_file_text(file_id, pages=pages, lines=lines, grep=grep)

        if numbered and not grep:
            start_line = 1
            if lines:
                parts = lines.strip().split("-")
                start_line = int(parts[0])
            text = format_with_line_numbers(text, start=start_line)

        return text

    async def search(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Full-text search across indexed files."""
        from app.services.search_service import search_files
        return await search_files(query=query, limit=limit, offset=offset)

    async def glob(self, pattern: str, path: str | Path | None = None) -> dict:
        """Find files matching a glob pattern."""
        import os
        root = Path(path).resolve() if path else self.root
        matches = []
        for p in root.glob(pattern):
            if p.is_file():
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    mtime = 0.0
                matches.append((p, mtime))
        matches.sort(key=lambda x: x[1], reverse=True)
        truncated = len(matches) > 500
        matches = matches[:500]
        files = []
        for p, _ in matches:
            try:
                files.append(str(p.relative_to(root)).replace(os.sep, "/"))
            except ValueError:
                files.append(str(p).replace(os.sep, "/"))
        return {"count": len(files), "truncated": truncated, "files": files}
