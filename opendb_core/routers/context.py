"""Context endpoint: compact agent-oriented search bundle."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from opendb_core.services.context_service import build_context

router = APIRouter(tags=["context"])


class ContextRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(8, ge=1, le=20)
    include_snippets: bool = True


@router.post("/context")
async def context(request: ContextRequest) -> dict:
    """Build compact code/document context for an agent task or symbol lookup."""
    return await build_context(
        query=request.query,
        limit=request.limit,
        include_snippets=request.include_snippets,
    )
