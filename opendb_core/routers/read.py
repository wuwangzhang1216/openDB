"""Read endpoint: GET /read/{filename}"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from opendb_core.services.read_service import (
    AmbiguousFilenameError,
    FileNotFoundError,
    read_file_text,
    read_structured_spreadsheet,
    resolve_filename,
)
from opendb_core.utils.text import format_with_line_numbers

router = APIRouter(tags=["read"])


@router.get("/read/{filename:path}")
async def read_file(
    filename: str,
    pages: str | None = Query(None, description="Page/slide/sheet range: 3, 3-7, 1,3,5, or sheet name"),
    lines: str | None = Query(None, description="Line range: 50-80"),
    grep: str | None = Query(None, description="Search pattern, use + for multi-term"),
    toc: bool = Query(False, description="Return table of contents"),
    format: str | None = Query(None, description="Output format: 'json' for structured data (spreadsheets only)"),
    numbered: bool = Query(False, description="Add line numbers to output (cat -n style)"),
) -> JSONResponse | PlainTextResponse:
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

    # Structured JSON output for spreadsheets
    if format == "json":
        try:
            data = await read_structured_spreadsheet(file_id, pages=pages)
            return JSONResponse(content=data)
        except ValueError as e:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": str(e)},
            )

    text, info = await read_file_text(
        file_id, pages=pages, lines=lines, grep=grep, toc=toc
    )

    # Apply line numbering if requested
    if numbered and not grep and not toc:
        start_line = 1
        if lines:
            # Preserve original line numbers when a range is specified
            parts = lines.strip().split("-")
            start_line = int(parts[0])
        text = format_with_line_numbers(text, start=start_line)

    return PlainTextResponse(
        content=text,
        headers={
            "X-FileDB-Id": info["file_id"],
            "X-FileDB-Pages": str(info["total_pages"]),
            "X-FileDB-Lines": str(info["total_lines"]),
        },
    )
