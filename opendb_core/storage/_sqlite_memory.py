"""SQLite memory operations mixin.

Extracted from sqlite.py to keep file sizes manageable.
"""

from __future__ import annotations

import aiosqlite
import json
import logging

from opendb_core.storage.shared import build_highlight, escape_fts5

logger = logging.getLogger(__name__)


class SQLiteMemoryMixin:
    """Agent Memory operations for SQLiteBackend.

    Expects ``self._db`` (aiosqlite connection) and ``self._write_lock``
    (asyncio.Lock) to be set by the host class.
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
        from opendb_core.utils.tokenizer import tokenize_for_fts

        async with self._write_lock:
            await self._db.execute("BEGIN")
            try:
                await self._db.execute(
                    """
                    INSERT INTO memories (memory_id, content, memory_type, pinned, tags, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (memory_id, content, memory_type, int(pinned), json.dumps(tags), json.dumps(metadata)),
                )
                # Get the autoincrement rowid for FTS
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
            except aiosqlite.DatabaseError:
                await self._db.execute("ROLLBACK")
                raise

        # Return the stored record
        return await self.get_memory(memory_id)  # type: ignore[return-value]

    async def recall_memories(
        self,
        query: str,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
        pinned_only: bool = False,
    ) -> dict:
        # Fast path: return all pinned memories without FTS search
        if pinned_only:
            return await self._list_pinned(memory_type, tags, limit, offset)

        from opendb_core.utils.tokenizer import tokenize_for_fts

        fts_query = escape_fts5(tokenize_for_fts(query), use_or=True)

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

        # Fetch extra rows for Python-side time-decay re-ranking
        fetch_limit = max(limit * 3, 60)

        search_sql = f"""
            SELECT m.memory_id, m.content, m.memory_type, m.pinned,
                   m.tags, m.metadata, m.created_at, m.updated_at,
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

        # Time-decay re-ranking: score = fts_relevance * 0.5^(age_days/halflife)
        # Pinned memories get a 10x boost so they surface first.
        from opendb_core.config import settings
        halflife = settings.memory_decay_halflife_days

        scored = []
        for r in rows:
            fts_score = abs(float(r["fts_rank"]))
            age_days = float(r["age_days"]) if r["age_days"] else 0.0
            decay = 0.5 ** (age_days / halflife)
            pin_boost = 10.0 if r["pinned"] else 1.0
            score = round(fts_score * decay * pin_boost, 4)
            scored.append({
                "memory_id": r["memory_id"],
                "content": r["content"],
                "memory_type": r["memory_type"],
                "pinned": bool(r["pinned"]),
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "highlight": build_highlight(r["content"], query),
                "score": score,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        results = scored[offset : offset + limit]
        return {"total": total, "results": results}

    async def _list_pinned(
        self,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
    ) -> dict:
        """Return all pinned memories (no FTS search needed)."""
        conditions = ["m.pinned = 1"]
        params: list = []
        if memory_type:
            conditions.append("m.memory_type = ?")
            params.append(memory_type)
        if tags:
            for tag in tags:
                conditions.append("m.tags LIKE ?")
                params.append(f'%"{tag}"%')

        where = " AND ".join(conditions)
        sql = f"""
            SELECT m.memory_id, m.content, m.memory_type, m.pinned,
                   m.tags, m.metadata, m.created_at, m.updated_at
            FROM memories m
            WHERE {where}
            ORDER BY m.created_at DESC
            LIMIT ? OFFSET ?
        """
        count_sql = f"SELECT COUNT(*) FROM memories m WHERE {where}"

        async with self._db.execute(sql, [*params, limit, offset]) as cur:
            rows = await cur.fetchall()
        async with self._db.execute(count_sql, params) as cur:
            total = (await cur.fetchone())[0]

        results = []
        for r in rows:
            results.append({
                "memory_id": r["memory_id"],
                "content": r["content"],
                "memory_type": r["memory_type"],
                "pinned": True,
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "score": 1.0,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return {"total": total, "results": results}

    async def get_memory(self, memory_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT memory_id, content, memory_type, pinned, tags, metadata, "
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
            "pinned": bool(row["pinned"]),
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
            SELECT memory_id, content, memory_type, pinned, tags, metadata,
                   created_at, updated_at
            FROM memories
            {where_clause}
            ORDER BY pinned DESC, created_at DESC
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
                "pinned": bool(r["pinned"]),
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return {"total": total, "memories": memories}
