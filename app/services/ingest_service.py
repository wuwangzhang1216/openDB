"""File ingestion pipeline.

Orchestrates: receive -> save -> parse -> index -> ready.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import uuid
from pathlib import Path

import asyncpg
import magic

from app.config import settings
from app.database import get_pool
from app.parsers.registry import parse_file
from app.utils.hashing import compute_sha256
from app.utils.text import assemble_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Document type inference
# ---------------------------------------------------------------------------

_FILENAME_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"invoice", re.IGNORECASE), "invoice"),
    (re.compile(r"receipt", re.IGNORECASE), "receipt"),
    (re.compile(r"contract", re.IGNORECASE), "contract"),
    (re.compile(r"report", re.IGNORECASE), "report"),
    (re.compile(r"(resume|cv\b)", re.IGNORECASE), "resume"),
    (re.compile(r"statement", re.IGNORECASE), "statement"),
    (re.compile(r"letter", re.IGNORECASE), "letter"),
    (re.compile(r"memo", re.IGNORECASE), "memo"),
    (re.compile(r"proposal", re.IGNORECASE), "proposal"),
    (re.compile(r"agenda", re.IGNORECASE), "agenda"),
    (re.compile(r"minutes", re.IGNORECASE), "minutes"),
]

_CONTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(INVOICE|Invoice\s+#|Invoice\s+Number|Bill\s+To|Total\s+Due)", re.IGNORECASE), "invoice"),
    (re.compile(r"(RECEIPT|Payment\s+Received|Transaction\s+ID)", re.IGNORECASE), "receipt"),
    (re.compile(r"(CONTRACT|AGREEMENT|hereby\s+agree|terms\s+and\s+conditions)", re.IGNORECASE), "contract"),
]

_MIME_TYPE_MAP: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
    "application/vnd.ms-excel": "spreadsheet",
    "text/csv": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "presentation",
    "application/vnd.ms-powerpoint": "presentation",
}


def infer_document_type(
    filename: str, mime_type: str, text_sample: str,
) -> str | None:
    """Infer a document category from filename, MIME type, and content sample.

    Returns a short label like 'invoice', 'receipt', 'report', etc., or None.
    """
    # 1. Filename patterns (highest signal)
    for pattern, doc_type in _FILENAME_PATTERNS:
        if pattern.search(filename):
            return doc_type

    # 2. Content keyword patterns
    if text_sample:
        for pattern, doc_type in _CONTENT_PATTERNS:
            if pattern.search(text_sample):
                return doc_type

    # 3. MIME type fallback
    if mime_type in _MIME_TYPE_MAP:
        return _MIME_TYPE_MAP[mime_type]
    if mime_type.startswith("image/"):
        return "image"

    return None


# ---------------------------------------------------------------------------
# Core persistence helper (shared by both ingestion paths)
# ---------------------------------------------------------------------------

async def _persist_ingestion(
    *,
    file_id: uuid.UUID,
    file_path: Path,
    original_filename: str,
    mime_type: str,
    file_size: int,
    checksum: str,
    tags: list[str],
    merged_metadata: dict,
    parse_result,
    full_text: str,
    total_lines: int,
    line_index: list[int],
    toc: str,
    page_line_ranges: list[tuple[int, int]],
) -> dict:
    """Write parsed file data into the database (atomic transaction).

    Returns the file record dict on success.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO files (id, filename, mime_type, file_size, file_path,
                                       checksum, status, tags, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, 'processing', $7, $8)
                    """,
                    file_id,
                    original_filename,
                    mime_type,
                    file_size,
                    str(file_path),
                    checksum,
                    tags,
                    json.dumps(merged_metadata),
                )

                for i, page in enumerate(parse_result.pages):
                    line_start, line_end = page_line_ranges[i]
                    await conn.execute(
                        """
                        INSERT INTO pages (file_id, page_number, section_title,
                                           content_type, text, line_start, line_end)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        file_id,
                        page.page_number,
                        page.section_title,
                        page.content_type,
                        page.text,
                        line_start,
                        line_end,
                    )

                await conn.execute(
                    """
                    INSERT INTO file_text (file_id, full_text, total_lines, line_index, toc)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    file_id,
                    full_text,
                    total_lines,
                    line_index,
                    toc,
                )

                await conn.execute(
                    """
                    UPDATE files SET status = 'ready', metadata = $2
                    WHERE id = $1
                    """,
                    file_id,
                    json.dumps(merged_metadata),
                )
        except asyncpg.UniqueViolationError:
            existing = await conn.fetchrow(
                "SELECT id, filename FROM files WHERE checksum = $1 AND status = 'ready'",
                checksum,
            )
            if existing:
                return {
                    "id": str(existing["id"]),
                    "filename": existing["filename"],
                    "status": "duplicate",
                    "detail": "File with identical content already exists",
                }
            raise

    return {
        "id": str(file_id),
        "filename": original_filename,
        "mime_type": mime_type,
        "file_size": file_size,
        "status": "ready",
        "total_pages": len(parse_result.pages),
        "total_lines": total_lines,
        "metadata": merged_metadata,
    }


# ---------------------------------------------------------------------------
# Shared: parse + enrich metadata + persist
# ---------------------------------------------------------------------------

async def _parse_and_persist(
    *,
    file_id: uuid.UUID,
    file_path: Path,
    original_filename: str,
    mime_type: str,
    file_size: int,
    checksum: str,
    tags: list[str],
    metadata: dict,
    source_stat_result,
) -> dict:
    """Parse the file, enrich metadata, and persist to DB."""
    parse_result = await asyncio.to_thread(parse_file, file_path, mime_type)
    logger.info("Parsed %s: %d pages", original_filename, len(parse_result.pages))

    full_text, line_index, toc, page_line_ranges = await asyncio.to_thread(
        assemble_text, parse_result.pages, mime_type
    )
    total_lines = full_text.count("\n") + 1

    # Merge metadata
    merged_metadata = metadata.copy()
    if parse_result.extracted_metadata:
        merged_metadata["_extracted"] = parse_result.extracted_metadata

    # Enrich with filesystem timestamps
    if source_stat_result is not None:
        merged_metadata["fs_created"] = source_stat_result.st_ctime
        merged_metadata["fs_modified"] = source_stat_result.st_mtime

    # Infer document type
    inferred = infer_document_type(original_filename, mime_type, full_text[:500])
    if inferred:
        merged_metadata["inferred_type"] = inferred

    return await _persist_ingestion(
        file_id=file_id,
        file_path=file_path,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size=file_size,
        checksum=checksum,
        tags=tags,
        merged_metadata=merged_metadata,
        parse_result=parse_result,
        full_text=full_text,
        total_lines=total_lines,
        line_index=line_index,
        toc=toc,
        page_line_ranges=page_line_ranges,
    )


# ---------------------------------------------------------------------------
# Duplicate check helper
# ---------------------------------------------------------------------------

async def _check_duplicate(checksum: str) -> dict | None:
    """Return existing file record if a duplicate exists, else None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, filename FROM files WHERE checksum = $1 AND status = 'ready'",
            checksum,
        )
    if existing:
        return {
            "id": str(existing["id"]),
            "filename": existing["filename"],
            "status": "duplicate",
            "detail": "File with identical content already exists",
        }
    return None


# ---------------------------------------------------------------------------
# Public API: ingest from bytes (existing upload flow)
# ---------------------------------------------------------------------------

async def ingest_file(
    file_content: bytes,
    original_filename: str,
    mime_type: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Ingest a file from bytes: save to disk, parse, index in database.

    Returns the file record dict.
    """
    if len(file_content) > settings.max_file_size:
        raise ValueError(
            f"File size {len(file_content)} exceeds max {settings.max_file_size}"
        )

    file_id = uuid.uuid4()
    ext = Path(original_filename).suffix or ".bin"
    file_dir = settings.file_storage_path / str(file_id)
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / f"original{ext}"

    try:
        logger.info(
            "Ingesting %s (%s, %d bytes)", original_filename, mime_type, len(file_content)
        )

        file_path.write_bytes(file_content)

        checksum = await asyncio.to_thread(compute_sha256, file_path)

        dup = await _check_duplicate(checksum)
        if dup:
            shutil.rmtree(file_dir, ignore_errors=True)
            return dup

        # Stat the saved copy (source timestamps not available for upload)
        source_stat = file_path.stat()

        return await _parse_and_persist(
            file_id=file_id,
            file_path=file_path,
            original_filename=original_filename,
            mime_type=mime_type,
            file_size=len(file_content),
            checksum=checksum,
            tags=tags or [],
            metadata=metadata or {},
            source_stat_result=source_stat,
        )

    except Exception as e:
        logger.error("Ingestion failed for %s: %s", original_filename, e, exc_info=True)
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE files SET status = 'failed', error_message = $2
                    WHERE id = $1
                    """,
                    file_id,
                    str(e),
                )
        except Exception as inner_err:
            logger.error("Failed to mark file %s as failed in DB: %s", file_id, inner_err)

        if file_dir.exists():
            shutil.rmtree(file_dir, ignore_errors=True)

        raise


# ---------------------------------------------------------------------------
# Public API: ingest from local filesystem path (for /index endpoint)
# ---------------------------------------------------------------------------

async def ingest_local_file(
    source_path: Path,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Ingest a file from a local filesystem path.

    Copies the file into managed storage without loading it entirely into memory.
    Returns the file record dict.
    """
    source_stat = source_path.stat()
    file_size = source_stat.st_size

    if file_size > settings.max_file_size:
        raise ValueError(
            f"File size {file_size} exceeds max {settings.max_file_size}"
        )

    mime_type = magic.from_file(str(source_path), mime=True)
    original_filename = source_path.name

    file_id = uuid.uuid4()
    ext = source_path.suffix or ".bin"
    file_dir = settings.file_storage_path / str(file_id)
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / f"original{ext}"

    try:
        logger.info(
            "Ingesting local %s (%s, %d bytes)", source_path, mime_type, file_size
        )

        # Compute checksum from source (no copy yet)
        checksum = await asyncio.to_thread(compute_sha256, source_path)

        dup = await _check_duplicate(checksum)
        if dup:
            shutil.rmtree(file_dir, ignore_errors=True)
            return dup

        # Copy file to managed storage (preserves timestamps)
        await asyncio.to_thread(shutil.copy2, source_path, file_path)

        return await _parse_and_persist(
            file_id=file_id,
            file_path=file_path,
            original_filename=original_filename,
            mime_type=mime_type,
            file_size=file_size,
            checksum=checksum,
            tags=tags or [],
            metadata=metadata or {},
            source_stat_result=source_stat,
        )

    except Exception as e:
        logger.error("Ingestion failed for %s: %s", source_path, e, exc_info=True)
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE files SET status = 'failed', error_message = $2
                    WHERE id = $1
                    """,
                    file_id,
                    str(e),
                )
        except Exception as inner_err:
            logger.error("Failed to mark file %s as failed in DB: %s", file_id, inner_err)

        if file_dir.exists():
            shutil.rmtree(file_dir, ignore_errors=True)

        raise
