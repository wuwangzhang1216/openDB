"""Unit tests for file parsers.

These are pure unit tests — no database, no async. They exercise the most
fragile code in the project: format-specific parsing logic.
"""

from pathlib import Path

import pytest

from opendb_core.parsers.base import ParseResult
from opendb_core.parsers.registry import get_parser

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helper to validate ParseResult invariants
# ---------------------------------------------------------------------------

def assert_valid_result(result: ParseResult) -> None:
    """Every ParseResult must have non-empty pages with valid fields."""
    assert isinstance(result, ParseResult)
    assert len(result.pages) > 0, "ParseResult must have at least one page"
    for page in result.pages:
        assert page.page_number >= 1, f"page_number must be >= 1, got {page.page_number}"
        assert isinstance(page.text, str), "page.text must be a string"


# ---------------------------------------------------------------------------
# TextParser
# ---------------------------------------------------------------------------

class TestTextParser:
    def test_parse_sample(self) -> None:
        result = get_parser("text/plain").parse(FIXTURES / "sample.txt")
        assert_valid_result(result)
        # Content should contain the text
        all_text = "\n".join(p.text for p in result.pages)
        assert "quick brown fox" in all_text

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        result = get_parser("text/plain").parse(f)
        assert_valid_result(result)
        assert result.pages[0].text == ""

    def test_heading_extraction(self) -> None:
        result = get_parser("text/markdown").parse(FIXTURES / "sample.txt")
        assert_valid_result(result)
        # Should detect markdown headings as section titles
        titles = [p.section_title for p in result.pages if p.section_title]
        assert len(titles) > 0, "Should extract at least one heading"

    def test_large_text_gets_chunked(self, tmp_path: Path) -> None:
        f = tmp_path / "large.txt"
        # Create text > CHUNK_SIZE (3000) to trigger chunking
        paragraph = "This is a test paragraph. " * 50 + "\n\n"
        f.write_text(paragraph * 10, encoding="utf-8")
        result = get_parser("text/plain").parse(f)
        assert_valid_result(result)
        assert len(result.pages) > 1, "Large text should produce multiple pages"

    def test_encoding_fallback(self, tmp_path: Path) -> None:
        f = tmp_path / "latin1.txt"
        f.write_bytes("café résumé naïve".encode("latin-1"))
        result = get_parser("text/plain").parse(f)
        assert_valid_result(result)
        assert "caf" in result.pages[0].text


# ---------------------------------------------------------------------------
# CsvParser
# ---------------------------------------------------------------------------

class TestCsvParser:
    def test_parse_sample(self) -> None:
        result = get_parser("text/csv").parse(FIXTURES / "sample.csv")
        assert_valid_result(result)
        all_text = "\n".join(p.text for p in result.pages)
        assert "Alice" in all_text
        assert "name" in all_text.lower()

    def test_empty_csv(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.csv"
        f.write_text("", encoding="utf-8")
        result = get_parser("text/csv").parse(f)
        assert_valid_result(result)

    def test_single_column(self, tmp_path: Path) -> None:
        f = tmp_path / "single.csv"
        f.write_text("value\n1\n2\n3\n", encoding="utf-8")
        result = get_parser("text/csv").parse(f)
        assert_valid_result(result)


# ---------------------------------------------------------------------------
# XlsxParser
# ---------------------------------------------------------------------------

class TestXlsxParser:
    @pytest.fixture
    def sample_xlsx(self, tmp_path: Path) -> Path:
        """Create a minimal XLSX fixture at test time."""
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Data"
        ws.append(["Name", "Value"])
        ws.append(["Alpha", 100])
        ws.append(["Beta", 200])

        wb.create_sheet("Empty")  # empty sheet

        path = tmp_path / "sample.xlsx"
        wb.save(path)
        wb.close()
        return path

    def test_parse_basic(self, sample_xlsx: Path) -> None:
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        result = get_parser(mime).parse(sample_xlsx)
        assert_valid_result(result)
        all_text = "\n".join(p.text for p in result.pages)
        assert "Alpha" in all_text
        assert "Beta" in all_text

    def test_empty_sheet_handled(self, sample_xlsx: Path) -> None:
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        result = get_parser(mime).parse(sample_xlsx)
        # Should have pages for both sheets including the empty one
        titles = [p.section_title for p in result.pages]
        assert any("Empty" in (t or "") for t in titles)

    def test_metadata_extraction(self, sample_xlsx: Path) -> None:
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        result = get_parser(mime).parse(sample_xlsx)
        meta = result.extracted_metadata
        assert "sheet_names" in meta
        assert "Data" in meta["sheet_names"]


# ---------------------------------------------------------------------------
# Parser Registry
# ---------------------------------------------------------------------------

class TestParserRegistry:
    def test_exact_match(self) -> None:
        parser = get_parser("text/plain")
        assert parser is not None

    def test_prefix_match(self) -> None:
        # "text/*" is registered as a wildcard
        parser = get_parser("text/x-unknown-subtype")
        assert parser is not None

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="No parser registered"):
            get_parser("application/x-nonexistent-test-type")

    def test_pdf_registered(self) -> None:
        assert get_parser("application/pdf") is not None

    def test_image_registered(self) -> None:
        assert get_parser("image/png") is not None
