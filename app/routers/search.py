"""Search endpoint: POST /search"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.search_service import search_files

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    filters: dict | None = None
    limit: int = 20
    offset: int = 0


class SearchResultItem(BaseModel):
    filename: str
    file_id: str
    page_number: int
    section_title: str | None
    highlight: str
    relevance_score: float


class SearchResponse(BaseModel):
    total: int
    results: list[SearchResultItem]


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Full-text search across all files."""
    return await search_files(
        query=request.query,
        filters=request.filters,
        limit=request.limit,
        offset=request.offset,
    )
