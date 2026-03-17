"""Directory indexing service.

Scans a local directory, computes checksums, skips duplicates,
and ingests supported files concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import magic

from app.config import settings
from app.database import get_pool
from app.parsers.registry import get_parser
from app.services.ingest_service import ingest_local_file
from app.utils.hashing import compute_sha256

logger = logging.getLogger(__name__)

# Directories and files to always skip
_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".idea", ".vscode", ".tox", ".mypy_cache", ".pytest_cache",
}
_EXCLUDE_FILES = {".DS_Store", "thumbs.db", "Thumbs.db", "desktop.ini"}


def _is_excluded(path: Path, extra_excludes: list[str]) -> bool:
    """Check if a path component matches an exclusion pattern."""
    parts = set(path.parts)
    if parts & _EXCLUDE_DIRS:
        return True
    if path.name in _EXCLUDE_FILES:
        return True
    if path.name.startswith("."):
        return True
    for pattern in extra_excludes:
        if pattern in parts or path.name == pattern:
            return True
    return False


def scan_directory(
    dir_path: Path, extra_excludes: list[str] | None = None,
) -> list[Path]:
    """Recursively find all files in *dir_path*, skipping excluded dirs/files."""
    excludes = extra_excludes or []
    results: list[Path] = []
    for child in sorted(dir_path.rglob("*")):
        if not child.is_file():
            continue
        if _is_excluded(child.relative_to(dir_path), excludes):
            continue
        results.append(child)
    return results


def _has_parser(mime_type: str) -> bool:
    """Return True if we have a parser for *mime_type*."""
    try:
        get_parser(mime_type)
        return True
    except ValueError:
        return False


async def batch_check_duplicates(checksums: list[str]) -> set[str]:
    """Return the subset of *checksums* that already exist in the database."""
    if not checksums:
        return set()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT checksum FROM files "
            "WHERE checksum = ANY($1) AND status = 'ready'",
            checksums,
        )
    return {row["checksum"] for row in rows}


async def index_directory(
    dir_path: Path,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    max_concurrent: int = 4,
) -> dict:
    """Scan and ingest all supported files in a directory.

    Returns a summary dict with counts and per-file results.
    """
    files = await asyncio.to_thread(
        scan_directory, dir_path, settings.index_exclude_patterns
    )

    # Classify files: detect MIME and check parser support
    supported: list[tuple[Path, str]] = []  # (path, mime_type)
    unsupported_files: list[Path] = []

    for f in files:
        try:
            mime = magic.from_file(str(f), mime=True)
        except Exception:
            unsupported_files.append(f)
            continue
        if _has_parser(mime):
            supported.append((f, mime))
        else:
            unsupported_files.append(f)

    # Compute checksums for supported files (thread-pooled)
    async def _compute_checksum(path: Path) -> str:
        return await asyncio.to_thread(compute_sha256, path)

    checksum_map: dict[Path, str] = {}
    sem = asyncio.Semaphore(max_concurrent)

    async def _checksum_worker(path: Path):
        async with sem:
            checksum_map[path] = await _compute_checksum(path)

    await asyncio.gather(*[_checksum_worker(p) for p, _ in supported])

    # Batch duplicate check
    existing_checksums = await batch_check_duplicates(list(checksum_map.values()))

    # Partition into to-ingest and duplicates
    to_ingest: list[tuple[Path, str]] = []
    results: list[dict] = []

    for path, mime in supported:
        cs = checksum_map[path]
        if cs in existing_checksums:
            results.append({
                "filename": path.name,
                "status": "skipped",
                "reason": "duplicate",
            })
        else:
            to_ingest.append((path, mime))

    # Ingest concurrently with semaphore
    ingest_sem = asyncio.Semaphore(max_concurrent)

    async def _ingest_worker(path: Path, mime: str) -> dict:
        async with ingest_sem:
            try:
                result = await ingest_local_file(
                    source_path=path,
                    tags=tags,
                    metadata=metadata,
                )
                return {
                    "filename": path.name,
                    "status": result["status"],
                    "id": result.get("id"),
                }
            except Exception as e:
                logger.error("Failed to ingest %s: %s", path, e, exc_info=True)
                return {
                    "filename": path.name,
                    "status": "failed",
                    "error": str(e),
                }

    ingest_results = await asyncio.gather(
        *[_ingest_worker(p, m) for p, m in to_ingest]
    )
    results.extend(ingest_results)

    # Add unsupported files to results
    for path in unsupported_files:
        results.append({
            "filename": path.name,
            "status": "unsupported",
        })

    # Aggregate counts
    ingested = sum(1 for r in results if r["status"] == "ready")
    skipped = sum(1 for r in results if r["status"] in ("skipped", "duplicate"))
    failed = sum(1 for r in results if r["status"] == "failed")
    unsupported = sum(1 for r in results if r["status"] == "unsupported")

    # Auto-start watching the directory
    from app.services.watch_service import start_watch
    watch_id = start_watch(dir_path.resolve(), tags=tags, metadata=metadata)

    return {
        "path": str(dir_path.resolve()),
        "total_files": len(files),
        "ingested": ingested,
        "skipped": skipped,
        "failed": failed,
        "unsupported": unsupported,
        "watch_id": watch_id,
        "files": results,
    }
