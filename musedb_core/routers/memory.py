"""Memory endpoints: store, recall, forget, and list agent memories."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from musedb_core.services.memory_service import (
    forget_memory,
    list_memories,
    recall_memories,
    store_memory,
)

router = APIRouter(tags=["memory"])


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------

class MemoryStoreRequest(BaseModel):
    content: str
    memory_type: str = "semantic"
    tags: list[str] = []
    metadata: dict = {}


class MemoryRecallRequest(BaseModel):
    query: str
    memory_type: str | None = None
    tags: list[str] | None = None
    limit: int = 10
    offset: int = 0


class MemoryForgetRequest(BaseModel):
    memory_id: str | None = None
    query: str | None = None
    memory_type: str | None = None


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/memory")
async def store(request: MemoryStoreRequest):
    """Store a new memory entry."""
    return await store_memory(
        content=request.content,
        memory_type=request.memory_type,
        tags=request.tags,
        metadata=request.metadata,
    )


@router.post("/memory/recall")
async def recall(request: MemoryRecallRequest):
    """Search and recall stored memories."""
    return await recall_memories(
        query=request.query,
        memory_type=request.memory_type,
        tags=request.tags,
        limit=request.limit,
        offset=request.offset,
    )


@router.post("/memory/forget")
async def forget(request: MemoryForgetRequest):
    """Delete memories by ID or by search query."""
    return await forget_memory(
        memory_id=request.memory_id,
        query=request.query,
        memory_type=request.memory_type,
    )


@router.get("/memory")
async def list_all(
    memory_type: str | None = None,
    tags: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    """List memories with optional filters."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    return await list_memories(
        memory_type=memory_type,
        tags=tag_list,
        limit=limit,
        offset=offset,
    )
