from opendb_core.parsers.base import Page
from opendb_core.utils.text import (
    assemble_text,
    build_line_index,
    extract_lines,
    format_page_marker,
    grep_with_context,
)


class TestBuildLineIndex:
    def test_single_line(self) -> None:
        index = build_line_index("hello")
        assert index == [0]

    def test_multiple_lines(self) -> None:
        text = "line1\nline2\nline3"
        index = build_line_index(text)
        assert index == [0, 6, 12]

    def test_empty_string(self) -> None:
        index = build_line_index("")
        assert index == [0]

    def test_trailing_newline(self) -> None:
        text = "a\nb\n"
        index = build_line_index(text)
        assert index == [0, 2, 4]


class TestExtractLines:
    def setup_method(self) -> None:
        self.text = "line1\nline2\nline3\nline4\nline5"
        self.index = build_line_index(self.text)

    def test_extract_single_line(self) -> None:
        result = extract_lines(self.text, self.index, 1, 1)
        assert result == "line1"

    def test_extract_range(self) -> None:
        result = extract_lines(self.text, self.index, 2, 4)
        assert result == "line2\nline3\nline4"

    def test_extract_last_line(self) -> None:
        result = extract_lines(self.text, self.index, 5, 5)
        assert result == "line5"

    def test_extract_all(self) -> None:
        result = extract_lines(self.text, self.index, 1, 5)
        assert result == self.text

    def test_extract_beyond_end(self) -> None:
        result = extract_lines(self.text, self.index, 4, 100)
        assert result == "line4\nline5"


class TestGrepWithContext:
    def setup_method(self) -> None:
        self.text = (
            "[Page 1]\n"
            "\n"
            "Introduction to the report\n"
            "This document covers revenue data\n"
            "for the fiscal year 2024.\n"
            "\n"
            "[Page 2]\n"
            "\n"
            "Revenue exceeded the target of $450M.\n"
            "Management attributes this to Q4.\n"
            "Strong performance across segments.\n"
            "\n"
            "The 2025 revenue target is $600M."
        )

    def test_single_term(self) -> None:
        result = grep_with_context(self.text, "revenue", context=1)
        assert "revenue" in result.lower()
        assert "[Page" in result or "[Line" in result

    def test_multi_term(self) -> None:
        result = grep_with_context(self.text, "revenue+target", context=0)
        assert "revenue" in result.lower()
        assert "target" in result.lower()
        # Line 4 "revenue data" should NOT match (doesn't contain "target")
        assert "data" not in result.lower() or "target" in result.lower()

    def test_no_match(self) -> None:
        result = grep_with_context(self.text, "nonexistent", context=2)
        assert result == ""

    def test_case_insensitive(self) -> None:
        result = grep_with_context(self.text, "REVENUE", context=0)
        assert result != ""


class TestFormatPageMarker:
    def test_pdf_page(self) -> None:
        page = Page(page_number=3, section_title="Revenue", text="")
        assert format_page_marker(page) == "[Page 3]"

    def test_pptx_slide(self) -> None:
        page = Page(page_number=1, section_title="Title", text="")
        marker = format_page_marker(page, "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        assert marker == "[Slide 1]"

    def test_xlsx_sheet_with_name(self) -> None:
        page = Page(page_number=1, section_title="Revenue", text="")
        marker = format_page_marker(page, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert marker == "[Sheet: Revenue]"


class TestAssembleText:
    def test_basic_assembly(self) -> None:
        pages = [
            Page(page_number=1, section_title="Intro", text="Hello world\nThis is page one"),
            Page(page_number=2, section_title="Body", text="Page two content"),
        ]
        full_text, line_index, toc, page_ranges = assemble_text(pages)

        # Check page markers present
        assert "[Page 1]" in full_text
        assert "[Page 2]" in full_text

        # Check content present
        assert "Hello world" in full_text
        assert "Page two content" in full_text

        # Check TOC
        assert "Intro" in toc
        assert "Body" in toc

        # Check page ranges are valid
        assert len(page_ranges) == 2
        for start, end in page_ranges:
            assert start <= end
            assert start >= 1

    def test_line_index_consistency(self) -> None:
        pages = [
            Page(page_number=1, section_title=None, text="line a\nline b"),
        ]
        full_text, line_index, _, _ = assemble_text(pages)

        # Each index should point to the start of a line
        lines = full_text.split("\n")
        for i, offset in enumerate(line_index):
            if i < len(lines):
                remaining = full_text[offset:]
                assert remaining.startswith(lines[i])
