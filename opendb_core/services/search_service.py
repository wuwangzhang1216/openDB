"""Search service: full-text search with filters (unified CJK + Latin via jieba)."""

from __future__ import annotations

import time

from opendb_core.storage import get_backend


async def search_files(
    query: str,
    filters: dict | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Search across all files. Returns page-level results with highlights.

    CJK queries are tokenized via jieba inside the backend's search_fts(),
    so all languages go through the same FTS5 code path.
    """
    filters = filters or {}
    backend = get_backend()
    start = time.perf_counter()
    result = await backend.search_fts(query, filters, limit, offset)

    from opendb_core.services.eval_service import capture_eval, now_ms

    await capture_eval(
        tool_name="search",
        query=query,
        result_ids=[str(r.get("file_id")) for r in result.get("results", []) if r.get("file_id")],
        result_count=int(result.get("total", 0)),
        latency_ms=now_ms(start),
        metadata={
            "limit": limit,
            "offset": offset,
            "filters": filters,
        },
    )
    return result
