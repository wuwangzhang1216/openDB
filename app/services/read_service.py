"""Read service: filename resolution, line extraction, grep."""

from __future__ import annotations

import re
from uuid import UUID

from app.database import get_pool
from app.utils.text import extract_lines, grep_with_context


class AmbiguousFilenameError(Exception):
    def __init__(self, candidates: list[dict]):
        self.candidates = candidates
        super().__init__(f"Ambiguous filename, {len(candidates)} candidates found")


class FileNotFoundError(Exception):
    pass


async def resolve_filename(filename: str) -> UUID:
    """Resolve a filename to a file UUID.

    Resolution order: exact → UUID → fuzzy (trigram) → unique substring.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Exact match
        row = await conn.fetchrow(
            "SELECT id FROM files WHERE filename = $1 AND status = 'ready'",
            filename,
        )
        if row:
            return row["id"]

        # 2. UUID match
        try:
            file_id = UUID(filename)
            row = await conn.fetchrow(
                "SELECT id FROM files WHERE id = $1 AND status = 'ready'",
                file_id,
            )
            if row:
                return row["id"]
        except ValueError:
            pass

        # 3. Fuzzy match (trigram similarity)
        rows = await conn.fetch(
            "SELECT id, filename, similarity(filename, $1) AS sim "
            "FROM files WHERE filename % $1 AND status = 'ready' "
            "ORDER BY sim DESC LIMIT 5",
            filename,
        )
        if len(rows) == 1:
            return rows[0]["id"]
        if len(rows) > 1:
            # If top match is significantly better, use it
            if rows[0]["sim"] > rows[1]["sim"] + 0.2:
                return rows[0]["id"]
            raise AmbiguousFilenameError(
                candidates=[
                    {"id": str(r["id"]), "filename": r["filename"]} for r in rows
                ]
            )

        # 4. Unique substring match
        rows = await conn.fetch(
            "SELECT id, filename FROM files "
            "WHERE filename ILIKE $1 AND status = 'ready'",
            f"%{filename}%",
        )
        if len(rows) == 1:
            return rows[0]["id"]
        if len(rows) > 1:
            raise AmbiguousFilenameError(
                candidates=[
                    {"id": str(r["id"]), "filename": r["filename"]} for r in rows
                ]
            )

        raise FileNotFoundError(f"No file matching '{filename}'")


async def get_file_text(file_id: UUID) -> dict:
    """Get the full text record for a file."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT full_text, total_lines, line_index, toc "
            "FROM file_text WHERE file_id = $1",
            file_id,
        )
        if not row:
            raise FileNotFoundError(f"No text found for file {file_id}")
        return dict(row)


async def get_total_pages(file_id: UUID) -> int:
    """Get the total number of pages for a file."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM pages WHERE file_id = $1", file_id
        )
        return count


async def get_page_line_ranges(
    file_id: UUID, page_numbers: list[int]
) -> list[tuple[int, int]]:
    """Get line ranges for specific pages."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT line_start, line_end FROM pages "
            "WHERE file_id = $1 AND page_number = ANY($2) "
            "ORDER BY page_number",
            file_id,
            page_numbers,
        )
        return [(r["line_start"], r["line_end"]) for r in rows]


async def get_page_by_section_title(file_id: UUID, title: str) -> list[int]:
    """Find page numbers by section title (for XLSX sheet names etc)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT page_number FROM pages "
            "WHERE file_id = $1 AND section_title ILIKE $2 "
            "ORDER BY page_number",
            file_id,
            f"%{title}%",
        )
        return [r["page_number"] for r in rows]


def parse_page_spec(spec: str) -> list[int] | str:
    """Parse a page specification string.

    Returns list of ints for numeric specs, or a string for named specs (sheet names).
    Examples: "3" → [3], "3-7" → [3,4,5,6,7], "1,3,5" → [1,3,5], "Revenue" → "Revenue"
    """
    spec = spec.strip()

    # Try numeric patterns
    if re.match(r"^\d+$", spec):
        return [int(spec)]

    if re.match(r"^\d+-\d+$", spec):
        parts = spec.split("-")
        start, end = int(parts[0]), int(parts[1])
        return list(range(start, end + 1))

    if re.match(r"^\d+(,\d+)+$", spec):
        return [int(x) for x in spec.split(",")]

    # Non-numeric: treat as section/sheet name
    return spec


def parse_line_spec(spec: str) -> tuple[int, int]:
    """Parse a line range specification. Example: "50-80" → (50, 80)."""
    parts = spec.strip().split("-")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    if len(parts) == 1:
        n = int(parts[0])
        return n, n
    raise ValueError(f"Invalid line spec: {spec}")


async def read_file_text(
    file_id: UUID,
    pages: str | None = None,
    lines: str | None = None,
    grep: str | None = None,
    toc: bool = False,
) -> tuple[str, dict]:
    """Read file text with optional filtering.

    Returns (text, info_dict).
    """
    text_row = await get_file_text(file_id)
    total_pages = await get_total_pages(file_id)

    info = {
        "file_id": str(file_id),
        "total_pages": total_pages,
        "total_lines": text_row["total_lines"],
    }

    # Return TOC
    if toc:
        return text_row["toc"] or "", info

    text = text_row["full_text"]
    line_index = text_row["line_index"]

    # Filter by pages
    if pages:
        page_spec = parse_page_spec(pages)
        if isinstance(page_spec, str):
            # Named page (sheet name)
            page_nums = await get_page_by_section_title(file_id, page_spec)
        else:
            page_nums = page_spec

        if page_nums:
            ranges = await get_page_line_ranges(file_id, page_nums)
            parts = []
            for start, end in ranges:
                parts.append(extract_lines(text, line_index, start, end))
            text = "\n\n".join(parts)

    # Filter by lines
    if lines:
        start, end = parse_line_spec(lines)
        text = extract_lines(text, line_index, start, end)

    # Grep
    if grep:
        text = grep_with_context(text, grep, context=2)

    return text, info
