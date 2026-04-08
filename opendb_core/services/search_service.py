"""Search service: full-text search with filters (unified CJK + Latin via jieba)."""

from __future__ import annotations

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
    return await backend.search_fts(query, filters, limit, offset)
