"""Unit tests for the tokenizer module — CJK segmentation, hyphens, registration."""

import os
from unittest.mock import patch

import pytest

from opendb_core.utils.tokenizer import (
    tokenize_for_fts,
    register_tokenizer,
    _expand_hyphens,
    _CJK_RE,
    _THAI_RE,
    _HYPHEN_RE,
    _tokenizers,
)


# ---------------------------------------------------------------------------
# Regex pattern tests
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_cjk_regex_matches_chinese(self):
        assert _CJK_RE.search("你好") is not None

    def test_cjk_regex_matches_japanese_hiragana(self):
        assert _CJK_RE.search("こんにちは") is not None

    def test_cjk_regex_matches_japanese_katakana(self):
        assert _CJK_RE.search("カタカナ") is not None

    def test_cjk_regex_matches_korean(self):
        assert _CJK_RE.search("한국어") is not None

    def test_cjk_regex_no_match_latin(self):
        assert _CJK_RE.search("hello world") is None

    def test_thai_regex_matches(self):
        assert _THAI_RE.search("สวัสดี") is not None

    def test_thai_regex_no_match_latin(self):
        assert _THAI_RE.search("hello") is None

    def test_hyphen_regex_matches(self):
        m = _HYPHEN_RE.search("gardening-related tips")
        assert m is not None
        assert m.group(0) == "gardening-related"

    def test_hyphen_regex_multi_hyphen(self):
        m = _HYPHEN_RE.search("state-of-the-art model")
        assert m is not None
        assert m.group(0) == "state-of-the-art"


# ---------------------------------------------------------------------------
# _expand_hyphens
# ---------------------------------------------------------------------------

class TestExpandHyphens:
    def test_single_hyphen(self):
        result = _expand_hyphens("gardening-related tips")
        assert "gardening-related" in result
        assert "gardening" in result
        assert "related" in result
        assert "tips" in result

    def test_no_hyphens(self):
        text = "hello world"
        assert _expand_hyphens(text) == text

    def test_multi_hyphen(self):
        result = _expand_hyphens("state-of-the-art")
        assert "state-of-the-art" in result
        assert "state" in result
        assert "art" in result


# ---------------------------------------------------------------------------
# tokenize_for_fts
# ---------------------------------------------------------------------------

class TestTokenizeForFts:
    def test_latin_text_unchanged(self):
        text = "hello world"
        assert tokenize_for_fts(text) == text

    def test_chinese_text_segmented(self):
        result = tokenize_for_fts("今天天气很好")
        assert " " in result
        assert "天气" in result

    def test_mixed_cjk_latin(self):
        result = tokenize_for_fts("hello 你好 world")
        assert "hello" in result
        assert " " in result

    def test_hyphenated_words_expanded(self):
        result = tokenize_for_fts("gardening-related tips")
        assert "gardening" in result
        assert "related" in result
        assert "gardening-related" in result

    def test_empty_string(self):
        assert tokenize_for_fts("") == ""

    def test_pure_numbers(self):
        result = tokenize_for_fts("12345")
        assert "12345" in result

    def test_japanese_segmented(self):
        result = tokenize_for_fts("東京は日本の首都です")
        assert " " in result

    def test_korean_segmented(self):
        result = tokenize_for_fts("서울은 대한민국의 수도입니다")
        # jieba may not segment Korean well, but should not crash
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# register_tokenizer
# ---------------------------------------------------------------------------

class TestRegisterTokenizer:
    def test_register_custom(self):
        def my_tokenizer(text: str) -> str:
            return text.upper()

        register_tokenizer("test_upper", my_tokenizer)
        assert "test_upper" in _tokenizers
        assert _tokenizers["test_upper"]("hello") == "HELLO"

    def test_register_overrides_existing(self):
        original = _tokenizers.get("jieba")
        try:
            register_tokenizer("jieba", lambda t: "CUSTOM")
            assert _tokenizers["jieba"]("anything") == "CUSTOM"
        finally:
            # Restore original
            if original:
                _tokenizers["jieba"] = original

    def test_custom_tokenizer_used_via_env(self):
        register_tokenizer("test_custom", lambda t: "tokenized:" + t)
        try:
            with patch.dict(os.environ, {"FILEDB_TOKENIZER": "test_custom"}):
                result = tokenize_for_fts("中文测试")
                assert result == "tokenized:中文测试"
        finally:
            _tokenizers.pop("test_custom", None)
