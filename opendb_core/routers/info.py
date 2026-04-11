"""Info endpoint: GET /info — workspace statistics + active workspace identity."""

from __future__ import annotations

from fastapi import APIRouter

from opendb_core.services import workspace_service
from opendb_core.storage import get_backend

router = APIRouter(tags=["info"])


@router.get("/info")
async def info() -> dict:
    """Return workspace statistics plus the active workspace identity."""
    backend = get_backend()
    stats = await backend.get_workspace_stats()
    active = await workspace_service.current_workspace()
    return {"workspace": active, **stats}
