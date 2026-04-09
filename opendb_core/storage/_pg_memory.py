"""PostgreSQL memory operations mixin.

Extracted from postgres.py to keep file sizes manageable.
"""

from __future__ import annotations

import json
import logging

from opendb_core.storage.shared import (
    compute_temporal_score,
    content_token_set,
    has_recency_intent,
    jaccard_similarity,
)

logger = logging.getLogger(__name__)


class PgMemoryMixin:
    """Agent Memory operations for PostgresBackend.

    All methods acquire their own connection from the pool via
    ``opendb_core.database.get_pool()``.
    """

    async def _find_conflicting_memory_pg(
        self,
        conn,
        content: str,
        memory_type: str,
        threshold: float = 0.3,
    ) -> str | None:
        """Find an existing PG memory that overlaps significantly.

        Returns the UUID string of the best match, or None.
        """
        new_tokens = content_token_set(content)
        if len(new_tokens) < 2:
            return None

        rows = await conn.fetch(
            "SELECT id, content FROM memories "
            "WHERE memory_type = $1 "
            "ORDER BY updated_at DESC LIMIT 20",
            memory_type,
        )

        best_id: str | None = None
        best_sim = 0.0
        for r in rows:
            old_tokens = content_token_set(r["content"])
            sim = jaccard_similarity(new_tokens, old_tokens)
            if sim >= threshold and sim > best_sim:
                best_sim = sim
                best_id = str(r["id"])
        return best_id

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
            # Skip conflict detection for episodic memories — they are event
            # records that should never overwrite each other.
            conflict_id = None
            if memory_type != "episodic":
                conflict_id = await self._find_conflicting_memory_pg(
                    conn, content, memory_type,
                )

            if conflict_id:
                row = await conn.fetchrow(
                    """
                    UPDATE memories
                    SET content = $1, pinned = $2, tags = $3, metadata = $4::jsonb,
                        content_jieba = $5, updated_at = now()
                    WHERE id = $6
                    RETURNING id, content, memory_type, pinned, tags, metadata,
                              created_at, updated_at
                    """,
                    content, pinned, tags, json.dumps(metadata),
                    tokenize_for_fts(content), _uuid.UUID(conflict_id),
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO memories (id, content, memory_type, pinned, tags, metadata,
                                          content_jieba)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    RETURNING id, content, memory_type, pinned, tags, metadata,
                              created_at, updated_at
                    """,
                    _uuid.UUID(memory_id), content, memory_type, pinned, tags,
                    json.dumps(metadata), tokenize_for_fts(content),
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

            # Fetch extra rows for Python-side temporal re-ranking
            fetch_limit = max(limit * 3, 60)

            search_sql = f"""
                SELECT m.id, m.content, m.memory_type, m.pinned, m.tags, m.metadata,
                       m.created_at, m.updated_at,
                       {rank_expr} AS fts_score,
                       EXTRACT(EPOCH FROM (now() - m.updated_at)) / 86400.0 AS age_days,
                       {headline_expr} AS highlight
                FROM memories m
                WHERE {where_clause}
                ORDER BY {rank_expr} DESC
                LIMIT ${n + 1}
            """
            count_sql = f"""
                SELECT COUNT(*) FROM memories m WHERE {where_clause}
            """

            rows = await conn.fetch(search_sql, *params, fetch_limit)
            total = await conn.fetchval(count_sql, *params)

        recency = has_recency_intent(query)
        scored = []
        for r in rows:
            fts_score = float(r["fts_score"]) if r["fts_score"] else 0.0
            db_age = float(r["age_days"]) if r["age_days"] else 0.0
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            score, eff_age = compute_temporal_score(
                fts_score, db_age, meta, halflife,
                pinned=bool(r.get("pinned", False)), recency_intent=recency,
            )
            scored.append({
                "memory_id": str(r["id"]),
                "content": r["content"],
                "memory_type": r["memory_type"],
                "pinned": bool(r.get("pinned", False)),
                "tags": r["tags"],
                "metadata": meta,
                "highlight": r["highlight"] or "",
                "score": score,
                "created_at": r["created_at"].isoformat() + "Z" if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() + "Z" if r["updated_at"] else None,
                "_age_days": eff_age,
            })

        # Recency tiebreaker
        if len(scored) >= 2:
            max_score = max(s["score"] for s in scored)
            if max_score > 0:
                for s in scored:
                    if s["score"] / max_score > 0.7:
                        recency_bonus = 1.0 + 0.3 * (0.5 ** (s["_age_days"] / 1.0))
                        s["score"] = s["score"] * recency_bonus

        scored.sort(key=lambda x: x["score"], reverse=True)
        for s in scored:
            s.pop("_age_days", None)
            s["score"] = round(s["score"], 4)
        results = scored[offset : offset + limit]
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
