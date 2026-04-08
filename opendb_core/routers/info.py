"""Info endpoint: GET /info — workspace statistics."""

from __future__ import annotations

from fastapi import APIRouter

from opendb_core.storage import get_backend

router = APIRouter(tags=["info"])


@router.get("/info")
async def info():
    """Return workspace statistics: file counts by status/type, recent files."""
    backend = get_backend()
    return await backend.get_workspace_stats()
