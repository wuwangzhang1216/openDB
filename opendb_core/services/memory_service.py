"""Memory service: store, recall, and forget agent memories."""

from __future__ import annotations

import uuid

from opendb_core.storage import get_backend

VALID_MEMORY_TYPES = {"episodic", "semantic", "procedural"}


async def store_memory(
    content: str,
    memory_type: str = "semantic",
    tags: list[str] | None = None,
    metadata: dict | None = None,
    pinned: bool = False,
) -> dict:
    """Store a new memory entry."""
    if memory_type not in VALID_MEMORY_TYPES:
        raise ValueError(
            f"Invalid memory_type '{memory_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_MEMORY_TYPES))}"
        )
    if not content or not content.strip():
        raise ValueError("Memory content cannot be empty")

    backend = get_backend()
    return await backend.store_memory(
        memory_id=str(uuid.uuid4()),
        content=content.strip(),
        memory_type=memory_type,
        tags=tags or [],
        metadata=metadata or {},
        pinned=pinned,
    )


async def recall_memories(
    query: str,
    memory_type: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
    offset: int = 0,
    pinned_only: bool = False,
) -> dict:
    """Search memories with FTS + time-decay scoring.

    If *pinned_only* is True, skip FTS and return all pinned memories.
    """
    if memory_type and memory_type not in VALID_MEMORY_TYPES:
        raise ValueError(f"Invalid memory_type filter: '{memory_type}'")

    backend = get_backend()
    return await backend.recall_memories(
        query=query,
        memory_type=memory_type,
        tags=tags,
        limit=limit,
        offset=offset,
        pinned_only=pinned_only,
    )


async def forget_memory(
    memory_id: str | None = None,
    query: str | None = None,
    memory_type: str | None = None,
) -> dict:
    """Delete memories by ID or by query match.

    If *memory_id* is given, delete that single memory.
    Otherwise, recall matching memories by *query* and delete them.
    """
    backend = get_backend()

    if memory_id:
        deleted = await backend.delete_memory(memory_id)
        return {"deleted": 1 if deleted else 0, "by": "id"}

    if query:
        # Find matching memories then delete them
        results = await backend.recall_memories(
            query=query, memory_type=memory_type, tags=None, limit=100, offset=0,
        )
        count = 0
        for r in results.get("results", []):
            if await backend.delete_memory(r["memory_id"]):
                count += 1
        return {"deleted": count, "by": "query"}

    raise ValueError("Either memory_id or query must be provided")


async def list_memories(
    memory_type: str | None = None,
    tags: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """List memories with optional type/tag filters."""
    backend = get_backend()
    return await backend.list_memories(
        memory_type=memory_type,
        tags=tags,
        limit=limit,
        offset=offset,
    )
