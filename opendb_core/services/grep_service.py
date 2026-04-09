"""Grep service: regex search across files on the filesystem."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path


async def grep_files(
    query: str,
    path: str,
    glob: str | None = None,
    case_insensitive: bool = False,
    context: int = 0,
    max_results: int = 100,
    per_file_timeout: float = 5.0,
) -> dict:
    """Regex search across files in a directory.

    Returns matching lines with file paths, line numbers, and optional context.
    ``per_file_timeout`` caps how long (seconds) a single file may be read.
    """
    return await asyncio.to_thread(
        _grep_files_sync,
        query, path, glob, case_insensitive, context, max_results,
        per_file_timeout,
    )


_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB — skip files larger than this


def _grep_files_sync(
    query: str,
    path: str,
    glob: str | None,
    case_insensitive: bool,
    context: int,
    max_results: int,
    per_file_timeout: float = 5.0,
) -> dict:
    """Synchronous grep implementation, run in a thread."""
    import time

    root = Path(path)
    if not root.is_dir():
        return {"total": 0, "results": [], "error": f"Directory not found: {path}"}

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        pattern = re.compile(query, flags)
    except re.error as e:
        return {"total": 0, "results": [], "error": f"Invalid regex: {e}"}

    file_pattern = glob or "**/*"
    results: list[dict] = []
    total = 0
    timed_out_files: list[str] = []

    for file_path in root.glob(file_pattern):
        if not file_path.is_file():
            continue

        # Skip binary files and common non-text directories
        rel = str(file_path.relative_to(root)).replace(os.sep, "/")
        if _should_skip(rel):
            continue

        # Skip files larger than threshold
        try:
            if file_path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue

        try:
            deadline = time.monotonic() + per_file_timeout
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        lines = text.split("\n")
        for i, line in enumerate(lines):
            # Check per-file timeout every 5000 lines
            if i % 5000 == 0 and time.monotonic() > deadline:
                timed_out_files.append(rel)
                break

            if not pattern.search(line):
                continue

            total += 1
            if len(results) >= max_results:
                return {
                    "total": total,
                    "results": results,
                    "truncated": True,
                }

            ctx_before = []
            ctx_after = []
            if context > 0:
                for c in range(max(0, i - context), i):
                    ctx_before.append(lines[c])
                for c in range(i + 1, min(len(lines), i + context + 1)):
                    ctx_after.append(lines[c])

            results.append({
                "file": rel,
                "line": i + 1,  # 1-indexed
                "text": line,
                "context_before": ctx_before,
                "context_after": ctx_after,
            })

    return {
        "total": total,
        "results": results,
        "truncated": False,
        **({"timed_out_files": timed_out_files} if timed_out_files else {}),
    }


# Directories and file patterns to skip
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".next", ".nuxt", ".svelte-kit",
}

_SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".pptx",
    ".db", ".sqlite", ".sqlite3",
}


def _should_skip(rel_path: str) -> bool:
    """Check if a file should be skipped based on path patterns."""
    parts = rel_path.split("/")
    for part in parts:
        if part in _SKIP_DIRS:
            return True
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in _SKIP_EXTENSIONS:
        return True
    return False
