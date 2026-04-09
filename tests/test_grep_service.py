"""Unit tests for grep service — regex search across filesystem files."""

from pathlib import Path

import pytest

from opendb_core.services.grep_service import grep_files, _should_skip


# ---------------------------------------------------------------------------
# _should_skip helper
# ---------------------------------------------------------------------------

class TestShouldSkip:
    def test_git_directory(self):
        assert _should_skip(".git/objects/abc") is True

    def test_node_modules(self):
        assert _should_skip("node_modules/pkg/index.js") is True

    def test_pycache(self):
        assert _should_skip("src/__pycache__/mod.cpython-312.pyc") is True

    def test_binary_extension(self):
        assert _should_skip("images/logo.png") is True

    def test_archive_extension(self):
        assert _should_skip("dist/bundle.zip") is True

    def test_normal_python_file(self):
        assert _should_skip("src/main.py") is False

    def test_normal_text_file(self):
        assert _should_skip("docs/readme.txt") is False

    def test_nested_skip_dir(self):
        assert _should_skip("frontend/.next/cache/data.json") is True

    def test_pdf_skipped(self):
        assert _should_skip("reports/q4.pdf") is True

    def test_sqlite_skipped(self):
        assert _should_skip("data/metadata.sqlite") is True


# ---------------------------------------------------------------------------
# grep_files async function
# ---------------------------------------------------------------------------

class TestGrepFiles:
    @pytest.mark.asyncio
    async def test_basic_match(self, tmp_path: Path):
        (tmp_path / "hello.txt").write_text("Hello world\nGoodbye world\n")
        result = await grep_files("Hello", str(tmp_path))
        assert result["total"] == 1
        assert result["results"][0]["file"] == "hello.txt"
        assert result["results"][0]["line"] == 1
        assert result["results"][0]["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("nothing here\n")
        result = await grep_files("nonexistent", str(tmp_path))
        assert result["total"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_invalid_regex(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("text\n")
        result = await grep_files("[invalid", str(tmp_path))
        assert "error" in result
        assert "Invalid regex" in result["error"]

    @pytest.mark.asyncio
    async def test_directory_not_found(self):
        result = await grep_files("test", "/nonexistent/path/abc123")
        assert result["total"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_case_insensitive(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("HELLO world\nhello WORLD\n")
        result = await grep_files("hello", str(tmp_path), case_insensitive=True)
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_case_sensitive(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("HELLO world\nhello WORLD\n")
        result = await grep_files("hello", str(tmp_path), case_insensitive=False)
        assert result["total"] == 1
        assert result["results"][0]["line"] == 2

    @pytest.mark.asyncio
    async def test_context_lines(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("line1\nline2\nMATCH\nline4\nline5\n")
        result = await grep_files("MATCH", str(tmp_path), context=1)
        assert result["total"] == 1
        r = result["results"][0]
        assert r["context_before"] == ["line2"]
        assert r["context_after"] == ["line4"]

    @pytest.mark.asyncio
    async def test_max_results(self, tmp_path: Path):
        lines = "\n".join(f"match line {i}" for i in range(50))
        (tmp_path / "a.txt").write_text(lines)
        result = await grep_files("match", str(tmp_path), max_results=5)
        assert len(result["results"]) == 5
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_glob_filter(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("target\n")
        (tmp_path / "b.txt").write_text("target\n")
        result = await grep_files("target", str(tmp_path), glob="*.py")
        assert result["total"] == 1
        assert result["results"][0]["file"] == "a.py"

    @pytest.mark.asyncio
    async def test_skips_binary_files(self, tmp_path: Path):
        (tmp_path / "code.py").write_text("match here\n")
        (tmp_path / "image.png").write_bytes(b"match here\n")
        result = await grep_files("match", str(tmp_path))
        assert result["total"] == 1
        assert result["results"][0]["file"] == "code.py"

    @pytest.mark.asyncio
    async def test_skips_git_dir(self, tmp_path: Path):
        (tmp_path / "code.py").write_text("match\n")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("match\n")
        result = await grep_files("match", str(tmp_path))
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_regex_pattern(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("foo123bar\nfoobar\n")
        result = await grep_files(r"foo\d+bar", str(tmp_path))
        assert result["total"] == 1
        assert result["results"][0]["line"] == 1

    @pytest.mark.asyncio
    async def test_multiple_files(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("target line\n")
        (tmp_path / "b.txt").write_text("another target\n")
        result = await grep_files("target", str(tmp_path))
        assert result["total"] == 2
        files = {r["file"] for r in result["results"]}
        assert files == {"a.txt", "b.txt"}

    @pytest.mark.asyncio
    async def test_empty_directory(self, tmp_path: Path):
        result = await grep_files("test", str(tmp_path))
        assert result["total"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_line_numbers_are_1_indexed(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("first\nsecond\nthird\n")
        result = await grep_files("third", str(tmp_path))
        assert result["results"][0]["line"] == 3
