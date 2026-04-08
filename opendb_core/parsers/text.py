"""Plain text / Markdown / HTML parser.

Chunks text at ~3000 characters on paragraph boundaries.
Extracts headings as section titles.
"""

from __future__ import annotations

import re
from pathlib import Path

from opendb_core.parsers.base import Page, ParseResult
from opendb_core.parsers.registry import register

CHUNK_SIZE = 3000

# Patterns for detecting headings
_MD_HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
_ALLCAPS_HEADING = re.compile(r"^([A-Z][A-Z0-9 ]{2,78})$", re.MULTILINE)


class TextParser:
    def parse(self, file_path: Path) -> ParseResult:
        text = self._read_file(file_path)
        if not text.strip():
            return ParseResult(pages=[Page(page_number=1, section_title=None, text="")])

        chunks = self._chunk_text(text)
        pages: list[Page] = []
        for i, chunk in enumerate(chunks, start=1):
            title = self._extract_title(chunk)
            pages.append(Page(page_number=i, section_title=title, text=chunk))

        return ParseResult(pages=pages)

    def _read_file(self, file_path: Path) -> str:
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return file_path.read_text(encoding=encoding)
            except (UnicodeDecodeError, ValueError):
                continue
        return file_path.read_text(encoding="latin-1", errors="replace")

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into chunks of ~CHUNK_SIZE on paragraph boundaries."""
        if len(text) <= CHUNK_SIZE:
            return [text]

        # Split on double newlines (paragraph breaks)
        paragraphs = re.split(r"\n\s*\n", text)
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para)
            if current_len + para_len > CHUNK_SIZE and current:
                chunks.append("\n\n".join(current))
                current = [para]
                current_len = para_len
            else:
                current.append(para)
                current_len += para_len + 2  # +2 for \n\n

        if current:
            chunks.append("\n\n".join(current))

        return chunks

    def _extract_title(self, chunk: str) -> str | None:
        """Extract the first heading from a chunk."""
        # Try markdown headings first
        match = _MD_HEADING.search(chunk)
        if match:
            return match.group(1).strip()

        # Try all-caps lines
        for line in chunk.split("\n")[:5]:  # Only check first 5 lines
            line = line.strip()
            if _ALLCAPS_HEADING.match(line) and len(line) > 3:
                return line

        return None


# Register for text MIME types
_parser = TextParser()
register("text/plain", _parser)
register("text/markdown", _parser)
register("text/html", _parser)
register("text/*", _parser)
