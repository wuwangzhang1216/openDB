"""PostgreSQL storage backend.

Wraps the existing asyncpg pool (managed by app/database.py).
All SQL here is PostgreSQL-dialect; the service layer calls these methods
instead of constructing raw queries.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

logger = logging.getLogger(__name__)


from opendb_core.storage._pg_memory import PgMemoryMixin


class PostgresBackend(PgMemoryMixin):
    """PostgreSQL implementation of StorageBackend.

    Does NOT manage the asyncpg pool itself — it delegates to
    ``app.database.get_pool()``, which is initialised by ``app/main.py``.
    """

    def __init__(self, *, workspace_id: str = "_default") -> None:
        self.workspace_id = workspace_id

    async def init(self) -> None:
        """Run lightweight schema migrations (add columns if missing)."""
        await self._migrate_cjk_columns()
        await self._migrate_eval_and_links()
        await self._backfill_code_symbols_if_needed()

    async def close(self) -> None:
        pass  # Pool lifecycle owned by app/database.py

    async def _migrate_cjk_columns(self) -> None:
        """Add jieba tokenization columns and pinned flag if missing."""
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            # pages.text_jieba
            exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'pages' AND column_name = 'text_jieba'"
            )
            if not exists:
                await conn.execute("ALTER TABLE pages ADD COLUMN text_jieba TEXT")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pages_jieba ON pages USING GIN("
                    "to_tsvector('simple', COALESCE(text_jieba, '')))"
                )
                logger.info("Added pages.text_jieba column + GIN index")

            # memories.pinned
            exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'memories' AND column_name = 'pinned'"
            )
            if not exists:
                await conn.execute(
                    "ALTER TABLE memories ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT false"
                )
                logger.info("Added memories.pinned column")

            # memories.content_jieba
            exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'memories' AND column_name = 'content_jieba'"
            )
            if not exists:
                await conn.execute("ALTER TABLE memories ADD COLUMN content_jieba TEXT")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_jieba ON memories USING GIN("
                    "to_tsvector('simple', COALESCE(content_jieba, '')))"
                )
                logger.info("Added memories.content_jieba column + GIN index")

            # v1.6 memory hardening columns
            for col, ddl in [
                ("source", "ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'"),
                ("superseded_id", "ALTER TABLE memories ADD COLUMN superseded_id UUID DEFAULT NULL"),
                ("confidence", "ALTER TABLE memories ADD COLUMN confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0"),
                ("last_accessed", "ALTER TABLE memories ADD COLUMN last_accessed TIMESTAMPTZ DEFAULT NULL"),
                ("access_count", "ALTER TABLE memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0"),
                ("workspace_id", "ALTER TABLE memories ADD COLUMN workspace_id TEXT NOT NULL DEFAULT '_default'"),
            ]:
                exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'memories' AND column_name = $1", col
                )
                if not exists:
                    await conn.execute(ddl)
                    logger.info("Added memories.%s column", col)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_workspace ON memories(workspace_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_confidence "
                "ON memories(confidence) WHERE confidence >= 0.3"
            )

    async def _migrate_eval_and_links(self) -> None:
        """Create opt-in eval capture and lightweight file-link tables."""
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_captures (
                    id            BIGSERIAL PRIMARY KEY,
                    tool_name     TEXT NOT NULL CHECK (tool_name IN ('search', 'memory_recall')),
                    query         TEXT NOT NULL CHECK (length(query) <= 51200),
                    result_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
                    result_count  INTEGER NOT NULL DEFAULT 0,
                    latency_ms    INTEGER NOT NULL DEFAULT 0,
                    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_captures_created "
                "ON eval_captures(created_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_captures_tool "
                "ON eval_captures(tool_name)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_links (
                    id            BIGSERIAL PRIMARY KEY,
                    from_file_id  UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    to_file_id    UUID REFERENCES files(id) ON DELETE SET NULL,
                    target        TEXT NOT NULL,
                    link_type     TEXT NOT NULL DEFAULT 'reference',
                    context       TEXT NOT NULL DEFAULT '',
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(from_file_id, target, link_type)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_links_from ON file_links(from_file_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_links_to ON file_links(to_file_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_links_target ON file_links(target)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS code_symbols (
                    id             BIGSERIAL PRIMARY KEY,
                    file_id        UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    name           TEXT NOT NULL,
                    kind           TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    start_line     INTEGER NOT NULL,
                    end_line       INTEGER NOT NULL,
                    signature      TEXT NOT NULL DEFAULT '',
                    docstring      TEXT NOT NULL DEFAULT '',
                    tsv            TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('english', name || ' ' || qualified_name || ' ' || signature || ' ' || docstring)
                    ) STORED,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_code_symbols_file ON code_symbols(file_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_code_symbols_name ON code_symbols(name)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_code_symbols_kind ON code_symbols(kind)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_code_symbols_tsv ON code_symbols USING GIN(tsv)"
            )

    async def _backfill_code_symbols_if_needed(self) -> None:
        """Populate code_symbols for ready code files indexed before this table existed."""
        from opendb_core.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    f.id::text AS file_id,
                    f.filename,
                    f.metadata,
                    p.page_number,
                    p.text,
                    p.line_start,
                    p.line_end
                FROM files f
                JOIN pages p ON p.file_id = f.id
                WHERE f.status = 'ready'
                  AND NOT EXISTS (
                    SELECT 1 FROM code_symbols s WHERE s.file_id = f.id
                  )
                  AND (
                    lower(f.filename) LIKE '%.py'
                    OR lower(f.filename) LIKE '%.js'
                    OR lower(f.filename) LIKE '%.jsx'
                    OR lower(f.filename) LIKE '%.ts'
                    OR lower(f.filename) LIKE '%.tsx'
                    OR lower(f.metadata->>'source_path') LIKE '%.py'
                    OR lower(f.metadata->>'source_path') LIKE '%.js'
                    OR lower(f.metadata->>'source_path') LIKE '%.jsx'
                    OR lower(f.metadata->>'source_path') LIKE '%.ts'
                    OR lower(f.metadata->>'source_path') LIKE '%.tsx'
                  )
                ORDER BY f.id, p.page_number
                """
            )
            if not rows:
                return

            from opendb_core.config import settings
            from opendb_core.utils.code_intel import extract_code_intel_from_pages

            current_file_id = None
            group = []
            backfilled = 0

            async def flush_group(items) -> None:
                nonlocal backfilled
                if not items:
                    return
                first = items[0]
                metadata = first["metadata"]
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                source_path = (metadata or {}).get("source_path") or first["filename"]
                pages = [
                    SimpleNamespace(page_number=row["page_number"], text=row["text"])
                    for row in items
                ]
                line_ranges = [(row["line_start"], row["line_end"]) for row in items]
                symbols, code_links = extract_code_intel_from_pages(
                    pages,
                    line_ranges,
                    filename=first["filename"],
                    source_path=source_path,
                )
                await self._replace_code_symbols_unlocked(conn, first["file_id"], symbols)
                if settings.link_extraction_enabled and code_links:
                    await self._insert_file_links_unlocked(conn, first["file_id"], code_links)
                backfilled += 1

            async with conn.transaction():
                for row in rows:
                    if current_file_id is None:
                        current_file_id = row["file_id"]
                    if row["file_id"] != current_file_id:
                        await flush_group(group)
                        group = []
                        current_file_id = row["file_id"]
                    group.append(row)
                await flush_group(group)
            if backfilled:
                logger.info("Backfilled code symbols for %d existing code files.", backfilled)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def check_duplicate(self, checksum: str) -> dict | None:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, filename FROM files WHERE checksum = $1 AND status = 'ready'",
                checksum,
            )
        if row:
            return {
                "id": str(row["id"]),
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
        import uuid as _uuid
        import asyncpg
        from opendb_core.database import get_pool

        file_uuid = _uuid.UUID(file_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO files (id, filename, mime_type, file_size, file_path,
                                           checksum, status, tags, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6, 'processing', $7, $8)
                        """,
                        file_uuid,
                        original_filename,
                        mime_type,
                        file_size,
                        file_path,
                        checksum,
                        tags,
                        json.dumps(merged_metadata),
                    )

                    if parse_result.pages:
                        from opendb_core.utils.tokenizer import tokenize_for_fts
                        await conn.executemany(
                            """
                            INSERT INTO pages (file_id, page_number, section_title,
                                               content_type, text, line_start, line_end,
                                               text_jieba)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            """,
                            [
                                (
                                    file_uuid,
                                    page.page_number,
                                    page.section_title,
                                    page.content_type,
                                    page.text,
                                    page_line_ranges[i][0],
                                    page_line_ranges[i][1],
                                    tokenize_for_fts(page.text),
                                )
                                for i, page in enumerate(parse_result.pages)
                            ],
                        )

                    await conn.execute(
                        """
                        INSERT INTO file_text (file_id, full_text, total_lines, line_index, toc)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        file_uuid,
                        full_text,
                        total_lines,
                        line_index,
                        toc,
                    )

                    from opendb_core.config import settings
                    source_path = merged_metadata.get("source_path") or original_filename
                    from opendb_core.utils.code_intel import extract_code_intel_from_pages
                    symbols, code_links = extract_code_intel_from_pages(
                        parse_result.pages,
                        page_line_ranges,
                        filename=original_filename,
                        source_path=source_path,
                    )
                    await self._replace_code_symbols_unlocked(conn, str(file_uuid), symbols)
                    if settings.link_extraction_enabled:
                        from opendb_core.utils.link_extractor import extract_file_links
                        links = extract_file_links(full_text, source_path=source_path)
                        links.extend(code_links)
                        await self._replace_file_links_unlocked(conn, str(file_uuid), links)

                    await conn.execute(
                        "UPDATE files SET status = 'ready', metadata = $2 WHERE id = $1",
                        file_uuid,
                        json.dumps(merged_metadata),
                    )
                    if settings.link_extraction_enabled:
                        await self._reconcile_links_to_file_unlocked(conn, str(file_uuid))

            except asyncpg.UniqueViolationError:
                existing = await conn.fetchrow(
                    "SELECT id, filename FROM files WHERE checksum = $1 AND status = 'ready'",
                    checksum,
                )
                if existing:
                    return {
                        "id": str(existing["id"]),
                        "filename": existing["filename"],
                        "status": "duplicate",
                        "detail": "File with identical content already exists",
                    }
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
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE files SET status = 'failed', error_message = $2 WHERE id = $1",
                _uuid.UUID(file_id),
                error,
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_file_text(self, file_id: str) -> dict:
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT full_text, total_lines, line_index, toc "
                "FROM file_text WHERE file_id = $1",
                _uuid.UUID(file_id),
            )
        if not row:
            from opendb_core.services.read_service import FileNotFoundError
            raise FileNotFoundError(f"No text found for file {file_id}")
        return dict(row)

    async def get_total_pages(self, file_id: str) -> int:
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM pages WHERE file_id = $1",
                _uuid.UUID(file_id),
            )
        return count

    async def get_page_line_ranges(
        self, file_id: str, page_numbers: list[int]
    ) -> list[tuple[int, int]]:
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT line_start, line_end FROM pages "
                "WHERE file_id = $1 AND page_number = ANY($2) "
                "ORDER BY page_number",
                _uuid.UUID(file_id),
                page_numbers,
            )
        return [(r["line_start"], r["line_end"]) for r in rows]

    async def get_page_by_section_title(
        self, file_id: str, title: str
    ) -> list[int]:
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT page_number FROM pages "
                "WHERE file_id = $1 AND section_title ILIKE $2 "
                "ORDER BY page_number",
                _uuid.UUID(file_id),
                f"%{title}%",
            )
        return [r["page_number"] for r in rows]

    async def get_file_info(self, file_id: str) -> dict:
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT file_path, mime_type, filename FROM files WHERE id = $1",
                _uuid.UUID(file_id),
            )
        if not row:
            from opendb_core.services.read_service import FileNotFoundError
            raise FileNotFoundError(f"File {file_id} not found")
        return dict(row)

    async def get_sheet_names_for_pages(
        self, file_id: str, page_nums: list[int]
    ) -> list[str]:
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT section_title FROM pages "
                "WHERE file_id = $1 AND page_number = ANY($2) "
                "AND section_title IS NOT NULL",
                _uuid.UUID(file_id),
                page_nums,
            )
        names: set[str] = set()
        for r in rows:
            title = r["section_title"]
            base = title.split(" - rows ")[0] if " - rows " in title else title
            names.add(base)
        return list(names)

    # ------------------------------------------------------------------
    # Filename resolution
    # ------------------------------------------------------------------

    async def find_by_source_path(self, source_path: str) -> str | None:
        """Find a file by its original source path (stored in metadata)."""
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM files "
                "WHERE metadata @> $1::jsonb AND status = 'ready'",
                json.dumps({"source_path": source_path}),
            )
        return str(row["id"]) if row else None

    async def find_file_exact(self, filename: str) -> str | None:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM files WHERE filename = $1 AND status = 'ready'",
                filename,
            )
        return str(row["id"]) if row else None

    async def find_file_by_uuid(self, file_id_str: str) -> str | None:
        import uuid as _uuid
        from opendb_core.database import get_pool
        try:
            file_uuid = _uuid.UUID(file_id_str)
        except ValueError:
            return None
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM files WHERE id = $1 AND status = 'ready'",
                file_uuid,
            )
        return str(row["id"]) if row else None

    async def find_files_fuzzy(self, filename: str) -> list[dict]:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, filename, similarity(filename, $1) AS sim "
                "FROM files WHERE filename % $1 AND status = 'ready' "
                "ORDER BY sim DESC LIMIT 5",
                filename,
            )
        return [{"id": str(r["id"]), "filename": r["filename"], "sim": r["sim"]} for r in rows]

    async def find_files_ilike(self, pattern: str) -> list[dict]:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, filename FROM files "
                "WHERE filename ILIKE $1 AND status = 'ready'",
                f"%{pattern}%",
            )
        return [{"id": str(r["id"]), "filename": r["filename"]} for r in rows]

    async def find_by_source_path_suffix(self, suffix: str) -> list[dict]:
        norm = suffix.replace("\\", "/").lstrip("/")
        pattern = "%/" + norm
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, filename FROM files "
                "WHERE metadata->>'source_path' LIKE $1 AND status = 'ready'",
                pattern,
            )
        return [{"id": str(r["id"]), "filename": r["filename"]} for r in rows]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_fts(
        self, query: str, filters: dict, limit: int, offset: int
    ) -> dict:
        from opendb_core.utils.tokenizer import _CJK_RE, tokenize_for_fts
        from opendb_core.database import get_pool

        has_cjk = bool(_CJK_RE.search(query))
        pool = await get_pool()
        async with pool.acquire() as conn:
            if has_cjk:
                # CJK path: use jieba-tokenized column with 'simple' config
                tokenized = tokenize_for_fts(query)
                conditions = [
                    "to_tsvector('simple', COALESCE(p.text_jieba, '')) "
                    "@@ plainto_tsquery('simple', $1)",
                    "f.status = 'ready'",
                ]
                params: list = [tokenized]
                _add_pg_filters(conditions, params, filters)
                where_clause = " AND ".join(conditions)
                n = len(params)
                params.extend([limit, offset])

                search_sql = f"""
                    SELECT
                        f.filename, f.id AS file_id, p.page_number, p.section_title,
                        ts_rank_cd(
                            to_tsvector('simple', COALESCE(p.text_jieba, '')),
                            plainto_tsquery('simple', $1)
                        ) AS relevance_score,
                        ts_headline('simple', COALESCE(p.text_jieba, ''),
                            plainto_tsquery('simple', $1),
                            'StartSel=<mark>, StopSel=</mark>, MaxWords=35, MinWords=15'
                        ) AS highlight,
                        f.updated_at
                    FROM pages p
                    JOIN files f ON f.id = p.file_id
                    WHERE {where_clause}
                    ORDER BY relevance_score DESC
                    LIMIT ${n + 1} OFFSET ${n + 2}
                """
            else:
                # Latin path: use stored tsv column with 'english' config
                conditions = [
                    "p.tsv @@ plainto_tsquery('english', $1)",
                    "f.status = 'ready'",
                ]
                params = [query]
                _add_pg_filters(conditions, params, filters)
                where_clause = " AND ".join(conditions)
                n = len(params)
                params.extend([limit, offset])

                search_sql = f"""
                    SELECT
                        f.filename, f.id AS file_id, p.page_number, p.section_title,
                        ts_rank_cd(p.tsv, plainto_tsquery('english', $1)) AS relevance_score,
                        ts_headline('english', p.text, plainto_tsquery('english', $1),
                            'StartSel=<mark>, StopSel=</mark>, MaxWords=35, MinWords=15'
                        ) AS highlight,
                        f.updated_at
                    FROM pages p
                    JOIN files f ON f.id = p.file_id
                    WHERE {where_clause}
                    ORDER BY relevance_score DESC
                    LIMIT ${n + 1} OFFSET ${n + 2}
                """

            count_sql = f"""
                SELECT COUNT(*) FROM pages p JOIN files f ON f.id = p.file_id
                WHERE {where_clause}
            """

            rows = await conn.fetch(search_sql, *params)
            total = await conn.fetchval(count_sql, *params[:n])

        results = [
            {
                "filename": r["filename"],
                "file_id": str(r["file_id"]),
                "page_number": r["page_number"],
                "section_title": r["section_title"],
                "highlight": r["highlight"] or "",
                "relevance_score": float(r["relevance_score"]),
                "updated_at": r["updated_at"].isoformat() + "Z" if r["updated_at"] else None,
            }
            for r in rows
        ]
        from opendb_core.config import settings
        if settings.backlink_boost_enabled and results:
            counts = await self.get_backlink_counts([r["file_id"] for r in results])
            weight = max(0.0, settings.backlink_boost_weight)
            for r in results:
                count = counts.get(r["file_id"], 0)
                if count:
                    r["relevance_score"] *= 1.0 + weight * count
            results.sort(key=lambda r: r["relevance_score"], reverse=True)
        for r in results:
            r["relevance_score"] = round(float(r["relevance_score"]), 3)

        return {
            "total": total,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def batch_check_duplicates(self, checksums: list[str]) -> set[str]:
        if not checksums:
            return set()
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT checksum FROM files "
                "WHERE checksum = ANY($1) AND status = 'ready'",
                checksums,
            )
        return {row["checksum"] for row in rows}

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
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = ["f.status = 'ready'"]
            params: list = []
            idx = 0

            if filters.get("tags"):
                idx += 1
                conditions.append(f"f.tags @> ${idx}::text[]")
                params.append([filters["tags"]])

            if filters.get("mime_type"):
                idx += 1
                conditions.append(f"f.mime_type = ${idx}")
                params.append(filters["mime_type"])

            if filters.get("filename"):
                idx += 1
                conditions.append(f"f.filename % ${idx}")
                params.append(filters["filename"])

            where_clause = " AND ".join(conditions)
            n = len(params)
            params.extend([limit, offset])

            query = f"""
                SELECT f.id, f.filename, f.mime_type, f.file_size,
                       f.tags, f.metadata, f.created_at, f.updated_at, f.status,
                       ft.total_lines,
                       (SELECT COUNT(*) FROM pages p WHERE p.file_id = f.id) AS total_pages
                FROM files f
                LEFT JOIN file_text ft ON ft.file_id = f.id
                WHERE {where_clause}
                ORDER BY f.{sort_field} {sort_dir}
                LIMIT ${n + 1} OFFSET ${n + 2}
            """
            count_query = f"SELECT COUNT(*) FROM files f WHERE {where_clause}"

            rows = await conn.fetch(query, *params)
            total = await conn.fetchval(count_query, *params[:n])

        files = [_pg_file_row(r) for r in rows]
        return {"total": total, "files": files}

    async def get_file_by_id(self, file_id: str) -> dict | None:
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT f.id, f.filename, f.mime_type, f.file_size,
                       f.tags, f.metadata, f.created_at, f.updated_at, f.status,
                       ft.total_lines,
                       (SELECT COUNT(*) FROM pages p WHERE p.file_id = f.id) AS total_pages
                FROM files f
                LEFT JOIN file_text ft ON ft.file_id = f.id
                WHERE f.id = $1
                """,
                _uuid.UUID(file_id),
            )
        return _pg_file_row(row) if row else None

    async def delete_file(self, file_id: str) -> str | None:
        import uuid as _uuid
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT file_path FROM files WHERE id = $1",
                _uuid.UUID(file_id),
            )
            if not row:
                return None
            await conn.execute(
                "DELETE FROM files WHERE id = $1",
                _uuid.UUID(file_id),
            )
        return row["file_path"]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_workspace_stats(self) -> dict:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            status_rows = await conn.fetch(
                "SELECT status, COUNT(*) AS cnt FROM files GROUP BY status"
            )
            by_status = {r["status"]: r["cnt"] for r in status_rows}

            type_rows = await conn.fetch(
                "SELECT mime_type, COUNT(*) AS cnt FROM files "
                "WHERE status = 'ready' GROUP BY mime_type ORDER BY cnt DESC"
            )
            by_type = [(r["mime_type"], r["cnt"]) for r in type_rows]

            recent_rows = await conn.fetch(
                "SELECT filename, updated_at FROM files "
                "WHERE status = 'ready' ORDER BY updated_at DESC LIMIT 5"
            )
            recent = [
                {
                    "filename": r["filename"],
                    "updated_at": r["updated_at"].isoformat() + "Z" if r["updated_at"] else None,
                }
                for r in recent_rows
            ]

        # Memory stats
        async with pool.acquire() as conn:
            mem_rows = await conn.fetch(
                "SELECT memory_type, COUNT(*) AS cnt FROM memories GROUP BY memory_type"
            )
        memory_by_type = {r["memory_type"]: r["cnt"] for r in mem_rows}
        memory_total = sum(memory_by_type.values())

        return {
            "by_status": by_status,
            "by_type": by_type,
            "recent": recent,
            "memory": {
                "total": memory_total,
                "by_type": memory_by_type,
            },
        }

    # ------------------------------------------------------------------
    # Agent Memory — see _pg_memory.py (PgMemoryMixin)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Eval capture
    # ------------------------------------------------------------------

    async def log_eval_capture(
        self,
        *,
        tool_name: str,
        query: str,
        result_ids: list[str],
        result_count: int,
        latency_ms: int,
        metadata: dict,
    ) -> None:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO eval_captures
                    (tool_name, query, result_ids, result_count, latency_ms, metadata)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6::jsonb)
                """,
                tool_name,
                query[:51200],
                json.dumps(result_ids),
                result_count,
                latency_ms,
                json.dumps(metadata),
            )

    async def export_eval_captures(
        self,
        *,
        limit: int = 1000,
        tool_name: str | None = None,
    ) -> list[dict]:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            if tool_name:
                rows = await conn.fetch(
                    """
                    SELECT id, tool_name, query, result_ids, result_count,
                           latency_ms, metadata, created_at
                    FROM eval_captures
                    WHERE tool_name = $1
                    ORDER BY id DESC
                    LIMIT $2
                    """,
                    tool_name,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, tool_name, query, result_ids, result_count,
                           latency_ms, metadata, created_at
                    FROM eval_captures
                    ORDER BY id DESC
                    LIMIT $1
                    """,
                    limit,
                )
        exported = []
        for r in rows:
            result_ids = r["result_ids"]
            if isinstance(result_ids, str):
                result_ids = json.loads(result_ids)
            metadata = r["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            exported.append({
                "id": int(r["id"]),
                "tool_name": r["tool_name"],
                "query": r["query"],
                "result_ids": result_ids,
                "result_count": r["result_count"],
                "latency_ms": r["latency_ms"],
                "metadata": metadata,
                "created_at": r["created_at"].isoformat() + "Z" if r["created_at"] else None,
            })
        return exported

    # ------------------------------------------------------------------
    # File links
    # ------------------------------------------------------------------

    async def _resolve_link_target_pg(self, conn, target: str) -> str | None:
        row = await conn.fetchrow(
            "SELECT id FROM files WHERE metadata->>'source_path' = $1 "
            "AND status = 'ready'",
            target,
        )
        if row:
            return str(row["id"])

        suffix = target.replace("\\", "/").lstrip("/")
        row = await conn.fetchrow(
            "SELECT id FROM files WHERE metadata->>'source_path' LIKE $1 "
            "AND status = 'ready' ORDER BY length(metadata->>'source_path') LIMIT 1",
            f"%/{suffix}",
        )
        if row:
            return str(row["id"])

        from pathlib import Path as _Path
        basename = _Path(target).name
        if basename:
            row = await conn.fetchrow(
                "SELECT id FROM files WHERE filename = $1 AND status = 'ready' LIMIT 1",
                basename,
            )
            if row:
                return str(row["id"])
        return None

    async def _replace_file_links_unlocked(self, conn, file_id: str, links: list[dict]) -> int:
        import uuid as _uuid

        file_uuid = _uuid.UUID(file_id)
        await conn.execute("DELETE FROM file_links WHERE from_file_id = $1", file_uuid)
        return await self._insert_file_links_unlocked(conn, file_id, links)

    async def _insert_file_links_unlocked(self, conn, file_id: str, links: list[dict]) -> int:
        import uuid as _uuid

        file_uuid = _uuid.UUID(file_id)
        rows = []
        for link in links:
            target = str(link.get("target", "")).strip()
            if not target:
                continue
            resolved = await self._resolve_link_target_pg(conn, target)
            rows.append((
                file_uuid,
                _uuid.UUID(resolved) if resolved else None,
                target,
                str(link.get("link_type") or "reference"),
                str(link.get("context") or "")[:500],
            ))
        if rows:
            await conn.executemany(
                """
                INSERT INTO file_links
                    (from_file_id, to_file_id, target, link_type, context)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (from_file_id, target, link_type) DO NOTHING
                """,
                rows,
            )
        return len(rows)

    async def _reconcile_links_to_file_unlocked(self, conn, file_id: str) -> None:
        import uuid as _uuid

        file_uuid = _uuid.UUID(file_id)
        row = await conn.fetchrow(
            "SELECT filename, metadata FROM files WHERE id = $1 AND status = 'ready'",
            file_uuid,
        )
        if not row:
            return
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        source_path = str((metadata or {}).get("source_path") or "").replace("\\", "/")
        filename = row["filename"]
        if not source_path and not filename:
            return
        await conn.execute(
            """
            UPDATE file_links
            SET to_file_id = $1
            WHERE to_file_id IS NULL
              AND (
                target = $2
                OR ($2 != '' AND $2 LIKE '%' || CASE
                    WHEN substr(target, 1, 1) = '/' THEN target
                    ELSE '/' || target
                  END)
                OR target = $3
              )
            """,
            file_uuid,
            source_path,
            filename,
        )

    async def replace_file_links(
        self,
        *,
        file_id: str,
        links: list[dict],
    ) -> int:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await self._replace_file_links_unlocked(conn, file_id, links)

    async def get_backlink_counts(self, file_ids: list[str]) -> dict[str, int]:
        import uuid as _uuid
        ids = [_uuid.UUID(fid) for fid in sorted({fid for fid in file_ids if fid})]
        if not ids:
            return {}
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT to_file_id, COUNT(*) AS cnt
                FROM file_links
                WHERE to_file_id = ANY($1::uuid[])
                GROUP BY to_file_id
                """,
                ids,
            )
        return {str(r["to_file_id"]): int(r["cnt"]) for r in rows}

    async def _replace_code_symbols_unlocked(self, conn, file_id: str, symbols: list[dict]) -> int:
        import uuid as _uuid

        file_uuid = _uuid.UUID(file_id)
        await conn.execute("DELETE FROM code_symbols WHERE file_id = $1", file_uuid)
        rows = []
        for symbol in symbols:
            name = str(symbol.get("name") or "").strip()
            if not name:
                continue
            rows.append((
                file_uuid,
                name,
                str(symbol.get("kind") or "symbol"),
                str(symbol.get("qualified_name") or name),
                int(symbol.get("start_line") or 1),
                int(symbol.get("end_line") or symbol.get("start_line") or 1),
                str(symbol.get("signature") or "")[:1000],
                str(symbol.get("docstring") or "")[:2000],
            ))
        if rows:
            await conn.executemany(
                """
                INSERT INTO code_symbols
                    (file_id, name, kind, qualified_name, start_line, end_line, signature, docstring)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                rows,
            )
        return len(rows)

    async def search_code_symbols(
        self,
        query: str,
        *,
        limit: int = 20,
        kinds: list[str] | None = None,
    ) -> list[dict]:
        from opendb_core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            kind_clause = ""
            params: list = [query, limit]
            if kinds:
                kind_clause = "AND s.kind = ANY($3::text[])"
                params.append(kinds)
            rows = await conn.fetch(
                f"""
                SELECT
                    s.file_id::text AS file_id,
                    f.filename,
                    f.metadata->>'source_path' AS source_path,
                    s.name,
                    s.kind,
                    s.qualified_name,
                    s.start_line,
                    s.end_line,
                    s.signature,
                    s.docstring,
                    ts_rank(s.tsv, plainto_tsquery('english', $1)) AS rank
                FROM code_symbols s
                JOIN files f ON f.id = s.file_id
                WHERE s.tsv @@ plainto_tsquery('english', $1)
                  AND f.status = 'ready'
                  {kind_clause}
                ORDER BY rank DESC, length(s.qualified_name), s.start_line
                LIMIT $2
                """,
                *params,
            )
            if not rows:
                kind_clause = ""
                params = [f"%{query}%", limit]
                if kinds:
                    kind_clause = "AND s.kind = ANY($3::text[])"
                    params.append(kinds)
                rows = await conn.fetch(
                    f"""
                    SELECT
                        s.file_id::text AS file_id,
                        f.filename,
                        f.metadata->>'source_path' AS source_path,
                        s.name,
                        s.kind,
                        s.qualified_name,
                        s.start_line,
                        s.end_line,
                        s.signature,
                        s.docstring,
                        0.0 AS rank
                    FROM code_symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE (s.name ILIKE $1 OR s.qualified_name ILIKE $1)
                      AND f.status = 'ready'
                      {kind_clause}
                    ORDER BY length(s.qualified_name), s.start_line
                    LIMIT $2
                    """,
                    *params,
                )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers — delegated to opendb_core.storage.shared
# ---------------------------------------------------------------------------

from opendb_core.storage.shared import (  # noqa: E402
    add_pg_filters as _add_pg_filters,
    pg_file_row as _pg_file_row,
)
