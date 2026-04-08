"""Directory indexing and watch endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from opendb_core.services.index_service import index_directory
from opendb_core.services.watch_service import get_watch, list_watches, start_watch, stop_watch

# Ensure all parsers are registered
import opendb_core.parsers.text  # noqa: F401
import opendb_core.parsers.pdf  # noqa: F401
import opendb_core.parsers.docx  # noqa: F401
import opendb_core.parsers.pptx  # noqa: F401
import opendb_core.parsers.spreadsheet  # noqa: F401
import opendb_core.parsers.image  # noqa: F401

router = APIRouter(tags=["index"])


@router.post("/index")
async def index_directory_endpoint(
    path: str = Query(..., description="Local filesystem path to a directory"),
    tags: str | None = Query(None, description="JSON array of tags to apply to all files"),
    metadata: str | None = Query(None, description="JSON object of metadata to apply"),
    max_concurrent: int = Query(4, ge=1, le=16, description="Max concurrent ingestion workers"),
):
    """Recursively scan a local directory, ingest all supported files,
    and start watching for future changes."""
    dir_path = Path(path).resolve()

    if not dir_path.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {path}")
    if not dir_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {path}")

    parsed_tags = json.loads(tags) if tags else None
    parsed_metadata = json.loads(metadata) if metadata else None

    result = await index_directory(
        dir_path=dir_path,
        tags=parsed_tags,
        metadata=parsed_metadata,
        max_concurrent=max_concurrent,
    )

    # Start watching for future changes (previously embedded in index_directory)
    watch_id = start_watch(dir_path, tags=parsed_tags, metadata=parsed_metadata)
    result["watch_id"] = watch_id

    return result


# ---------------------------------------------------------------------------
# Watch management
# ---------------------------------------------------------------------------


@router.get("/watch")
async def list_watches_endpoint():
    """List all active directory watchers."""
    return {"watches": list_watches()}


@router.get("/watch/{watch_id}")
async def get_watch_endpoint(watch_id: str):
    """Get details of a single watcher."""
    info = get_watch(watch_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Watch not found")
    return info


@router.delete("/watch/{watch_id}")
async def stop_watch_endpoint(watch_id: str):
    """Stop a directory watcher."""
    removed = stop_watch(watch_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Watch not found")
    return {"status": "stopped", "id": watch_id}
