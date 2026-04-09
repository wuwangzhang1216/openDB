"""PDF parser using pymupdf (fitz).

Features: text extraction, table detection, scanned page OCR, metadata extraction.
"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz  # pymupdf

from opendb_core.config import settings
from opendb_core.parsers.base import Page, ParseResult
from opendb_core.parsers.registry import register

logger = logging.getLogger(__name__)

# If a page yields fewer than this many characters, treat as scanned
SCANNED_THRESHOLD = 50


class PdfParser:
    def parse(self, file_path: Path) -> ParseResult:
        doc = fitz.open(str(file_path))
        try:
            pages: list[Page] = []
            total_text_len = 0

            # First pass: extract text and detect if scanned
            raw_texts: list[str] = []
            for page in doc:
                text = page.get_text()
                raw_texts.append(text)
                total_text_len += len(text.strip())

            avg_text_per_page = total_text_len / max(len(doc), 1)
            needs_ocr = avg_text_per_page < SCANNED_THRESHOLD and settings.ocr_enabled

            for page_num, page in enumerate(doc, start=1):
                text = raw_texts[page_num - 1]

                # OCR fallback for scanned pages
                if needs_ocr and len(text.strip()) < SCANNED_THRESHOLD:
                    text = self._ocr_page(page)

                # Extract tables
                tables_text = self._extract_tables(page)
                if tables_text:
                    text = text.rstrip() + "\n\n" + tables_text

                # Extract section title from first line or large-font text
                section_title = self._extract_title(page)

                pages.append(
                    Page(
                        page_number=page_num,
                        section_title=section_title,
                        text=text.strip(),
                    )
                )

            metadata = self._extract_metadata(doc)
        finally:
            doc.close()

        return ParseResult(pages=pages, extracted_metadata=metadata)

    def _ocr_page(self, page: fitz.Page) -> str:
        """Render page to image and OCR it."""
        try:
            import pytesseract
            from PIL import Image
            import io

            pix = page.get_pixmap(dpi=300)
            try:
                img_bytes = pix.tobytes("png")
            finally:
                pix = None  # Release native pixmap memory
            image = Image.open(io.BytesIO(img_bytes))

            # Preprocess: grayscale + contrast
            image = image.convert("L")
            from PIL import ImageEnhance
            image = ImageEnhance.Contrast(image).enhance(1.5)

            text = pytesseract.image_to_string(
                image, lang=settings.ocr_languages
            )
            return text
        except (ImportError, OSError, RuntimeError):
            logger.warning("OCR failed for page %s", page.number, exc_info=True)
            return ""

    def _extract_tables(self, page: fitz.Page) -> str:
        """Extract tables from a page as pipe-delimited text."""
        try:
            tables = page.find_tables()
            if not tables or not tables.tables:
                return ""

            parts: list[str] = []
            for table in tables:
                rows = table.extract()
                if not rows:
                    continue

                # Format as pipe-delimited
                formatted_rows: list[str] = []
                for row in rows:
                    cells = [str(c) if c is not None else "" for c in row]
                    formatted_rows.append("| " + " | ".join(cells) + " |")

                # Add header separator after first row
                if len(formatted_rows) > 1:
                    separator = "|" + "|".join(
                        "-" * (len(c) + 2) for c in rows[0]
                    ) + "|"
                    formatted_rows.insert(1, separator)

                parts.append("\n".join(formatted_rows))

            return "\n\n".join(parts)
        except (AttributeError, RuntimeError, ValueError):
            logger.debug("Table extraction failed for page %s", page.number, exc_info=True)
            return ""

    def _extract_title(self, page: fitz.Page) -> str | None:
        """Extract section title from a page using font analysis."""
        try:
            blocks = page.get_text("dict")["blocks"]
            max_size = 0
            title_text = None

            for block in blocks[:3]:  # Check first 3 blocks
                if "lines" not in block:
                    continue
                for line in block["lines"][:2]:  # Check first 2 lines
                    for span in line["spans"]:
                        if span["size"] > max_size and span["text"].strip():
                            max_size = span["size"]
                            title_text = span["text"].strip()

            # Only return if significantly larger than body text
            if max_size > 14:
                return title_text
        except (KeyError, AttributeError, TypeError):
            pass
        return None

    def _extract_metadata(self, doc: fitz.Document) -> dict:
        """Extract PDF metadata."""
        meta = doc.metadata or {}
        result = {}
        field_map = {
            "title": "title",
            "author": "author",
            "subject": "subject",
            "creator": "creator",
            "creationDate": "creation_date",
            "modDate": "mod_date",
        }
        for pdf_key, our_key in field_map.items():
            val = meta.get(pdf_key)
            if val and val.strip():
                result[our_key] = val.strip()

        result["page_count"] = len(doc)
        return result


register("application/pdf", PdfParser())
