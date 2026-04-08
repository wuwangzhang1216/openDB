"""Directory indexing service.

Scans a local directory, computes checksums, skips duplicates,
and ingests supported files concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import magic

from opendb_core.config import settings
from opendb_core.parsers.registry import get_parser
from opendb_core.services.ingest_service import ingest_local_file
from opendb_core.storage import get_backend
from opendb_core.utils.hashing import compute_sha256

logger = logging.getLogger(__name__)

# Directories and files to always skip
_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".idea", ".vscode", ".tox", ".mypy_cache", ".pytest_cache",
    ".opendb",  # embedded workspace metadata directory
}
_EXCLUDE_FILES = {".DS_Store", "thumbs.db", "Thumbs.db", "desktop.ini"}


def _is_excluded(path: Path, extra_excludes: list[str]) -> bool:
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
    try:
        get_parser(mime_type)
        return True
    except ValueError:
        return False


async def batch_check_duplicates(checksums: list[str]) -> set[str]:
    """Return the subset of *checksums* that already exist in storage."""
    return await get_backend().batch_check_duplicates(checksums)


async def index_directory(
    dir_path: Path,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    max_concurrent: int = 8,
    incremental: bool = True,
) -> dict:
    """Scan and ingest all supported files in a directory.

    When *incremental* is True (default), files whose checksum matches an
    existing record are skipped.  Files whose source_path already exists but
    with a **different** checksum are re-ingested (old record deleted first).
    """
    files = await asyncio.to_thread(
        scan_directory, dir_path, settings.index_exclude_patterns
    )

    # Detect MIME types in parallel
    mime_sem = asyncio.Semaphore(max_concurrent)

    async def _detect_mime(f: Path) -> tuple[Path, str | None]:
        async with mime_sem:
            try:
                mime = await asyncio.to_thread(magic.from_file, str(f), mime=True)
                return (f, mime)
            except Exception:
                return (f, None)

    mime_results = await asyncio.gather(*[_detect_mime(f) for f in files])

    supported: list[tuple[Path, str]] = []
    unsupported_files: list[Path] = []

    for f, mime in mime_results:
        if mime and _has_parser(mime):
            supported.append((f, mime))
        else:
            unsupported_files.append(f)

    # Compute checksums
    checksum_map: dict[Path, str] = {}
    sem = asyncio.Semaphore(max_concurrent)

    async def _checksum_worker(path: Path):
        async with sem:
            checksum_map[path] = await asyncio.to_thread(compute_sha256, path)

    await asyncio.gather(*[_checksum_worker(p) for p, _ in supported])

    existing_checksums = await batch_check_duplicates(list(checksum_map.values()))

    to_ingest: list[tuple[Path, str]] = []
    results: list[dict] = []
    updated = 0

    backend = get_backend()
    for path, mime in supported:
        cs = checksum_map[path]
        if cs in existing_checksums:
            results.append({"filename": path.name, "status": "skipped", "reason": "duplicate"})
            continue

        # Incremental: check if file already indexed under a different checksum
        if incremental:
            source = str(path.resolve()).replace("\\", "/")
            existing_id = await backend.find_by_source_path(source)
            if existing_id:
                # Content changed — delete old record and re-ingest
                await backend.delete_file(existing_id)
                updated += 1
                logger.info("Re-indexing changed file: %s", path.name)

        to_ingest.append((path, mime))

    ingest_sem = asyncio.Semaphore(max_concurrent)

    async def _ingest_worker(path: Path, mime: str) -> dict:
        async with ingest_sem:
            try:
                result = await ingest_local_file(source_path=path, tags=tags, metadata=metadata)
                return {"filename": path.name, "status": result["status"], "id": result.get("id")}
            except Exception as e:
                logger.error("Failed to ingest %s: %s", path, e, exc_info=True)
                return {"filename": path.name, "status": "failed", "error": str(e)}

    ingest_results = await asyncio.gather(*[_ingest_worker(p, m) for p, m in to_ingest])
    results.extend(ingest_results)

    for path in unsupported_files:
        results.append({"filename": path.name, "status": "unsupported"})

    ingested = sum(1 for r in results if r["status"] == "ready")
    skipped = sum(1 for r in results if r["status"] in ("skipped", "duplicate"))
    failed = sum(1 for r in results if r["status"] == "failed")
    unsupported = sum(1 for r in results if r["status"] == "unsupported")

    return {
        "path": str(dir_path.resolve()),
        "total_files": len(files),
        "ingested": ingested,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "unsupported": unsupported,
        "files": results,
    }
