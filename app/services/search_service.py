"""Search service: full-text search with filters and CJK fallback."""

from __future__ import annotations

import json
import re

from app.database import get_pool

# CJK character ranges
_CJK_PATTERN = re.compile(
    r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]"
)


def _is_cjk_query(query: str) -> bool:
    return bool(_CJK_PATTERN.search(query))


async def search_files(
    query: str,
    filters: dict | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Search across all files. Returns page-level results with highlights."""
    filters = filters or {}
    is_cjk = _is_cjk_query(query)

    pool = await get_pool()
    async with pool.acquire() as conn:
        if is_cjk:
            return await _search_cjk(conn, query, filters, limit, offset)
        else:
            return await _search_fts(conn, query, filters, limit, offset)


async def _search_fts(
    conn, query: str, filters: dict, limit: int, offset: int
) -> dict:
    """Full-text search using tsvector."""
    conditions = ["p.tsv @@ plainto_tsquery('english', $1)", "f.status = 'ready'"]
    params: list = [query]
    param_idx = 1

    _add_filters(conditions, params, filters, param_idx)
    param_idx = len(params)

    where_clause = " AND ".join(conditions)

    param_idx += 1
    limit_idx = param_idx
    param_idx += 1
    offset_idx = param_idx
    params.extend([limit, offset])

    search_query = f"""
        SELECT
            f.filename,
            f.id AS file_id,
            p.page_number,
            p.section_title,
            ts_rank_cd(p.tsv, plainto_tsquery('english', $1)) AS relevance_score,
            ts_headline('english', p.text, plainto_tsquery('english', $1),
                'StartSel=<mark>, StopSel=</mark>, MaxWords=35, MinWords=15'
            ) AS highlight
        FROM pages p
        JOIN files f ON f.id = p.file_id
        WHERE {where_clause}
        ORDER BY relevance_score DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """

    count_query = f"""
        SELECT COUNT(*)
        FROM pages p
        JOIN files f ON f.id = p.file_id
        WHERE {where_clause}
    """

    rows = await conn.fetch(search_query, *params)
    total = await conn.fetchval(count_query, *params[: len(params) - 2])

    return {
        "total": total,
        "results": [
            {
                "filename": row["filename"],
                "file_id": str(row["file_id"]),
                "page_number": row["page_number"],
                "section_title": row["section_title"],
                "highlight": row["highlight"],
                "relevance_score": round(float(row["relevance_score"]), 3),
            }
            for row in rows
        ],
    }


async def _search_cjk(
    conn, query: str, filters: dict, limit: int, offset: int
) -> dict:
    """CJK search using trigram ILIKE fallback."""
    conditions = ["p.text ILIKE $1", "f.status = 'ready'"]
    params: list = [f"%{query}%"]
    param_idx = 1

    _add_filters(conditions, params, filters, param_idx)
    param_idx = len(params)

    where_clause = " AND ".join(conditions)

    param_idx += 1
    limit_idx = param_idx
    param_idx += 1
    offset_idx = param_idx
    params.extend([limit, offset])

    search_query = f"""
        SELECT
            f.filename,
            f.id AS file_id,
            p.page_number,
            p.section_title,
            1.0 AS relevance_score,
            substring(p.text FROM position($2 IN p.text) - 50 FOR 150) AS highlight
        FROM pages p
        JOIN files f ON f.id = p.file_id
        WHERE {where_clause}
        ORDER BY f.created_at DESC, p.page_number
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """

    count_query = f"""
        SELECT COUNT(*)
        FROM pages p
        JOIN files f ON f.id = p.file_id
        WHERE {where_clause}
    """

    # CJK needs the raw query for substring extraction
    params_with_raw = params[:param_idx - 2]  # before limit/offset
    search_params = list(params_with_raw)
    search_params.insert(1, query)  # $2 for raw query in substring
    search_params.extend([limit, offset])

    rows = await conn.fetch(search_query, *search_params)
    total = await conn.fetchval(count_query, *params[: len(params) - 2])

    return {
        "total": total,
        "results": [
            {
                "filename": row["filename"],
                "file_id": str(row["file_id"]),
                "page_number": row["page_number"],
                "section_title": row["section_title"],
                "highlight": row["highlight"] or "",
                "relevance_score": 1.0,
            }
            for row in rows
        ],
    }


def _add_filters(
    conditions: list[str], params: list, filters: dict, start_idx: int
) -> None:
    """Add filter conditions to the query."""
    idx = start_idx

    if "tags" in filters and filters["tags"]:
        idx += 1
        conditions.append(f"f.tags @> ${idx}::text[]")
        tags = filters["tags"]
        if isinstance(tags, str):
            tags = [tags]
        params.append(tags)

    if "mime_type" in filters and filters["mime_type"]:
        idx += 1
        conditions.append(f"f.mime_type = ${idx}")
        params.append(filters["mime_type"])

    if "metadata" in filters and filters["metadata"]:
        idx += 1
        conditions.append(f"f.metadata @> ${idx}::jsonb")
        params.append(json.dumps(filters["metadata"]))

    if "created_after" in filters and filters["created_after"]:
        idx += 1
        conditions.append(f"f.created_at >= ${idx}::timestamptz")
        params.append(filters["created_after"])
