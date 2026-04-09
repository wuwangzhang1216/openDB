"""SQLite memory operations mixin.

Extracted from sqlite.py to keep file sizes manageable.
"""

from __future__ import annotations

import aiosqlite
import json
import logging
import re

from opendb_core.storage.shared import (
    build_highlight,
    compute_temporal_score,
    content_token_set,
    escape_fts5,
    has_recency_intent,
    jaccard_similarity,
)

logger = logging.getLogger(__name__)


class SQLiteMemoryMixin:
    """Agent Memory operations for SQLiteBackend.

    Expects ``self._db`` (aiosqlite connection) and ``self._write_lock``
    (asyncio.Lock) to be set by the host class.
    """

    # ------------------------------------------------------------------
    # Conflict detection for knowledge updates
    # ------------------------------------------------------------------

    # Phrases that signal the content is an update to a previous fact.
    _UPDATE_SIGNALS = re.compile(
        r"\b(moved to|changed|switched|updated|no longer|"
        r"instead of|replaced|now (?:use|live|work|prefer)|"
        r"grew to|new role|started a new|"
        r"changed it from|switched to|migrated)\b",
        re.IGNORECASE,
    )

    async def _find_conflicting_memory(
        self,
        content: str,
        memory_type: str,
        threshold: float = 0.4,
    ) -> int | None:
        """Find an existing memory that overlaps significantly with *content*.

        Returns the integer rowid (``memories.id``) of the best match whose
        Jaccard token similarity >= *threshold*, or ``None``.

        When the new content contains update-signal phrases (e.g. "moved to",
        "switched to"), the threshold is lowered to catch updates where the
        old and new values share few tokens (e.g. address changes).
        """
        from opendb_core.utils.tokenizer import tokenize_for_fts

        new_tokens = content_token_set(content)
        if len(new_tokens) < 2:
            return None

        # Lower threshold when the content explicitly signals an update
        effective_threshold = threshold
        if self._UPDATE_SIGNALS.search(content):
            effective_threshold = min(threshold, 0.15)

        fts_query = escape_fts5(tokenize_for_fts(content), use_or=True)
        if not fts_query.strip():
            return None

        sql = """
            SELECT m.id, m.content
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.id
            WHERE memories_fts MATCH ? AND m.memory_type = ?
            LIMIT 10
        """
        try:
            async with self._db.execute(sql, (fts_query, memory_type)) as cur:
                rows = await cur.fetchall()
        except aiosqlite.DatabaseError:
            rows = []

        best_id: int | None = None
        best_sim = 0.0
        for r in rows:
            old_tokens = content_token_set(r["content"])
            sim = jaccard_similarity(new_tokens, old_tokens)
            if sim >= effective_threshold and sim > best_sim:
                best_sim = sim
                best_id = r["id"]

        # Fallback for update-signal content with zero FTS overlap:
        # find the most recent same-type memory and check if the new
        # content looks like a replacement (e.g. address change).
        if best_id is None and self._UPDATE_SIGNALS.search(content):
            fallback_sql = """
                SELECT m.id, m.content
                FROM memories m
                WHERE m.memory_type = ?
                ORDER BY m.updated_at DESC
                LIMIT 5
            """
            try:
                async with self._db.execute(fallback_sql, (memory_type,)) as cur:
                    fallback_rows = await cur.fetchall()
            except aiosqlite.DatabaseError:
                fallback_rows = []

            for r in fallback_rows:
                old_tokens = content_token_set(r["content"])
                sim = jaccard_similarity(new_tokens, old_tokens)
                # Even very low overlap counts when update signal is present
                if sim >= 0.05 and sim > best_sim:
                    best_sim = sim
                    best_id = r["id"]

        return best_id

    # ------------------------------------------------------------------
    # Store
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
        from opendb_core.utils.tokenizer import tokenize_for_fts

        async with self._write_lock:
            # Check for conflicting existing memory (knowledge-update detection).
            # Skip for episodic memories — they are event records that should
            # never overwrite each other.
            conflict_id = None
            if memory_type != "episodic":
                conflict_id = await self._find_conflicting_memory(
                    content, memory_type, threshold=0.3,
                )

            await self._db.execute("BEGIN")
            try:
                if conflict_id is not None:
                    # Supersede: update existing memory instead of inserting duplicate
                    await self._db.execute(
                        "UPDATE memories SET content = ?, memory_id = ?, tags = ?, "
                        "metadata = ?, pinned = ?, "
                        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                        "WHERE id = ?",
                        (content, memory_id, json.dumps(tags), json.dumps(metadata),
                         int(pinned), conflict_id),
                    )
                    await self._db.execute(
                        "UPDATE memories_fts SET content = ? WHERE rowid = ?",
                        (tokenize_for_fts(content), conflict_id),
                    )
                else:
                    # Normal insert
                    await self._db.execute(
                        """
                        INSERT INTO memories (memory_id, content, memory_type, pinned, tags, metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (memory_id, content, memory_type, int(pinned),
                         json.dumps(tags), json.dumps(metadata)),
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
                   julianday('now') - julianday(m.updated_at) AS age_days
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

        # Time-decay re-ranking using metadata event dates when available.
        # Pinned memories get a 10x boost so they surface first.
        from opendb_core.config import settings
        halflife = settings.memory_decay_halflife_days
        recency = has_recency_intent(query)

        scored = []
        for r in rows:
            fts_score = abs(float(r["fts_rank"]))
            db_age = float(r["age_days"]) if r["age_days"] else 0.0
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            score, eff_age = compute_temporal_score(
                fts_score, db_age, meta, halflife,
                pinned=bool(r["pinned"]), recency_intent=recency,
            )
            scored.append({
                "memory_id": r["memory_id"],
                "content": r["content"],
                "memory_type": r["memory_type"],
                "pinned": bool(r["pinned"]),
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "metadata": meta,
                "highlight": build_highlight(r["content"], query),
                "score": score,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "_age_days": eff_age,
            })

        # Recency tiebreaker: when FTS scores cluster, boost newer memories
        if len(scored) >= 2:
            max_score = max(s["score"] for s in scored)
            if max_score > 0:
                for s in scored:
                    if s["score"] / max_score > 0.7:
                        age = s["_age_days"]
                        recency_bonus = 1.0 + 0.3 * (0.5 ** (age / 1.0))
                        s["score"] = s["score"] * recency_bonus

        scored.sort(key=lambda x: x["score"], reverse=True)
        # Strip internal field and round final scores before returning
        for s in scored:
            s.pop("_age_days", None)
            s["score"] = round(s["score"], 4)
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
