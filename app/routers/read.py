"""Read endpoint: GET /read/{filename}"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from app.services.read_service import (
    AmbiguousFilenameError,
    FileNotFoundError,
    read_file_text,
    resolve_filename,
)

router = APIRouter(tags=["read"])


@router.get("/read/{filename:path}")
async def read_file(
    filename: str,
    pages: str | None = Query(None, description="Page/slide/sheet range: 3, 3-7, 1,3,5, or sheet name"),
    lines: str | None = Query(None, description="Line range: 50-80"),
    grep: str | None = Query(None, description="Search pattern, use + for multi-term"),
    toc: bool = Query(False, description="Return table of contents"),
):
    """Read a file as plain text. Like cat but for any file format."""
    try:
        file_id = await resolve_filename(filename)
    except FileNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": "file_not_found", "detail": f"No file matching '{filename}'"},
        )
    except AmbiguousFilenameError as e:
        return JSONResponse(
            status_code=409,
            content={"error": "ambiguous_filename", "candidates": e.candidates},
        )

    text, info = await read_file_text(
        file_id, pages=pages, lines=lines, grep=grep, toc=toc
    )

    return PlainTextResponse(
        content=text,
        headers={
            "X-FileDB-Id": info["file_id"],
            "X-FileDB-Pages": str(info["total_pages"]),
            "X-FileDB-Lines": str(info["total_lines"]),
        },
    )
