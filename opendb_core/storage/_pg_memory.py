"""PostgreSQL memory operations mixin.

Extracted from postgres.py to keep file sizes manageable.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class PgMemoryMixin:
    """Agent Memory operations for PostgresBackend.

    All methods acquire their own connection from the pool via
    ``opendb_core.database.get_pool()``.
    """

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
        from opendb_core.storage.shared import pg_memory_row as _pg_memory_row

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
        from opendb_core.storage.shared import pg_memory_row as _pg_memory_row

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
        from opendb_core.storage.shared import pg_memory_row as _pg_memory_row

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
        from opendb_core.storage.shared import pg_memory_row as _pg_memory_row

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
