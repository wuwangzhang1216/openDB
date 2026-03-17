"""Image parser using Pillow + pytesseract for OCR.

Supports PNG, JPEG, TIFF, BMP.
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.parsers.base import Page, ParseResult
from app.parsers.registry import register


class ImageParser:
    def parse(self, file_path: Path) -> ParseResult:
        if not settings.ocr_enabled:
            return ParseResult(
                pages=[
                    Page(
                        page_number=1,
                        section_title=None,
                        text="(OCR disabled — image content not extracted)",
                    )
                ]
            )

        text = self._ocr_image(file_path)

        if not text.strip():
            text = "(no text detected in image)"

        pages = [
            Page(page_number=1, section_title=None, text=text.strip())
        ]

        return ParseResult(pages=pages)

    def _ocr_image(self, file_path: Path) -> str:
        """Run OCR on an image file."""
        import pytesseract
        from PIL import Image, ImageEnhance

        with Image.open(str(file_path)) as image:
            # Preprocess: grayscale + contrast
            image = image.convert("L")
            image = ImageEnhance.Contrast(image).enhance(1.5)

            text = pytesseract.image_to_string(
                image, lang=settings.ocr_languages
            )
        return text


_parser = ImageParser()
register("image/png", _parser)
register("image/jpeg", _parser)
register("image/tiff", _parser)
register("image/bmp", _parser)
register("image/*", _parser)
