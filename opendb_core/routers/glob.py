"""Glob endpoint: GET /glob — file pattern matching on the filesystem."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["glob"])

_MAX_RESULTS = 500


@router.get("/glob")
async def glob_files(
    pattern: str = Query(..., description="Glob pattern, e.g. '**/*.py', 'src/**/*.ts'"),
    path: str | None = Query(None, description="Root directory to search in"),
) -> dict | JSONResponse:
    """Find files matching a glob pattern. Returns paths sorted by modification time (newest first)."""
    if not path:
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "detail": "path parameter is required"},
        )

    root = Path(path)
    if not root.is_dir():
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "detail": f"Directory not found: {path}"},
        )

    try:
        matches = []
        for p in root.glob(pattern):
            if p.is_file():
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    mtime = 0.0
                matches.append((p, mtime))
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "detail": f"Invalid glob pattern: {e}"},
        )

    # Sort by modification time, newest first
    matches.sort(key=lambda x: x[1], reverse=True)

    truncated = len(matches) > _MAX_RESULTS
    matches = matches[:_MAX_RESULTS]

    # Return relative paths
    files = []
    for p, _ in matches:
        try:
            files.append(str(p.relative_to(root)).replace(os.sep, "/"))
        except ValueError:
            files.append(str(p).replace(os.sep, "/"))

    return {
        "count": len(files),
        "truncated": truncated,
        "files": files,
    }
