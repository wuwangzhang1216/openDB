"""File ingestion pipeline.

Orchestrates: receive -> save -> parse -> index -> ready.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path

import magic

from opendb_core.config import settings
from opendb_core.parsers.registry import parse_file
from opendb_core.storage import get_backend
from opendb_core.utils.hashing import compute_sha256
from opendb_core.utils.text import assemble_text

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
    """Infer a document category from filename, MIME type, and content sample."""
    for pattern, doc_type in _FILENAME_PATTERNS:
        if pattern.search(filename):
            return doc_type

    if text_sample:
        for pattern, doc_type in _CONTENT_PATTERNS:
            if pattern.search(text_sample):
                return doc_type

    if mime_type in _MIME_TYPE_MAP:
        return _MIME_TYPE_MAP[mime_type]
    if mime_type.startswith("image/"):
        return "image"

    return None


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
    """Parse the file, enrich metadata, and persist via the active backend."""
    from opendb_core.parsers.base import Page

    parse_result = await asyncio.to_thread(parse_file, file_path, mime_type)
    logger.info("Parsed %s: %d pages", original_filename, len(parse_result.pages))

    # For images: enhance with LLM vision description (async, no Tesseract needed)
    if mime_type.startswith("image/") and settings.vision_enabled:
        try:
            from opendb_core.services.vision_service import describe_image
            description = await describe_image(file_path, api_key=settings.vision_api_key or None)
            if description.strip() and not description.startswith("("):
                parse_result.pages = [
                    Page(page_number=1, section_title=None, text=description.strip())
                ]
                logger.info("Vision described %s: %d chars", original_filename, len(description))
        except Exception as e:
            logger.warning("Vision failed for %s, keeping parser output: %s", original_filename, e)

    full_text, line_index, toc, page_line_ranges = await asyncio.to_thread(
        assemble_text, parse_result.pages, mime_type
    )
    total_lines = full_text.count("\n") + 1

    merged_metadata = metadata.copy()
    if parse_result.extracted_metadata:
        merged_metadata["_extracted"] = parse_result.extracted_metadata

    if source_stat_result is not None:
        merged_metadata["fs_created"] = source_stat_result.st_ctime
        merged_metadata["fs_modified"] = source_stat_result.st_mtime

    inferred = infer_document_type(original_filename, mime_type, full_text[:500])
    if inferred:
        merged_metadata["inferred_type"] = inferred

    backend = get_backend()
    return await backend.persist_ingestion(
        file_id=str(file_id),
        file_path=str(file_path),
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
# Public API: ingest from bytes (upload flow)
# ---------------------------------------------------------------------------

async def ingest_file(
    file_content: bytes,
    original_filename: str,
    mime_type: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Ingest a file from bytes: save to disk, parse, index in storage."""
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

        backend = get_backend()
        dup = await backend.check_duplicate(checksum)
        if dup:
            shutil.rmtree(file_dir, ignore_errors=True)
            return dup

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
            backend = get_backend()
            await backend.mark_file_failed(str(file_id), str(e))
        except Exception as inner_err:
            logger.error("Failed to mark file %s as failed: %s", file_id, inner_err)

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
    """Ingest a file from a local filesystem path."""
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

        checksum = await asyncio.to_thread(compute_sha256, source_path)

        backend = get_backend()
        dup = await backend.check_duplicate(checksum)
        if dup:
            shutil.rmtree(file_dir, ignore_errors=True)
            return dup

        # Upsert: if a file from the same source path exists but content changed,
        # delete the old record so we can re-ingest with the new content.
        existing = await backend.find_by_source_path(str(source_path))
        if existing:
            old_path = await backend.delete_file(existing)
            if old_path:
                old_dir = Path(old_path).parent
                if old_dir.exists() and old_dir != settings.file_storage_path:
                    shutil.rmtree(old_dir, ignore_errors=True)
            logger.info("Re-ingesting modified file: %s", source_path)

        await asyncio.to_thread(shutil.copy2, source_path, file_path)

        # Store source path in metadata for upsert matching
        merged_meta = dict(metadata or {})
        merged_meta["source_path"] = str(source_path)

        return await _parse_and_persist(
            file_id=file_id,
            file_path=file_path,
            original_filename=original_filename,
            mime_type=mime_type,
            file_size=file_size,
            checksum=checksum,
            tags=tags or [],
            metadata=merged_meta,
            source_stat_result=source_stat,
        )

    except Exception as e:
        logger.error("Ingestion failed for %s: %s", source_path, e, exc_info=True)
        try:
            backend = get_backend()
            await backend.mark_file_failed(str(file_id), str(e))
        except Exception as inner_err:
            logger.error("Failed to mark file %s as failed: %s", file_id, inner_err)

        if file_dir.exists():
            shutil.rmtree(file_dir, ignore_errors=True)

        raise
