"""PostgreSQL storage backend.

Wraps the existing asyncpg pool (managed by app/database.py).
All SQL here is PostgreSQL-dialect; the service layer calls these methods
instead of constructing raw queries.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class PostgresBackend:
    """PostgreSQL implementation of StorageBackend.

    Does NOT manage the asyncpg pool itself — it delegates to
    ``app.database.get_pool()``, which is initialised by ``app/main.py``.
    """

    async def init(self) -> None:
        """Run lightweight schema migrations (add columns if missing)."""
        await self._migrate_cjk_columns()

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

                    await conn.execute(
                        "UPDATE files SET status = 'ready', metadata = $2 WHERE id = $1",
                        file_uuid,
                        json.dumps(merged_metadata),
                    )

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

        return {
            "total": total,
            "results": [
                {
                    "filename": r["filename"],
                    "file_id": str(r["file_id"]),
                    "page_number": r["page_number"],
                    "section_title": r["section_title"],
                    "highlight": r["highlight"] or "",
                    "relevance_score": round(float(r["relevance_score"]), 3),
                    "updated_at": r["updated_at"].isoformat() + "Z" if r["updated_at"] else None,
                }
                for r in rows
            ],
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
        pinned: bool = False,
    ) -> dict:
        import uuid as _uuid
        from opendb_core.database import get_pool
        from opendb_core.utils.tokenizer import tokenize_for_fts

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO memories (id, content, memory_type, pinned, tags, metadata,
                                      content_jieba)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                RETURNING id, content, memory_type, pinned, tags, metadata, created_at, updated_at
                """,
                _uuid.UUID(memory_id),
                content,
                memory_type,
                pinned,
                tags,
                json.dumps(metadata),
                tokenize_for_fts(content),
            )
        return _pg_memory_row(row)

    async def recall_memories(
        self,
        query: str,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
        pinned_only: bool = False,
    ) -> dict:
        from opendb_core.database import get_pool

        pool = await get_pool()

        # Fast path: pinned-only retrieval without FTS
        if pinned_only:
            async with pool.acquire() as conn:
                conditions = ["m.pinned = true"]
                params: list = []
                idx = 0
                if memory_type:
                    idx += 1
                    conditions.append(f"m.memory_type = ${idx}")
                    params.append(memory_type)
                if tags:
                    idx += 1
                    conditions.append(f"m.tags @> ${idx}::text[]")
                    params.append(tags)
                where = " AND ".join(conditions)
                n = len(params)
                rows = await conn.fetch(
                    f"SELECT m.* FROM memories m WHERE {where} "
                    f"ORDER BY m.created_at DESC LIMIT ${n+1} OFFSET ${n+2}",
                    *params, limit, offset,
                )
                total = await conn.fetchval(
                    f"SELECT COUNT(*) FROM memories m WHERE {where}", *params
                )
            return {"total": total or 0, "results": [
                {**_pg_memory_row(r), "score": 1.0} for r in rows
            ]}

        from opendb_core.utils.tokenizer import _CJK_RE, tokenize_for_fts
        from opendb_core.config import settings

        has_cjk = bool(_CJK_RE.search(query))
        halflife = settings.memory_decay_halflife_days

        async with pool.acquire() as conn:
            if has_cjk:
                tokenized = tokenize_for_fts(query)
                fts_cond = (
                    "to_tsvector('simple', COALESCE(m.content_jieba, '')) "
                    "@@ plainto_tsquery('simple', $1)"
                )
                rank_expr = (
                    "ts_rank_cd(to_tsvector('simple', COALESCE(m.content_jieba, '')), "
                    "plainto_tsquery('simple', $1))"
                )
                headline_expr = (
                    "ts_headline('simple', COALESCE(m.content_jieba, ''), "
                    "plainto_tsquery('simple', $1), 'MaxWords=30, MinWords=10')"
                )
                query_param = tokenized
            else:
                fts_cond = (
                    "to_tsvector('english', m.content) "
                    "@@ plainto_tsquery('english', $1)"
                )
                rank_expr = (
                    "ts_rank_cd(to_tsvector('english', m.content), "
                    "plainto_tsquery('english', $1))"
                )
                headline_expr = (
                    "ts_headline('english', m.content, "
                    "plainto_tsquery('english', $1), 'MaxWords=30, MinWords=10')"
                )
                query_param = query

            conditions = [fts_cond]
            params: list = [query_param]
            idx = 1

            if memory_type:
                idx += 1
                conditions.append(f"m.memory_type = ${idx}")
                params.append(memory_type)
            if tags:
                idx += 1
                conditions.append(f"m.tags @> ${idx}::text[]")
                params.append(tags)

            where_clause = " AND ".join(conditions)
            n = len(params)

            search_sql = f"""
                SELECT m.id, m.content, m.memory_type, m.pinned, m.tags, m.metadata,
                       m.created_at, m.updated_at,
                       {rank_expr} AS fts_score,
                       ({rank_expr}
                        * power(0.5, EXTRACT(EPOCH FROM (now() - m.created_at))
                                     / 86400.0 / {halflife:.1f})
                        * CASE WHEN m.pinned THEN 10.0 ELSE 1.0 END) AS score,
                       {headline_expr} AS highlight
                FROM memories m
                WHERE {where_clause}
                ORDER BY score DESC
                LIMIT ${n + 1} OFFSET ${n + 2}
            """
            count_sql = f"""
                SELECT COUNT(*) FROM memories m WHERE {where_clause}
            """

            rows = await conn.fetch(search_sql, *params, limit, offset)
            total = await conn.fetchval(count_sql, *params)

        results = []
        for r in rows:
            results.append({
                "memory_id": str(r["id"]),
                "content": r["content"],
                "memory_type": r["memory_type"],
                "pinned": bool(r.get("pinned", False)),
                "tags": r["tags"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "highlight": r["highlight"] or "",
                "score": round(float(r["score"]), 4) if r["score"] else 0.0,
                "created_at": r["created_at"].isoformat() + "Z" if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() + "Z" if r["updated_at"] else None,
            })
        return {"total": total or 0, "results": results}

    async def get_memory(self, memory_id: str) -> dict | None:
        import uuid as _uuid
        from opendb_core.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, content, memory_type, tags, metadata, "
                "created_at, updated_at FROM memories WHERE id = $1",
                _uuid.UUID(memory_id),
            )
        return _pg_memory_row(row) if row else None

    async def delete_memory(self, memory_id: str) -> bool:
        import uuid as _uuid
        from opendb_core.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM memories WHERE id = $1", _uuid.UUID(memory_id)
            )
        return result == "DELETE 1"

    async def list_memories(
        self,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
    ) -> dict:
        from opendb_core.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions: list[str] = []
            params: list = []
            idx = 0

            if memory_type:
                idx += 1
                conditions.append(f"memory_type = ${idx}")
                params.append(memory_type)
            if tags:
                idx += 1
                conditions.append(f"tags @> ${idx}::text[]")
                params.append(tags)

            where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            n = len(params)

            query = f"""
                SELECT id, content, memory_type, tags, metadata,
                       created_at, updated_at
                FROM memories
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ${n + 1} OFFSET ${n + 2}
            """
            count_query = f"SELECT COUNT(*) FROM memories {where_clause}"

            rows = await conn.fetch(query, *params, limit, offset)
            total = await conn.fetchval(count_query, *params)

        return {
            "total": total or 0,
            "memories": [_pg_memory_row(r) for r in rows],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers — delegated to opendb_core.storage.shared
# ---------------------------------------------------------------------------

from opendb_core.storage.shared import (  # noqa: E402
    add_pg_filters as _add_pg_filters,
    pg_memory_row as _pg_memory_row,
    pg_file_row as _pg_file_row,
)
