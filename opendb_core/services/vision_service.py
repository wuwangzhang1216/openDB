"""Vision service — describe images using a free vision-capable LLM.

Calls OpenRouter's google/gemma-3-27b-it:free via plain HTTP (no SDK needed).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from pathlib import Path

logger = logging.getLogger(__name__)

_VISION_MODEL = "google/gemini-2.5-flash-lite"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_DESCRIBE_PROMPT = (
    "Describe this image in detail. Extract ALL text, numbers, dates, and "
    "amounts visible in the image. If it's a receipt, invoice, or bill, "
    "list: vendor name, date, line items with amounts, subtotal, tax, and total. "
    "If it's a screenshot of a website or app, describe the UI and extract all visible text. "
    "Respond in the same language as the text in the image."
)

# Concurrency guard — avoid hammering the API with too many parallel calls
_semaphore = asyncio.Semaphore(8)


async def describe_image(
    file_path: Path,
    *,
    api_key: str | None = None,
    prompt: str | None = None,
) -> str:
    """Describe an image using the free vision model."""
    import os

    key = (
        api_key
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("MUSE_OPENROUTER_API_KEY")
    )
    if not file_path.exists():
        return f"(image not found: {file_path})"

    if not key:
        logger.info(
            "No OpenRouter API key — falling back to Tesseract OCR for %s. "
            "Set OPENROUTER_API_KEY or MUSE_OPENROUTER_API_KEY for LLM-based descriptions.",
            file_path,
        )
        return await _tesseract_fallback(file_path)

    # Build data URL
    raw = file_path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = f"image/{file_path.suffix.lstrip('.')}"
    data_url = f"data:{mime_type};base64,{b64}"

    async with _semaphore:
        return await _call_api(key, data_url, prompt or _DESCRIBE_PROMPT)


async def _call_api(
    api_key: str,
    image_url: str,
    prompt: str,
    *,
    max_retries: int = 3,
) -> str:
    """POST to OpenRouter with retry on 429."""
    import httpx

    payload = {
        "model": _VISION_MODEL,
        "provider": {"sort": "throughput"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(max_retries):
            try:
                resp = await client.post(_OPENROUTER_URL, json=payload, headers=headers)

                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.info("Vision 429, retry in %ds (%d/%d)", wait, attempt + 1, max_retries)
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return text.strip() if text else "(no description extracted)"

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.info("Vision 429, retry in %ds (%d/%d)", wait, attempt + 1, max_retries)
                    await asyncio.sleep(wait)
                    continue
                logger.warning("Vision API error: %s", e)
                return f"(vision API error: {e})"
            except Exception as e:
                logger.warning("Vision API call failed: %s", e)
                return f"(vision API error: {e})"

    return "(vision API rate limited — try again later)"


async def _tesseract_fallback(file_path: Path) -> str:
    """Extract text from image using Tesseract OCR as a fallback."""
    try:
        from PIL import Image
        import pytesseract

        img = await asyncio.to_thread(Image.open, file_path)
        text = await asyncio.to_thread(
            pytesseract.image_to_string, img, lang="eng+chi_sim+chi_tra"
        )
        text = text.strip()
        if text:
            logger.info("Tesseract OCR extracted %d chars from %s", len(text), file_path.name)
            return text
        return "(no text detected by Tesseract OCR)"
    except ImportError:
        logger.warning("Tesseract/Pillow not installed — cannot OCR %s", file_path)
        return "(no API key configured; install pytesseract + Pillow for OCR fallback)"
    except Exception as e:
        logger.warning("Tesseract OCR failed for %s: %s", file_path, e)
        return f"(OCR failed: {e})"
