"""File ingestion pipeline.

Orchestrates: receive → save → parse → index → ready.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from app.config import settings
from app.database import get_pool
from app.parsers.registry import parse_file
from app.utils.hashing import compute_sha256
from app.utils.text import assemble_text


async def ingest_file(
    file_content: bytes,
    original_filename: str,
    mime_type: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Ingest a file: save to disk, parse, index in database.

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
        # Save file to disk
        file_path.write_bytes(file_content)

        # Compute checksum
        checksum = compute_sha256(file_path)

        # Check for duplicate
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, filename FROM files WHERE checksum = $1 AND status = 'ready'",
                checksum,
            )
        if existing:
            # Clean up the just-saved file
            shutil.rmtree(file_dir, ignore_errors=True)
            return {
                "id": str(existing["id"]),
                "filename": existing["filename"],
                "status": "duplicate",
                "detail": "File with identical content already exists",
            }

        # Parse the file
        parse_result = parse_file(file_path, mime_type)

        # Assemble text
        full_text, line_index, toc, page_line_ranges = assemble_text(
            parse_result.pages, mime_type
        )
        total_lines = full_text.count("\n") + 1

        # Merge metadata
        merged_metadata = metadata or {}
        if parse_result.extracted_metadata:
            merged_metadata["_extracted"] = parse_result.extracted_metadata

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Insert files row
                await conn.execute(
                    """
                    INSERT INTO files (id, filename, mime_type, file_size, file_path,
                                       checksum, status, tags, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, 'processing', $7, $8)
                    """,
                    file_id,
                    original_filename,
                    mime_type,
                    len(file_content),
                    str(file_path),
                    checksum,
                    tags or [],
                    json.dumps(merged_metadata),
                )

                # Insert pages
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

                # Insert file_text
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

                # Update status to ready
                await conn.execute(
                    """
                    UPDATE files SET status = 'ready', metadata = $2
                    WHERE id = $1
                    """,
                    file_id,
                    json.dumps(merged_metadata),
                )

        return {
            "id": str(file_id),
            "filename": original_filename,
            "mime_type": mime_type,
            "file_size": len(file_content),
            "status": "ready",
            "total_pages": len(parse_result.pages),
            "total_lines": total_lines,
            "metadata": merged_metadata,
        }

    except Exception as e:
        # Try to mark as failed in DB if possible
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
        except Exception:
            pass

        # Clean up filesystem on failure
        if file_dir.exists():
            shutil.rmtree(file_dir, ignore_errors=True)

        raise
