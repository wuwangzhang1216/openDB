"""SQLite + FTS5 storage backend — zero-dependency embedded mode.

Uses aiosqlite for async access. Requires ``pip install musedb[embedded]``.

Schema notes:
- UUIDs stored as TEXT
- tags stored as JSON text (e.g. '["tag1","tag2"]')
- metadata stored as JSON text
- line_index stored as JSON text (list of int byte offsets)
- pages_fts is a standalone FTS5 virtual table (jieba-tokenized text)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS files (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    mime_type   TEXT NOT NULL,
    file_size   INTEGER NOT NULL,
    file_path   TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'processing',
    error_message TEXT,
    tags        TEXT NOT NULL DEFAULT '[]',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_files_checksum_ready
    ON files(checksum) WHERE status = 'ready';
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_filename ON files(filename);

CREATE TABLE IF NOT EXISTS file_text (
    file_id     TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    full_text   TEXT NOT NULL,
    total_lines INTEGER NOT NULL,
    line_index  TEXT NOT NULL DEFAULT '[]',
    toc         TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS pages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page_number   INTEGER NOT NULL,
    section_title TEXT,
    content_type  TEXT NOT NULL DEFAULT 'text',
    text          TEXT NOT NULL,
    line_start    INTEGER NOT NULL,
    line_end      INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_pages_file ON pages(file_id, page_number);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(text);

CREATE TRIGGER IF NOT EXISTS files_updated AFTER UPDATE ON files
    WHEN old.updated_at = new.updated_at
BEGIN
    UPDATE files SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = new.id;
END;

-- -----------------------------------------------------------------
-- Agent Memory
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id     TEXT NOT NULL UNIQUE,
    content       TEXT NOT NULL,
    memory_type   TEXT NOT NULL DEFAULT 'semantic',
    tags          TEXT NOT NULL DEFAULT '[]',
    metadata      TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content);

CREATE TRIGGER IF NOT EXISTS memories_updated AFTER UPDATE ON memories
    WHEN old.updated_at = new.updated_at
BEGIN
    UPDATE memories SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = new.id;
END;
"""


class SQLiteBackend:
    """SQLite + FTS5 implementation of StorageBackend.

    Usage::

        backend = SQLiteBackend(db_path=".musedb/metadata.db")
        await backend.init()
        ...
        await backend.close()
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db = None  # aiosqlite.Connection
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        try:
            import aiosqlite
        except ImportError:
            raise RuntimeError(
                "aiosqlite is required for embedded mode. "
                "Install it with: pip install musedb[embedded]"
            )

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._migrate_fts_if_needed()
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info("SQLite backend initialised at %s", self._db_path)

    async def _migrate_fts_if_needed(self) -> None:
        """Migrate old content-table FTS5 to standalone + jieba tokenization."""
        try:
            async with self._db.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'pages_fts'"
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return  # Fresh DB, no migration needed
            create_sql = row[0] or ""
            if "content=" not in create_sql:
                return  # Already standalone
            logger.info("Migrating pages_fts to standalone FTS5 with jieba tokenization...")
            await self._db.execute("DROP TRIGGER IF EXISTS pages_ai")
            await self._db.execute("DROP TRIGGER IF EXISTS pages_ad")
            await self._db.execute("DROP TRIGGER IF EXISTS pages_au")
            await self._db.execute("DROP TABLE IF EXISTS pages_fts")
            await self._db.execute(
                "CREATE VIRTUAL TABLE pages_fts USING fts5(text)"
            )
            from musedb_core.utils.tokenizer import tokenize_for_fts
            async with self._db.execute(
                "SELECT id, text FROM pages ORDER BY id"
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                fts_rows = [(r["id"], tokenize_for_fts(r["text"])) for r in rows]
                await self._db.executemany(
                    "INSERT INTO pages_fts(rowid, text) VALUES (?, ?)",
                    fts_rows,
                )
            await self._db.commit()
            logger.info("FTS migration complete — %d pages re-indexed.", len(rows) if rows else 0)
        except Exception:
            pass

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def check_duplicate(self, checksum: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, filename FROM files WHERE checksum = ? AND status = 'ready'",
            (checksum,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return {
                "id": row["id"],
                "filename": row["filename"],
                "status": "duplicate",
                "detail": "File with identical content already exists",
            }
        return None

    async def persist_ingestion(
        self,
        *,
        file_id: str,
        file_path: str,
        original_filename: str,
        mime_type: str,
        file_size: int,
        checksum: str,
        tags: list[str],
        merged_metadata: dict,
        parse_result,
        full_text: str,
        total_lines: int,
        line_index: list[int],
        toc: str,
        page_line_ranges: list[tuple[int, int]],
    ) -> dict:
        try:
            await self._db.execute("BEGIN")
            await self._db.execute(
                """
                INSERT INTO files
                    (id, filename, mime_type, file_size, file_path,
                     checksum, status, tags, metadata)
                VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?)
                """,
                (
                    file_id,
                    original_filename,
                    mime_type,
                    file_size,
                    file_path,
                    checksum,
                    json.dumps(tags),
                    json.dumps(merged_metadata),
                ),
            )

            from musedb_core.utils.tokenizer import tokenize_for_fts

            for i, page in enumerate(parse_result.pages):
                line_start, line_end = page_line_ranges[i]
                await self._db.execute(
                    """
                    INSERT INTO pages
                        (file_id, page_number, section_title,
                         content_type, text, line_start, line_end)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        page.page_number,
                        page.section_title,
                        page.content_type,
                        page.text,
                        line_start,
                        line_end,
                    ),
                )

            # Insert tokenized text into standalone FTS5 index
            if parse_result.pages:
                async with self._db.execute(
                    "SELECT id, text FROM pages WHERE file_id = ? ORDER BY page_number",
                    (file_id,),
                ) as cur:
                    page_id_rows = await cur.fetchall()
                for r in page_id_rows:
                    await self._db.execute(
                        "INSERT INTO pages_fts(rowid, text) VALUES (?, ?)",
                        (r["id"], tokenize_for_fts(r["text"])),
                    )

            await self._db.execute(
                """
                INSERT INTO file_text
                    (file_id, full_text, total_lines, line_index, toc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    full_text,
                    total_lines,
                    json.dumps(line_index),
                    toc,
                ),
            )

            await self._db.execute(
                "UPDATE files SET status = 'ready', metadata = ? WHERE id = ?",
                (json.dumps(merged_metadata), file_id),
            )
            await self._db.commit()

        except Exception as exc:
            await self._db.rollback()
            # Detect unique-constraint violation (duplicate checksum)
            if "UNIQUE constraint failed" in str(exc):
                dup = await self.check_duplicate(checksum)
                if dup:
                    return dup
            raise

        return {
            "id": file_id,
            "filename": original_filename,
            "mime_type": mime_type,
            "file_size": file_size,
            "status": "ready",
            "total_pages": len(parse_result.pages),
            "total_lines": total_lines,
            "metadata": merged_metadata,
        }

    async def mark_file_failed(self, file_id: str, error: str) -> None:
        await self._db.execute(
            "UPDATE files SET status = 'failed', error_message = ? WHERE id = ?",
            (error, file_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_file_text(self, file_id: str) -> dict:
        async with self._db.execute(
            "SELECT full_text, total_lines, line_index, toc "
            "FROM file_text WHERE file_id = ?",
            (file_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            from app.services.read_service import FileNotFoundError
            raise FileNotFoundError(f"No text found for file {file_id}")
        return {
            "full_text": row["full_text"],
            "total_lines": row["total_lines"],
            "line_index": json.loads(row["line_index"]),
            "toc": row["toc"],
        }

    async def get_total_pages(self, file_id: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM pages WHERE file_id = ?", (file_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def get_page_line_ranges(
        self, file_id: str, page_numbers: list[int]
    ) -> list[tuple[int, int]]:
        placeholders = ",".join("?" * len(page_numbers))
        async with self._db.execute(
            f"SELECT line_start, line_end FROM pages "
            f"WHERE file_id = ? AND page_number IN ({placeholders}) "
            f"ORDER BY page_number",
            (file_id, *page_numbers),
        ) as cur:
            rows = await cur.fetchall()
        return [(r["line_start"], r["line_end"]) for r in rows]

    async def get_page_by_section_title(
        self, file_id: str, title: str
    ) -> list[int]:
        async with self._db.execute(
            "SELECT page_number FROM pages "
            "WHERE file_id = ? AND section_title LIKE ? "
            "ORDER BY page_number",
            (file_id, f"%{title}%"),
        ) as cur:
            rows = await cur.fetchall()
        return [r["page_number"] for r in rows]

    async def get_file_info(self, file_id: str) -> dict:
        async with self._db.execute(
            "SELECT file_path, mime_type, filename FROM files WHERE id = ?",
            (file_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            from app.services.read_service import FileNotFoundError
            raise FileNotFoundError(f"File {file_id} not found")
        return {"file_path": row["file_path"], "mime_type": row["mime_type"], "filename": row["filename"]}

    async def get_sheet_names_for_pages(
        self, file_id: str, page_nums: list[int]
    ) -> list[str]:
        placeholders = ",".join("?" * len(page_nums))
        async with self._db.execute(
            f"SELECT DISTINCT section_title FROM pages "
            f"WHERE file_id = ? AND page_number IN ({placeholders}) "
            f"AND section_title IS NOT NULL",
            (file_id, *page_nums),
        ) as cur:
            rows = await cur.fetchall()
        names: set[str] = set()
        for r in rows:
            title = r["section_title"]
            base = title.split(" - rows ")[0] if " - rows " in title else title
            names.add(base)
        return list(names)

    # ------------------------------------------------------------------
    # Filename resolution
    # ------------------------------------------------------------------

    async def find_file_exact(self, filename: str) -> str | None:
        async with self._db.execute(
            "SELECT id FROM files WHERE filename = ? AND status = 'ready'",
            (filename,),
        ) as cur:
            row = await cur.fetchone()
        return row["id"] if row else None

    async def find_file_by_uuid(self, file_id_str: str) -> str | None:
        import uuid as _uuid
        try:
            _uuid.UUID(file_id_str)  # validate format
        except ValueError:
            return None
        async with self._db.execute(
            "SELECT id FROM files WHERE id = ? AND status = 'ready'",
            (file_id_str,),
        ) as cur:
            row = await cur.fetchone()
        return row["id"] if row else None

    async def find_files_fuzzy(self, filename: str) -> list[dict]:
        """Python-side fuzzy matching (no pg_trgm available in SQLite)."""
        from difflib import SequenceMatcher

        async with self._db.execute(
            "SELECT id, filename FROM files WHERE status = 'ready'"
        ) as cur:
            rows = await cur.fetchall()

        scored: list[dict] = []
        fn_lower = filename.lower()
        for r in rows:
            sim = SequenceMatcher(None, fn_lower, r["filename"].lower()).ratio()
            if sim > 0.3:
                scored.append({"id": r["id"], "filename": r["filename"], "sim": sim})

        scored.sort(key=lambda x: x["sim"], reverse=True)
        return scored[:5]

    async def find_files_ilike(self, pattern: str) -> list[dict]:
        async with self._db.execute(
            "SELECT id, filename FROM files "
            "WHERE filename LIKE ? AND status = 'ready'",
            (f"%{pattern}%",),
        ) as cur:
            rows = await cur.fetchall()
        return [{"id": r["id"], "filename": r["filename"]} for r in rows]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_fts(
        self, query: str, filters: dict, limit: int, offset: int
    ) -> dict:
        from musedb_core.utils.tokenizer import tokenize_for_fts

        # Tokenize query (handles CJK via jieba) then escape for FTS5
        fts_query = _escape_fts5(tokenize_for_fts(query))

        conditions = ["f.status = 'ready'"]
        params: list = []
        _add_sqlite_filters(conditions, params, filters)
        filter_clause = (" AND " + " AND ".join(conditions[1:])) if len(conditions) > 1 else ""

        search_sql = f"""
            SELECT f.filename, f.id AS file_id, p.page_number, p.section_title,
                   p.text, pages_fts.rank, f.updated_at
            FROM pages_fts
            JOIN pages p ON pages_fts.rowid = p.id
            JOIN files f ON p.file_id = f.id
            WHERE pages_fts MATCH ? AND f.status = 'ready'{filter_clause}
            ORDER BY pages_fts.rank
            LIMIT ? OFFSET ?
        """
        count_sql = f"""
            SELECT COUNT(*)
            FROM pages_fts
            JOIN pages p ON pages_fts.rowid = p.id
            JOIN files f ON p.file_id = f.id
            WHERE pages_fts MATCH ? AND f.status = 'ready'{filter_clause}
        """

        search_params = [fts_query, *params, limit, offset]
        count_params = [fts_query, *params]

        async with self._db.execute(search_sql, search_params) as cur:
            rows = await cur.fetchall()
        async with self._db.execute(count_sql, count_params) as cur:
            total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

        results = []
        for r in rows:
            results.append(
                {
                    "filename": r["filename"],
                    "file_id": r["file_id"],
                    "page_number": r["page_number"],
                    "section_title": r["section_title"],
                    "highlight": _build_highlight(r["text"], query),
                    "relevance_score": round(abs(float(r["rank"])), 3),
                    "updated_at": r["updated_at"],
                }
            )
        return {"total": total, "results": results}

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def batch_check_duplicates(self, checksums: list[str]) -> set[str]:
        if not checksums:
            return set()
        placeholders = ",".join("?" * len(checksums))
        async with self._db.execute(
            f"SELECT DISTINCT checksum FROM files "
            f"WHERE checksum IN ({placeholders}) AND status = 'ready'",
            checksums,
        ) as cur:
            rows = await cur.fetchall()
        return {r["checksum"] for r in rows}

    # ------------------------------------------------------------------
    # Files CRUD
    # ------------------------------------------------------------------

    async def list_files(
        self,
        filters: dict,
        sort_field: str,
        sort_dir: str,
        limit: int,
        offset: int,
    ) -> dict:
        allowed_sorts = {"created_at", "filename", "file_size"}
        sf = sort_field if sort_field in allowed_sorts else "created_at"
        sd = "ASC" if sort_dir.upper() == "ASC" else "DESC"

        conditions = ["f.status = 'ready'"]
        params: list = []

        if filters.get("tags"):
            # JSON contains — use LIKE for simplicity
            tag = filters["tags"] if isinstance(filters["tags"], str) else filters["tags"][0]
            params.append(f"%{tag}%")
            conditions.append("f.tags LIKE ?")

        if filters.get("mime_type"):
            params.append(filters["mime_type"])
            conditions.append("f.mime_type = ?")

        if filters.get("filename"):
            params.append(f"%{filters['filename']}%")
            conditions.append("f.filename LIKE ?")

        where_clause = " AND ".join(conditions)
        n = len(params)

        query = f"""
            SELECT f.id, f.filename, f.mime_type, f.file_size,
                   f.tags, f.metadata, f.created_at, f.updated_at, f.status,
                   ft.total_lines,
                   (SELECT COUNT(*) FROM pages p WHERE p.file_id = f.id) AS total_pages
            FROM files f
            LEFT JOIN file_text ft ON ft.file_id = f.id
            WHERE {where_clause}
            ORDER BY f.{sf} {sd}
            LIMIT ? OFFSET ?
        """
        count_query = f"SELECT COUNT(*) FROM files f WHERE {where_clause}"

        async with self._db.execute(query, [*params, limit, offset]) as cur:
            rows = await cur.fetchall()
        async with self._db.execute(count_query, params) as cur:
            total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

        return {"total": total, "files": [_sqlite_file_row(r) for r in rows]}

    async def get_file_by_id(self, file_id: str) -> dict | None:
        async with self._db.execute(
            """
            SELECT f.id, f.filename, f.mime_type, f.file_size,
                   f.tags, f.metadata, f.created_at, f.updated_at, f.status,
                   ft.total_lines,
                   (SELECT COUNT(*) FROM pages p WHERE p.file_id = f.id) AS total_pages
            FROM files f
            LEFT JOIN file_text ft ON ft.file_id = f.id
            WHERE f.id = ?
            """,
            (file_id,),
        ) as cur:
            row = await cur.fetchone()
        return _sqlite_file_row(row) if row else None

    async def delete_file(self, file_id: str) -> str | None:
        async with self._db.execute(
            "SELECT file_path FROM files WHERE id = ?", (file_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        file_path = row["file_path"]
        # Clean up standalone FTS5 index before cascade deletes pages
        await self._db.execute(
            "DELETE FROM pages_fts WHERE rowid IN "
            "(SELECT id FROM pages WHERE file_id = ?)",
            (file_id,),
        )
        await self._db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await self._db.commit()
        return file_path

    # ------------------------------------------------------------------
    # Agent Memory
    # ------------------------------------------------------------------

    async def store_memory(
        self,
        *,
        memory_id: str,
        content: str,
        memory_type: str,
        tags: list[str],
        metadata: dict,
    ) -> dict:
        from musedb_core.utils.tokenizer import tokenize_for_fts

        async with self._write_lock:
            await self._db.execute("BEGIN")
            try:
                await self._db.execute(
                    """
                    INSERT INTO memories (memory_id, content, memory_type, tags, metadata)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (memory_id, content, memory_type, json.dumps(tags), json.dumps(metadata)),
                )
                async with self._db.execute(
                    "SELECT id FROM memories WHERE memory_id = ?", (memory_id,)
                ) as cur:
                    row = await cur.fetchone()
                rowid = row["id"]
                await self._db.execute(
                    "INSERT INTO memories_fts(rowid, content) VALUES (?, ?)",
                    (rowid, tokenize_for_fts(content)),
                )
                await self._db.execute("COMMIT")
            except Exception:
                await self._db.execute("ROLLBACK")
                raise

        return await self.get_memory(memory_id)  # type: ignore[return-value]

    async def recall_memories(
        self,
        query: str,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
    ) -> dict:
        from musedb_core.utils.tokenizer import tokenize_for_fts

        fts_query = _escape_fts5(tokenize_for_fts(query))

        conditions: list[str] = []
        params: list = []
        if memory_type:
            conditions.append("m.memory_type = ?")
            params.append(memory_type)
        if tags:
            for tag in tags:
                conditions.append("m.tags LIKE ?")
                params.append(f'%"{tag}"%')

        filter_clause = (" AND " + " AND ".join(conditions)) if conditions else ""
        fetch_limit = max(limit * 3, 60)

        search_sql = f"""
            SELECT m.memory_id, m.content, m.memory_type, m.tags, m.metadata,
                   m.created_at, m.updated_at,
                   memories_fts.rank AS fts_rank,
                   julianday('now') - julianday(m.created_at) AS age_days
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.id
            WHERE memories_fts MATCH ?{filter_clause}
            ORDER BY memories_fts.rank
            LIMIT ?
        """
        count_sql = f"""
            SELECT COUNT(*)
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.id
            WHERE memories_fts MATCH ?{filter_clause}
        """

        search_params = [fts_query, *params, fetch_limit]
        count_params = [fts_query, *params]

        async with self._db.execute(search_sql, search_params) as cur:
            rows = await cur.fetchall()
        async with self._db.execute(count_sql, count_params) as cur:
            total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

        scored = []
        for r in rows:
            fts_score = abs(float(r["fts_rank"]))
            age_days = float(r["age_days"]) if r["age_days"] else 0.0
            decay = 0.5 ** (age_days / 30.0)
            score = round(fts_score * decay, 4)
            scored.append({
                "memory_id": r["memory_id"],
                "content": r["content"],
                "memory_type": r["memory_type"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "highlight": _build_highlight(r["content"], query),
                "score": score,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        results = scored[offset : offset + limit]
        return {"total": total, "results": results}

    async def get_memory(self, memory_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT memory_id, content, memory_type, tags, metadata, "
            "created_at, updated_at FROM memories WHERE memory_id = ?",
            (memory_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return {
            "memory_id": row["memory_id"],
            "content": row["content"],
            "memory_type": row["memory_type"],
            "tags": json.loads(row["tags"]) if row["tags"] else [],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def delete_memory(self, memory_id: str) -> bool:
        async with self._write_lock:
            async with self._db.execute(
                "SELECT id FROM memories WHERE memory_id = ?", (memory_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return False
            rowid = row["id"]
            await self._db.execute(
                "DELETE FROM memories_fts WHERE rowid = ?", (rowid,)
            )
            await self._db.execute(
                "DELETE FROM memories WHERE id = ?", (rowid,)
            )
            await self._db.commit()
            return True

    async def list_memories(
        self,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
    ) -> dict:
        conditions: list[str] = []
        params: list = []
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)
        if tags:
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        query = f"""
            SELECT memory_id, content, memory_type, tags, metadata,
                   created_at, updated_at
            FROM memories
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        count_query = f"SELECT COUNT(*) FROM memories {where_clause}"

        async with self._db.execute(query, [*params, limit, offset]) as cur:
            rows = await cur.fetchall()
        async with self._db.execute(count_query, params) as cur:
            total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

        memories = []
        for r in rows:
            memories.append({
                "memory_id": r["memory_id"],
                "content": r["content"],
                "memory_type": r["memory_type"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return {"total": total, "memories": memories}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_fts5(query: str) -> str:
    """Minimal FTS5 query escaping — wrap each term to avoid syntax errors."""
    terms = [t.strip() for t in query.split() if t.strip()]
    escaped = [f'"{t}"' for t in terms]
    return " ".join(escaped)


def _build_highlight(text: str, query: str, context_chars: int = 80) -> str:
    """Build a highlight snippet from original text by finding query terms."""
    terms = [t.strip().lower() for t in query.split() if t.strip()]
    if not terms:
        return text[:150]
    text_lower = text.lower()
    best_pos = -1
    for term in terms:
        pos = text_lower.find(term)
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos = pos
    if best_pos < 0:
        return text[:150]
    start = max(0, best_pos - context_chars)
    end = min(len(text), best_pos + context_chars)
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def _add_sqlite_filters(conditions: list[str], params: list, filters: dict) -> None:
    if filters.get("tags"):
        tag = filters["tags"] if isinstance(filters["tags"], str) else filters["tags"][0]
        params.append(f"%{tag}%")
        conditions.append("f.tags LIKE ?")

    if filters.get("mime_type"):
        params.append(filters["mime_type"])
        conditions.append("f.mime_type = ?")


def _sqlite_file_row(row) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "file_size": row["file_size"],
        "total_pages": row["total_pages"],
        "total_lines": row["total_lines"],
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
