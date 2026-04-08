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
        pass  # Pool lifecycle owned by app/database.py

    async def close(self) -> None:
        pass  # Pool lifecycle owned by app/database.py

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def check_duplicate(self, checksum: str) -> dict | None:
        from app.database import get_pool
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
        from app.database import get_pool

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

                    for i, page in enumerate(parse_result.pages):
                        line_start, line_end = page_line_ranges[i]
                        await conn.execute(
                            """
                            INSERT INTO pages (file_id, page_number, section_title,
                                               content_type, text, line_start, line_end)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                            """,
                            file_uuid,
                            page.page_number,
                            page.section_title,
                            page.content_type,
                            page.text,
                            line_start,
                            line_end,
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
        from app.database import get_pool
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
        from app.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT full_text, total_lines, line_index, toc "
                "FROM file_text WHERE file_id = $1",
                _uuid.UUID(file_id),
            )
        if not row:
            from app.services.read_service import FileNotFoundError
            raise FileNotFoundError(f"No text found for file {file_id}")
        return dict(row)

    async def get_total_pages(self, file_id: str) -> int:
        import uuid as _uuid
        from app.database import get_pool
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
        from app.database import get_pool
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
        from app.database import get_pool
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
        from app.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT file_path, mime_type, filename FROM files WHERE id = $1",
                _uuid.UUID(file_id),
            )
        if not row:
            from app.services.read_service import FileNotFoundError
            raise FileNotFoundError(f"File {file_id} not found")
        return dict(row)

    async def get_sheet_names_for_pages(
        self, file_id: str, page_nums: list[int]
    ) -> list[str]:
        import uuid as _uuid
        from app.database import get_pool
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

    async def find_file_exact(self, filename: str) -> str | None:
        from app.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM files WHERE filename = $1 AND status = 'ready'",
                filename,
            )
        return str(row["id"]) if row else None

    async def find_file_by_uuid(self, file_id_str: str) -> str | None:
        import uuid as _uuid
        from app.database import get_pool
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
        from app.database import get_pool
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
        from app.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, filename FROM files "
                "WHERE filename ILIKE $1 AND status = 'ready'",
                f"%{pattern}%",
            )
        return [{"id": str(r["id"]), "filename": r["filename"]} for r in rows]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_fts(
        self, query: str, filters: dict, limit: int, offset: int
    ) -> dict:
        from app.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = ["p.tsv @@ plainto_tsquery('english', $1)", "f.status = 'ready'"]
            params: list = [query]
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
                    ) AS highlight
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
            total = await conn.fetchval(count_sql, *params[: n])

        return {
            "total": total,
            "results": [
                {
                    "filename": r["filename"],
                    "file_id": str(r["file_id"]),
                    "page_number": r["page_number"],
                    "section_title": r["section_title"],
                    "highlight": r["highlight"],
                    "relevance_score": round(float(r["relevance_score"]), 3),
                }
                for r in rows
            ],
        }

    async def search_cjk(
        self, query: str, filters: dict, limit: int, offset: int
    ) -> dict:
        from app.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = ["p.text ILIKE $1", "f.status = 'ready'"]
            params: list = [f"%{query}%"]
            _add_pg_filters(conditions, params, filters)
            where_clause = " AND ".join(conditions)
            n = len(params)
            # Add raw query for substring extraction ($n+1), then limit/offset
            search_params = list(params)
            search_params.insert(1, query)
            search_params.extend([limit, offset])
            count_params = params[:]
            count_params.extend([limit, offset])

            search_sql = f"""
                SELECT
                    f.filename, f.id AS file_id, p.page_number, p.section_title,
                    1.0 AS relevance_score,
                    substring(p.text FROM position($2 IN p.text) - 50 FOR 150) AS highlight
                FROM pages p
                JOIN files f ON f.id = p.file_id
                WHERE {where_clause}
                ORDER BY f.created_at DESC, p.page_number
                LIMIT ${n + 2} OFFSET ${n + 3}
            """
            count_sql = f"""
                SELECT COUNT(*) FROM pages p JOIN files f ON f.id = p.file_id
                WHERE {where_clause}
            """

            rows = await conn.fetch(search_sql, *search_params)
            total = await conn.fetchval(count_sql, *params)

        return {
            "total": total,
            "results": [
                {
                    "filename": r["filename"],
                    "file_id": str(r["file_id"]),
                    "page_number": r["page_number"],
                    "section_title": r["section_title"],
                    "highlight": r["highlight"] or "",
                    "relevance_score": 1.0,
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
        from app.database import get_pool
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
        from app.database import get_pool
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
        from app.database import get_pool
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
        from app.database import get_pool
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
        import uuid as _uuid
        from app.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO memories (id, content, memory_type, tags, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING id, content, memory_type, tags, metadata, created_at, updated_at
                """,
                _uuid.UUID(memory_id),
                content,
                memory_type,
                tags,
                json.dumps(metadata),
            )
        return _pg_memory_row(row)

    async def recall_memories(
        self,
        query: str,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
    ) -> dict:
        from app.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = [
                "to_tsvector('english', m.content) @@ plainto_tsquery('english', $1)"
            ]
            params: list = [query]
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
                SELECT m.id, m.content, m.memory_type, m.tags, m.metadata,
                       m.created_at, m.updated_at,
                       ts_rank_cd(to_tsvector('english', m.content),
                                  plainto_tsquery('english', $1)) AS fts_score,
                       (ts_rank_cd(to_tsvector('english', m.content),
                                   plainto_tsquery('english', $1))
                        * power(0.5, EXTRACT(EPOCH FROM (now() - m.created_at))
                                     / 86400.0 / 30.0)) AS score,
                       ts_headline('english', m.content,
                                   plainto_tsquery('english', $1),
                                   'MaxWords=30, MinWords=10') AS highlight
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
        from app.database import get_pool

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
        from app.database import get_pool

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
        from app.database import get_pool

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

def _pg_memory_row(row) -> dict:
    return {
        "memory_id": str(row["id"]),
        "content": row["content"],
        "memory_type": row["memory_type"],
        "tags": row["tags"],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "created_at": row["created_at"].isoformat() + "Z" if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() + "Z" if row["updated_at"] else None,
    }


def _add_pg_filters(conditions: list[str], params: list, filters: dict) -> None:
    if filters.get("tags"):
        params.append(filters["tags"] if isinstance(filters["tags"], list) else [filters["tags"]])
        conditions.append(f"f.tags @> ${len(params)}::text[]")

    if filters.get("mime_type"):
        params.append(filters["mime_type"])
        conditions.append(f"f.mime_type = ${len(params)}")

    if filters.get("metadata"):
        params.append(json.dumps(filters["metadata"]))
        conditions.append(f"f.metadata @> ${len(params)}::jsonb")

    if filters.get("created_after"):
        params.append(filters["created_after"])
        conditions.append(f"f.created_at >= ${len(params)}::timestamptz")


def _pg_file_row(row) -> dict:
    return {
        "id": str(row["id"]),
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "file_size": row["file_size"],
        "total_pages": row["total_pages"],
        "total_lines": row["total_lines"],
        "tags": row["tags"],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }
