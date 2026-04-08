"""Read service: filename resolution, line extraction, grep."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from uuid import UUID

from opendb_core.storage import get_backend
from opendb_core.utils.text import extract_lines, grep_with_context

_SPREADSHEET_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
}


class AmbiguousFilenameError(Exception):
    def __init__(self, candidates: list[dict]):
        self.candidates = candidates
        super().__init__(f"Ambiguous filename, {len(candidates)} candidates found")


class FileNotFoundError(Exception):
    pass


async def resolve_filename(filename: str) -> UUID:
    """Resolve a filename to a file UUID.

    Resolution order: exact → UUID → path-aware → fuzzy → unique substring.
    """
    backend = get_backend()

    # 1. Exact match
    fid = await backend.find_file_exact(filename)
    if fid:
        return UUID(fid)

    # 2. UUID match
    fid = await backend.find_file_by_uuid(filename)
    if fid:
        return UUID(fid)

    # 3. Path-aware match — bridge for glob → read and grep → read handoffs.
    # When the caller passes a relative path like "docs/report.pdf", match it
    # as a boundary-aware suffix of files.metadata.source_path.
    if "/" in filename or "\\" in filename:
        path_rows = await backend.find_by_source_path_suffix(filename)
        if len(path_rows) == 1:
            return UUID(path_rows[0]["id"])
        if len(path_rows) > 1:
            raise AmbiguousFilenameError(
                candidates=[{"id": r["id"], "filename": r["filename"]} for r in path_rows]
            )
        # 0 matches → fall through; a path-looking string may still basename-match.

    # 4. Fuzzy match
    rows = await backend.find_files_fuzzy(filename)
    if len(rows) == 1:
        return UUID(rows[0]["id"])
    if len(rows) > 1:
        if rows[0]["sim"] > rows[1]["sim"] + 0.2:
            return UUID(rows[0]["id"])
        raise AmbiguousFilenameError(
            candidates=[{"id": r["id"], "filename": r["filename"]} for r in rows]
        )

    # 5. Unique substring match
    rows = await backend.find_files_ilike(filename)
    if len(rows) == 1:
        return UUID(rows[0]["id"])
    if len(rows) > 1:
        raise AmbiguousFilenameError(
            candidates=[{"id": r["id"], "filename": r["filename"]} for r in rows]
        )

    raise FileNotFoundError(f"No file matching '{filename}'")


async def get_file_text(file_id: UUID) -> dict:
    """Get the full text record for a file."""
    return await get_backend().get_file_text(str(file_id))


async def get_total_pages(file_id: UUID) -> int:
    """Get the total number of pages for a file."""
    return await get_backend().get_total_pages(str(file_id))


async def get_page_line_ranges(
    file_id: UUID, page_numbers: list[int]
) -> list[tuple[int, int]]:
    """Get line ranges for specific pages."""
    return await get_backend().get_page_line_ranges(str(file_id), page_numbers)


async def get_page_by_section_title(file_id: UUID, title: str) -> list[int]:
    """Find page numbers by section title."""
    return await get_backend().get_page_by_section_title(str(file_id), title)


def parse_page_spec(spec: str) -> list[int] | str:
    """Parse a page specification string.

    Returns list of ints for numeric specs, or a string for named specs.
    Examples: "3" → [3], "3-7" → [3,4,5,6,7], "1,3,5" → [1,3,5], "Revenue" → "Revenue"
    """
    spec = spec.strip()

    if re.match(r"^\d+$", spec):
        return [int(spec)]

    if re.match(r"^\d+-\d+$", spec):
        parts = spec.split("-")
        start, end = int(parts[0]), int(parts[1])
        return list(range(start, end + 1))

    if re.match(r"^\d+(,\d+)+$", spec):
        return [int(x) for x in spec.split(",")]

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
    """Read file text with optional filtering. Returns (text, info_dict)."""
    text_row = await get_file_text(file_id)
    total_pages = await get_total_pages(file_id)

    info = {
        "file_id": str(file_id),
        "total_pages": total_pages,
        "total_lines": text_row["total_lines"],
    }

    if toc:
        return text_row["toc"] or "", info

    text = text_row["full_text"]
    line_index = text_row["line_index"]

    if pages:
        page_spec = parse_page_spec(pages)
        if isinstance(page_spec, str):
            page_nums = await get_page_by_section_title(file_id, page_spec)
        else:
            page_nums = page_spec

        if page_nums:
            ranges = await get_page_line_ranges(file_id, page_nums)
            parts = []
            for start, end in ranges:
                parts.append(extract_lines(text, line_index, start, end))
            text = "\n\n".join(parts)

    if lines:
        start, end = parse_line_spec(lines)
        text = extract_lines(text, line_index, start, end)

    if grep:
        text = grep_with_context(text, grep, context=2)

    return text, info


# ---------------------------------------------------------------------------
# Structured spreadsheet reading
# ---------------------------------------------------------------------------

async def get_file_info(file_id: UUID) -> dict:
    """Get file_path, mime_type, and filename for a file."""
    return await get_backend().get_file_info(str(file_id))


def _parse_structured(file_path_str: str, mime_type: str, sheet_filter: list[str] | None) -> dict:
    """Synchronous helper — re-parse a spreadsheet into structured JSON."""
    from opendb_core.parsers.spreadsheet import XlsxParser, CsvParser

    file_path = Path(file_path_str)
    if mime_type == "text/csv":
        parser = CsvParser()
    else:
        parser = XlsxParser()
    return parser.parse_structured(file_path, sheet_filter=sheet_filter)


async def read_structured_spreadsheet(
    file_id: UUID,
    pages: str | None = None,
) -> dict:
    """Read a spreadsheet file and return structured JSON with columns/rows."""
    info = await get_file_info(file_id)
    mime_type = info["mime_type"]

    if mime_type not in _SPREADSHEET_MIMES:
        raise ValueError(
            f"format=json is only supported for spreadsheet files, got {mime_type}"
        )

    sheet_filter: list[str] | None = None
    if pages:
        page_spec = parse_page_spec(pages)
        if isinstance(page_spec, str):
            sheet_filter = [page_spec]
        else:
            names = await get_backend().get_sheet_names_for_pages(str(file_id), page_spec)
            sheet_filter = names if names else None

    return await asyncio.to_thread(
        _parse_structured, info["file_path"], mime_type, sheet_filter
    )
