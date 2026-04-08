"""File management endpoints: upload, list, delete."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import UUID

import magic
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from opendb_core.config import settings
from opendb_core.services.ingest_service import ingest_file
from opendb_core.storage import get_backend

# Ensure all parsers are registered
import opendb_core.parsers.text  # noqa: F401
import opendb_core.parsers.pdf  # noqa: F401
import opendb_core.parsers.docx  # noqa: F401
import opendb_core.parsers.pptx  # noqa: F401
import opendb_core.parsers.spreadsheet  # noqa: F401
import opendb_core.parsers.image  # noqa: F401

router = APIRouter(prefix="/files", tags=["files"])


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    tags: str | None = Form(None),
    metadata: str | None = Form(None),
):
    """Upload and ingest a file."""
    content = await file.read()

    if len(content) > settings.max_file_size:
        raise HTTPException(status_code=413, detail="File too large")

    mime_type = magic.from_buffer(content[:2048], mime=True)

    parsed_tags = json.loads(tags) if tags else []
    parsed_metadata = json.loads(metadata) if metadata else {}

    try:
        result = await ingest_file(
            file_content=content,
            original_filename=file.filename or "unnamed",
            mime_type=mime_type,
            tags=parsed_tags,
            metadata=parsed_metadata,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_files(
    tags: str | None = Query(None, description="Filter by tag"),
    mime_type: str | None = Query(None, description="Filter by MIME type"),
    filename: str | None = Query(None, description="Fuzzy filename search"),
    sort: str = Query("created_at:desc", description="Sort field:direction"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List files with optional filters."""
    allowed_sorts = {"created_at", "filename", "file_size"}
    sort_parts = sort.split(":")
    sort_field = sort_parts[0] if sort_parts[0] in allowed_sorts else "created_at"
    sort_dir = "ASC" if len(sort_parts) > 1 and sort_parts[1].lower() == "asc" else "DESC"

    filters = {}
    if tags:
        filters["tags"] = tags
    if mime_type:
        filters["mime_type"] = mime_type
    if filename:
        filters["filename"] = filename

    backend = get_backend()
    return await backend.list_files(filters, sort_field, sort_dir, limit, offset)


@router.get("/{file_id}")
async def get_file(file_id: UUID):
    """Get details of a single file."""
    backend = get_backend()
    record = await backend.get_file_by_id(str(file_id))
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    return record


@router.delete("/{file_id}")
async def delete_file(file_id: UUID):
    """Delete a file and all associated data."""
    backend = get_backend()
    file_path_str = await backend.delete_file(str(file_id))
    if file_path_str is None:
        raise HTTPException(status_code=404, detail="File not found")

    file_dir = Path(file_path_str).parent
    if file_dir.exists():
        shutil.rmtree(file_dir, ignore_errors=True)

    return {"status": "deleted", "id": str(file_id)}
