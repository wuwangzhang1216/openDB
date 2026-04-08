"""Tokenizer utilities for FTS5 indexing — handles CJK segmentation via jieba.

Custom tokenizers can be registered via ``register_tokenizer(name, fn)``
where *fn* accepts and returns a ``str``.  The active tokenizer is chosen
via ``FILEDB_TOKENIZER`` env var (default: ``"jieba"``).
"""

from __future__ import annotations

import os
import re
from typing import Callable

# CJK Unicode ranges: Chinese, Hiragana, Katakana, Hangul
_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]"
)

# Thai Unicode range
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")

# Hyphenated compound words: split "gardening-related" → "gardening-related gardening related"
_HYPHEN_RE = re.compile(r"\b(\w+(?:-\w+)+)\b")

# Registry of tokenizer functions: name → callable(text) → tokenized_text
_tokenizers: dict[str, Callable[[str], str]] = {}


def _jieba_tokenize(text: str) -> str:
    """Default CJK tokenizer using jieba."""
    import jieba
    return " ".join(jieba.cut_for_search(text))


def _pythainlp_tokenize(text: str) -> str:
    """Thai tokenizer using PyThaiNLP (must be installed separately)."""
    try:
        from pythainlp.tokenize import word_tokenize
        return " ".join(word_tokenize(text, engine="newmm"))
    except ImportError:
        raise RuntimeError(
            "PyThaiNLP is required for Thai tokenization. "
            "Install it with: pip install pythainlp"
        )


# Register built-in tokenizers
_tokenizers["jieba"] = _jieba_tokenize
_tokenizers["pythainlp"] = _pythainlp_tokenize


def register_tokenizer(name: str, fn: Callable[[str], str]) -> None:
    """Register a custom tokenizer function.

    Args:
        name: Tokenizer name (used in FILEDB_TOKENIZER env var).
        fn: Callable that takes raw text and returns space-separated tokens.
    """
    _tokenizers[name] = fn


def _expand_hyphens(text: str) -> str:
    """Expand hyphenated words while keeping the original form.

    "gardening-related tips" → "gardening-related gardening related tips"
    """
    def _replace(m: re.Match) -> str:
        original = m.group(0)
        parts = original.split("-")
        return original + " " + " ".join(parts)

    return _HYPHEN_RE.sub(_replace, text)


def _get_tokenizer_name() -> str:
    return os.environ.get("FILEDB_TOKENIZER", "jieba")


def tokenize_for_fts(text: str) -> str:
    """Tokenize text for FTS5 indexing.

    For text containing CJK characters, applies the configured tokenizer
    (default: jieba) so that FTS5 can index individual words.
    For Thai text, uses PyThaiNLP if configured.
    For pure Latin/ASCII text, returns as-is (FTS5 unicode61 handles it fine).

    Hyphenated compound words are always expanded so both the whole form
    and individual parts are indexed.
    """
    text = _expand_hyphens(text)

    tokenizer_name = _get_tokenizer_name()

    # Check if text needs non-Latin tokenization
    has_cjk = bool(_CJK_RE.search(text))
    has_thai = bool(_THAI_RE.search(text))

    if not has_cjk and not has_thai:
        return text

    if has_thai and tokenizer_name == "pythainlp":
        fn = _tokenizers.get("pythainlp")
        if fn:
            return fn(text)

    # Default: use jieba for CJK (also handles Thai-CJK mixed text)
    fn = _tokenizers.get(tokenizer_name, _jieba_tokenize)
    return fn(text)
