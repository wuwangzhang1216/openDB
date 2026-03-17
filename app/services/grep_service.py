"""Grep service: regex search across files on the filesystem."""

from __future__ import annotations

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
) -> dict:
    """Regex search across files in a directory.

    Returns matching lines with file paths, line numbers, and optional context.
    """
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

    for file_path in root.glob(file_pattern):
        if not file_path.is_file():
            continue

        # Skip binary files and common non-text directories
        rel = str(file_path.relative_to(root)).replace(os.sep, "/")
        if _should_skip(rel):
            continue

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        lines = text.split("\n")
        match_indices: list[int] = []

        for i, line in enumerate(lines):
            if pattern.search(line):
                match_indices.append(i)

        if not match_indices:
            continue

        for idx in match_indices:
            total += 1
            if len(results) >= max_results:
                continue

            ctx_before = []
            ctx_after = []
            if context > 0:
                for c in range(max(0, idx - context), idx):
                    ctx_before.append(lines[c])
                for c in range(idx + 1, min(len(lines), idx + context + 1)):
                    ctx_after.append(lines[c])

            results.append({
                "file": rel,
                "line": idx + 1,  # 1-indexed
                "text": lines[idx],
                "context_before": ctx_before,
                "context_after": ctx_after,
            })

    return {
        "total": total,
        "results": results,
        "truncated": total > max_results,
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
