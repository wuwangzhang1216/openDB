from __future__ import annotations

import re

from app.parsers.base import Page


def build_line_index(text: str) -> list[int]:
    """Compute byte offset of each line start in text (0-indexed lines internally)."""
    index = [0]
    for i, char in enumerate(text):
        if char == "\n":
            index.append(i + 1)
    return index


def extract_lines(text: str, line_index: list[int], start: int, end: int) -> str:
    """Extract lines [start, end] (1-indexed, inclusive) using byte offsets."""
    # Convert to 0-indexed
    start_idx = start - 1
    end_idx = end  # end is inclusive, so we need offset of line after end

    if start_idx < 0:
        start_idx = 0
    if start_idx >= len(line_index):
        return ""

    begin = line_index[start_idx]

    if end_idx < len(line_index):
        # Go to the start of the line after 'end', then strip the trailing newline
        finish = line_index[end_idx]
        return text[begin:finish].rstrip("\n")
    else:
        return text[begin:].rstrip("\n")


def grep_with_context(text: str, pattern: str, context: int = 2) -> str:
    """In-memory grep with context lines.

    Supports multi-term search via '+' separator (all terms must match).
    Returns matching lines with context, prefixed with line info.
    """
    lines = text.split("\n")
    terms = [t.strip().lower() for t in pattern.split("+") if t.strip()]

    # Find matching line numbers (0-indexed)
    match_indices: set[int] = set()
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if all(term in line_lower for term in terms):
            match_indices.add(i)

    if not match_indices:
        return ""

    # Expand with context
    display_indices: set[int] = set()
    for idx in match_indices:
        for offset in range(-context, context + 1):
            neighbor = idx + offset
            if 0 <= neighbor < len(lines):
                display_indices.add(neighbor)

    # Build output with group separators
    sorted_indices = sorted(display_indices)
    result_lines: list[str] = []
    current_page = _detect_current_page(lines, 0)

    for i, idx in enumerate(sorted_indices):
        # Insert separator between non-contiguous groups
        if i > 0 and idx > sorted_indices[i - 1] + 1:
            result_lines.append("--")

        # Track current page marker
        current_page = _detect_current_page(lines, idx, current_page)

        line_num = idx + 1  # 1-indexed
        prefix = f"[{current_page}, Line {line_num}]" if current_page else f"[Line {line_num}]"
        result_lines.append(f"{prefix} {lines[idx]}")

    return "\n".join(result_lines)


def _detect_current_page(lines: list[str], up_to_idx: int, default: str = "") -> str:
    """Scan backwards from up_to_idx to find the most recent page marker."""
    page_pattern = re.compile(r"^\[(Page|Slide|Sheet|Section)\s+.+\]$")
    for i in range(up_to_idx, -1, -1):
        stripped = lines[i].strip()
        if page_pattern.match(stripped):
            return stripped
    return default


def format_page_marker(page: Page, file_mime: str = "") -> str:
    """Generate the appropriate page marker string."""
    if "presentation" in file_mime:
        return f"[Slide {page.page_number}]"
    if "spreadsheet" in file_mime or "csv" in file_mime:
        if page.section_title:
            return f"[Sheet: {page.section_title}]"
        return f"[Sheet {page.page_number}]"
    return f"[Page {page.page_number}]"


def assemble_text(
    pages: list[Page], file_mime: str = ""
) -> tuple[str, list[int], str, list[tuple[int, int]]]:
    """Assemble full text from pages.

    Returns:
        (full_text, line_index, toc, page_line_ranges)
        page_line_ranges: list of (line_start, line_end) for each page (1-indexed)
    """
    lines: list[str] = []
    toc_lines: list[str] = []
    page_line_ranges: list[tuple[int, int]] = []
    current_line = 1

    for page in pages:
        marker = format_page_marker(page, file_mime)

        # Add page marker + blank line
        lines.append(marker)
        lines.append("")
        current_line += 2

        page_start = current_line
        page_lines = page.text.split("\n")
        lines.extend(page_lines)
        current_line += len(page_lines)

        page_end = current_line - 1
        page_line_ranges.append((page_start, page_end))

        # Blank line between pages
        lines.append("")
        current_line += 1

        # Build TOC entry
        title = page.section_title or f"Page {page.page_number}"
        toc_lines.append(f"{marker} {title} (lines {page_start}-{page_end})")

    full_text = "\n".join(lines)
    line_index = build_line_index(full_text)
    toc = "\n".join(toc_lines)

    return full_text, line_index, toc, page_line_ranges
