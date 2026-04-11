"""Workspace management REST endpoints.

::

    GET    /workspaces              → {active_id, workspaces: [...]}
    POST   /workspaces               body {root, name?, switch?}  → add (+optional switch)
    GET    /workspaces/active       → active workspace entry
    PUT    /workspaces/active        body {id} or {root}  → switch
    DELETE /workspaces/{id}         → unregister
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from opendb_core.services import workspace_service
from opendb_core.services.workspace_service import (
    WorkspaceNotFound,
    WorkspaceRootMissing,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AddWorkspaceRequest(BaseModel):
    root: str = Field(..., description="Absolute path to the workspace root")
    name: str | None = Field(None, description="Friendly name (defaults to directory basename)")
    switch: bool = Field(False, description="Also make this the active workspace")


class SwitchWorkspaceRequest(BaseModel):
    id: str | None = Field(None, description="Workspace id")
    root: str | None = Field(None, description="Workspace root path")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_workspaces() -> dict:
    """List all registered workspaces."""
    return await workspace_service.list_workspaces()


@router.post("")
async def add_workspace(body: AddWorkspaceRequest) -> dict:
    """Register a new workspace (creates ``.opendb/`` if missing)."""
    try:
        return await workspace_service.add_workspace(
            root=body.root,
            name=body.name,
            switch=body.switch,
        )
    except WorkspaceRootMissing as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/active")
async def get_active_workspace() -> dict:
    """Return the currently active workspace."""
    active = await workspace_service.current_workspace()
    if active is None:
        raise HTTPException(status_code=404, detail="No active workspace")
    return active


@router.put("/active")
async def switch_active_workspace(body: SwitchWorkspaceRequest) -> dict:
    """Switch the active workspace by id or root path."""
    target = body.id or body.root
    if not target:
        raise HTTPException(
            status_code=400, detail="Must provide either 'id' or 'root'"
        )
    try:
        return await workspace_service.switch_workspace(target)
    except WorkspaceNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/{workspace_id}")
async def delete_workspace(workspace_id: str, force: bool = False) -> dict:
    """Unregister a workspace. Does not delete files on disk."""
    try:
        return await workspace_service.remove_workspace(workspace_id, force=force)
    except WorkspaceNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
