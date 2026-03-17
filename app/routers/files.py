"""File management endpoints: upload, list, delete."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import UUID

import magic
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.config import settings
from app.database import get_pool
from app.services.ingest_service import ingest_file

# Ensure all parsers are registered
import app.parsers.text  # noqa: F401
import app.parsers.pdf  # noqa: F401
import app.parsers.docx  # noqa: F401
import app.parsers.pptx  # noqa: F401
import app.parsers.spreadsheet  # noqa: F401
import app.parsers.image  # noqa: F401

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

    # Detect MIME type
    mime_type = magic.from_buffer(content[:2048], mime=True)

    # Parse optional form fields
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
    pool = await get_pool()

    # Build dynamic query
    conditions = ["f.status = 'ready'"]
    params: list = []
    param_idx = 0

    if tags:
        param_idx += 1
        conditions.append(f"f.tags @> ${param_idx}::text[]")
        params.append([tags])

    if mime_type:
        param_idx += 1
        conditions.append(f"f.mime_type = ${param_idx}")
        params.append(mime_type)

    if filename:
        param_idx += 1
        conditions.append(f"f.filename % ${param_idx}")
        params.append(filename)

    where_clause = " AND ".join(conditions)

    # Parse sort
    allowed_sorts = {"created_at", "filename", "file_size"}
    sort_parts = sort.split(":")
    sort_field = sort_parts[0] if sort_parts[0] in allowed_sorts else "created_at"
    sort_dir = "ASC" if len(sort_parts) > 1 and sort_parts[1].lower() == "asc" else "DESC"

    param_idx += 1
    limit_param = param_idx
    param_idx += 1
    offset_param = param_idx
    params.extend([limit, offset])

    query = f"""
        SELECT f.id, f.filename, f.mime_type, f.file_size,
               f.tags, f.metadata, f.created_at, f.updated_at, f.status,
               ft.total_lines,
               (SELECT COUNT(*) FROM pages p WHERE p.file_id = f.id) AS total_pages
        FROM files f
        LEFT JOIN file_text ft ON ft.file_id = f.id
        WHERE {where_clause}
        ORDER BY f.{sort_field} {sort_dir}
        LIMIT ${limit_param} OFFSET ${offset_param}
    """

    count_query = f"""
        SELECT COUNT(*) FROM files f WHERE {where_clause}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        total = await conn.fetchval(count_query, *params[: param_idx - 2])

    files = []
    for row in rows:
        files.append(
            {
                "id": str(row["id"]),
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "file_size": row["file_size"],
                "total_pages": row["total_pages"],
                "total_lines": row["total_lines"],
                "tags": row["tags"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "status": row["status"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
        )

    return {"total": total, "files": files}


@router.get("/{file_id}")
async def get_file(file_id: UUID):
    """Get details of a single file."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT f.id, f.filename, f.mime_type, f.file_size,
                   f.tags, f.metadata, f.created_at, f.updated_at, f.status,
                   ft.total_lines,
                   (SELECT COUNT(*) FROM pages p WHERE p.file_id = f.id) AS total_pages
            FROM files f
            LEFT JOIN file_text ft ON ft.file_id = f.id
            WHERE f.id = $1
            """,
            file_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="File not found")

    return {
        "id": str(row["id"]),
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "file_size": row["file_size"],
        "total_pages": row["total_pages"],
        "total_lines": row["total_lines"],
        "tags": row["tags"],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router.delete("/{file_id}")
async def delete_file(file_id: UUID):
    """Delete a file and all associated data."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT file_path FROM files WHERE id = $1", file_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="File not found")

        file_path = Path(row["file_path"])

        # Delete from DB (cascades to pages + file_text)
        await conn.execute("DELETE FROM files WHERE id = $1", file_id)

    # Clean up filesystem
    file_dir = file_path.parent
    if file_dir.exists():
        shutil.rmtree(file_dir, ignore_errors=True)

    return {"status": "deleted", "id": str(file_id)}
