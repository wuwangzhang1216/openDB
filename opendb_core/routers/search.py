"""Search endpoint: POST /search — full-text search and grep."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from opendb_core.services.grep_service import grep_files
from opendb_core.services.search_service import search_files

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    mode: str = "fts"  # "fts" (full-text search) | "grep" (regex file search) | "auto"
    filters: dict | None = None
    limit: int = 20
    offset: int = 0
    # Grep-specific fields
    path: str | None = None
    glob: str | None = None
    case_insensitive: bool = False
    context: int = 0
    max_results: int = 100


class SearchResultItem(BaseModel):
    filename: str | None = None
    file_id: str | None = None
    page_number: int | None = None
    section_title: str | None = None
    highlight: str | None = None
    relevance_score: float | None = None
    updated_at: str | None = None
    # Grep-specific fields
    file: str | None = None
    line: int | None = None
    text: str | None = None
    context_before: list[str] | None = None
    context_after: list[str] | None = None


class SearchResponse(BaseModel):
    total: int
    results: list[SearchResultItem | dict]
    truncated: bool | None = None
    error: str | None = None


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse | dict:
    """Search across files. Supports full-text search (fts) and regex grep modes."""
    mode = request.mode

    # Auto-detect mode
    if mode == "auto":
        mode = "grep" if (request.path or request.glob) else "fts"

    if mode == "grep":
        if not request.path:
            return SearchResponse(
                total=0, results=[],
                error="'path' is required for grep mode",
            )
        return await grep_files(
            query=request.query,
            path=request.path,
            glob=request.glob,
            case_insensitive=request.case_insensitive,
            context=request.context,
            max_results=request.max_results,
        )

    # Default: full-text search
    return await search_files(
        query=request.query,
        filters=request.filters,
        limit=request.limit,
        offset=request.offset,
    )
