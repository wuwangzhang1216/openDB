"""MuseDB client — direct Python library access (no HTTP).

Manages an asyncpg connection pool and exposes MuseDB's service layer
as clean async methods. No HTTP server required.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MuseDBClient:
    """Direct Python client for MuseDB.

    Manages its own asyncpg pool and calls MuseDB service functions directly.
    No HTTP, no port, no separate process.

    Usage:
        db = MuseDBClient("postgresql://musedb:musedb@localhost:5432/musedb")
        await db.init()
        text = await db.read("report.pdf", pages="1-3")
        await db.close()
    """

    def __init__(
        self,
        database_url: str = "postgresql://musedb:musedb@localhost:5432/musedb",
        file_storage_path: str = "./data",
        pool_min: int = 2,
        pool_max: int = 10,
    ):
        self._database_url = database_url
        self._file_storage_path = Path(file_storage_path)
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._initialized = False
        self._available: bool | None = None

    async def init(self) -> None:
        """Initialize database pool and ensure schema exists."""
        if self._initialized:
            return
        try:
            import asyncpg
            from app.database import init_pool, get_pool
            from app.config import settings

            # Override settings for this instance
            settings.database_url = self._database_url
            settings.db_pool_min = self._pool_min
            settings.db_pool_max = self._pool_max
            settings.file_storage_path = self._file_storage_path

            await init_pool()
            self._file_storage_path.mkdir(parents=True, exist_ok=True)
            self._initialized = True
            self._available = True
            logger.info("MuseDB initialized (direct mode) — %s", self._database_url)
        except Exception as e:
            logger.warning("MuseDB init failed: %s", e)
            self._available = False

    async def is_available(self) -> bool:
        """Check if MuseDB is initialized and database is reachable."""
        if not self._initialized:
            try:
                await self.init()
            except Exception:
                return False
        if not self._available:
            return False
        try:
            from app.database import get_pool
            pool = await get_pool()
            await pool.fetchval("SELECT 1")
            return True
        except Exception:
            self._available = False
            return False

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def read_file(
        self,
        filename: str,
        numbered: bool = False,
        pages: str | None = None,
        lines: str | None = None,
        grep: str | None = None,
        format: str | None = None,
    ) -> str | None:
        """Read a file by filename. Returns None if unavailable."""
        if not await self.is_available():
            return None
        try:
            from app.services.read_service import (
                resolve_filename,
                read_file_text,
                read_structured_spreadsheet,
                FileNotFoundError,
            )
            from app.utils.text import format_with_line_numbers

            file_id = await resolve_filename(filename)

            # Structured JSON for spreadsheets
            if format == "json":
                data = await read_structured_spreadsheet(file_id, pages=pages)
                return json.dumps(data, indent=2, ensure_ascii=False)

            text, info = await read_file_text(
                file_id, pages=pages, lines=lines, grep=grep
            )

            # Apply line numbering
            if numbered and not grep:
                start_line = 1
                if lines:
                    parts = lines.strip().split("-")
                    start_line = int(parts[0])
                text = format_with_line_numbers(text, start=start_line)

            return text
        except Exception as e:
            logger.debug("MuseDB read_file failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        mode: str = "fts",
        path: str | None = None,
        glob: str | None = None,
        case_insensitive: bool = False,
        context: int = 0,
        limit: int = 20,
        offset: int = 0,
        max_results: int = 100,
    ) -> dict | None:
        """Search files. Returns None if unavailable."""
        if not await self.is_available():
            return None
        try:
            if mode == "grep" or (mode == "auto" and (path or glob)):
                from app.services.grep_service import grep_files
                return await grep_files(
                    query=query,
                    path=path or ".",
                    glob=glob,
                    case_insensitive=case_insensitive,
                    context=context,
                    max_results=max_results,
                )
            else:
                from app.services.search_service import search_files
                return await search_files(
                    query=query,
                    limit=limit,
                    offset=offset,
                )
        except Exception as e:
            logger.debug("MuseDB search failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Glob
    # ------------------------------------------------------------------

    async def glob_files(self, pattern: str, path: str | None = None) -> dict | None:
        """Find files matching glob pattern. Returns None if unavailable."""
        if path is None:
            return None
        try:
            root = Path(path)
            if not root.is_dir():
                return None

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
        except Exception as e:
            logger.debug("MuseDB glob failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    async def index_directory(
        self, path: str, tags: list[str] | None = None
    ) -> dict | None:
        """Index a directory. Returns None if unavailable."""
        if not await self.is_available():
            return None
        try:
            from app.services.index_service import index_directory
            return await index_directory(
                dir_path=Path(path),
                tags=tags or [],
                metadata={},
                max_concurrent=4,
            )
        except Exception as e:
            logger.debug("MuseDB index_directory failed: %s", e)
            return None

    async def upload_file(self, file_path: str | Path) -> dict | None:
        """Ingest a single file. Returns None if unavailable."""
        if not await self.is_available():
            return None
        fp = Path(file_path)
        if not fp.exists():
            return None
        try:
            from app.services.ingest_service import ingest_local_file
            return await ingest_local_file(
                local_path=fp,
                tags=[],
                metadata={},
            )
        except Exception as e:
            logger.debug("MuseDB upload_file failed: %s", e)
            return None

    async def list_watchers(self) -> list[dict] | None:
        """List active directory watchers."""
        try:
            from app.services.watch_service import list_watches
            return list_watches()
        except Exception as e:
            logger.debug("MuseDB list_watchers failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the database pool."""
        if not self._initialized:
            return
        try:
            from app.database import close_pool
            await close_pool()
        except Exception:
            pass
        self._initialized = False
        self._available = False
