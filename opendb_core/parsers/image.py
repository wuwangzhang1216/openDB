"""Image parser — synchronous text extraction only.

For OCR: uses pytesseract (if installed).
For LLM vision: the ingest pipeline calls vision_service directly (async).
This parser is intentionally sync — it runs inside asyncio.to_thread.
"""

from __future__ import annotations

import logging
from pathlib import Path

from opendb_core.config import settings
from opendb_core.parsers.base import Page, ParseResult
from opendb_core.parsers.registry import register

logger = logging.getLogger(__name__)


class ImageParser:
    def parse(self, file_path: Path) -> ParseResult:
        text = ""

        # Try Tesseract OCR if available
        if settings.ocr_enabled:
            text = self._ocr_image(file_path)

        if not text.strip():
            # Placeholder — will be replaced by vision service in the ingest pipeline
            text = f"(image: {file_path.name})"

        return ParseResult(
            pages=[Page(page_number=1, section_title=None, text=text.strip())]
        )

    def _ocr_image(self, file_path: Path) -> str:
        """Run OCR on an image file (Tesseract)."""
        try:
            import pytesseract
            from PIL import Image, ImageEnhance

            with Image.open(str(file_path)) as image:
                image = image.convert("L")
                image = ImageEnhance.Contrast(image).enhance(1.5)
                return pytesseract.image_to_string(image, lang=settings.ocr_languages)
        except Exception:
            return ""


_parser = ImageParser()
register("image/png", _parser)
register("image/jpeg", _parser)
register("image/tiff", _parser)
register("image/bmp", _parser)
register("image/webp", _parser)
register("image/*", _parser)
