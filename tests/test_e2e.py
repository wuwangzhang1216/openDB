"""End-to-end tests: full pipeline through Workspace API.

Tests the complete flow: init workspace -> index files -> search -> read -> memory.
Uses SQLite embedded mode (no PostgreSQL required).
"""

import asyncio
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def sample_dir(tmp_path):
    """Create a directory with sample text files."""
    (tmp_path / "report.txt").write_text(
        "Quarterly Revenue Report\n\n"
        "Revenue for Q4 2024 exceeded expectations at $450M.\n"
        "This represents a 15% increase over Q3.\n"
        "Key drivers include cloud services and enterprise sales.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.txt").write_text(
        "Meeting Notes - Product Team\n\n"
        "Discussed roadmap for 2025.\n"
        "Priority items: performance optimization, CJK search support.\n"
        "Next review scheduled for March.\n",
        encoding="utf-8",
    )
    (tmp_path / "chinese.txt").write_text(
        "产品需求文档\n\n"
        "本文档描述了2025年的产品路线图。\n"
        "重点包括性能优化和中文搜索支持。\n"
        "预计在三月份完成第一阶段。\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
async def workspace(tmp_path):
    """Create a temporary workspace using the Workspace class."""
    from opendb_core.workspace import Workspace

    ws = Workspace(root=tmp_path)
    await ws.init()
    yield ws
    await ws.close()


class TestE2EPipeline:
    @pytest.mark.asyncio
    async def test_index_search_read(self, workspace, sample_dir):
        """Full pipeline: index -> search -> read."""
        # Index the sample directory
        result = await workspace.index(str(sample_dir))
        assert result["ingested"] >= 2  # at least 2 files indexed

        # Search for English content
        search = await workspace.search("revenue quarterly")
        assert search["total"] > 0
        found_files = {r["filename"] for r in search["results"]}
        assert "report.txt" in found_files

        # Read a specific file
        read = await workspace.read("report.txt")
        assert "450M" in read

    @pytest.mark.asyncio
    async def test_cjk_search(self, workspace, sample_dir):
        """CJK content can be indexed and searched."""
        await workspace.index(str(sample_dir))

        search = await workspace.search("产品路线图")
        assert search["total"] > 0
        found_files = {r["filename"] for r in search["results"]}
        assert "chinese.txt" in found_files

    @pytest.mark.asyncio
    async def test_memory_lifecycle(self, workspace):
        """Store, recall, and forget memories."""
        # Store
        mem = await workspace.memory_store(
            "The user prefers dark mode in all editors",
            memory_type="semantic",
        )
        assert mem["memory_id"]

        # Recall
        results = await workspace.memory_recall("dark mode preference")
        assert results["total"] > 0
        assert "dark mode" in results["results"][0]["content"]

        # Forget
        forgot = await workspace.memory_forget(memory_id=mem["memory_id"])
        assert forgot["deleted"] == 1

        # Verify gone
        results = await workspace.memory_recall("dark mode preference")
        assert results["total"] == 0

    @pytest.mark.asyncio
    async def test_memory_cjk(self, workspace):
        """CJK memory store and recall."""
        await workspace.memory_store(
            "用户偏好使用深色主题",
            memory_type="semantic",
        )
        results = await workspace.memory_recall("深色主题")
        assert results["total"] > 0
        assert "深色" in results["results"][0]["content"]

    @pytest.mark.asyncio
    async def test_incremental_reindex(self, workspace, sample_dir):
        """Re-indexing skips unchanged files."""
        result1 = await workspace.index(str(sample_dir))
        ingested1 = result1["ingested"]

        # Re-index same directory — should skip all
        result2 = await workspace.index(str(sample_dir))
        assert result2["skipped"] >= ingested1
        assert result2["ingested"] == 0


class TestSharedHelpers:
    def test_build_highlight(self):
        from opendb_core.storage.shared import build_highlight

        text = "The quick brown fox jumps over the lazy dog near the river"
        hl = build_highlight(text, "fox")
        assert "fox" in hl

    def test_build_highlight_no_match(self):
        from opendb_core.storage.shared import build_highlight

        text = "Hello world"
        hl = build_highlight(text, "nonexistent")
        assert hl == text[:150]

    def test_escape_fts5_basic(self):
        from opendb_core.storage.shared import escape_fts5

        result = escape_fts5("hello world")
        assert '"hello"' in result
        assert '"world"' in result

    def test_escape_fts5_or_mode(self):
        from opendb_core.storage.shared import escape_fts5

        result = escape_fts5("running quickly", use_or=True)
        assert "OR" in result

    def test_tokenize_for_fts_latin(self):
        from opendb_core.utils.tokenizer import tokenize_for_fts

        result = tokenize_for_fts("hello world")
        assert result == "hello world"

    def test_tokenize_for_fts_cjk(self):
        from opendb_core.utils.tokenizer import tokenize_for_fts

        result = tokenize_for_fts("今天天气很好")
        # Should be segmented with spaces
        assert " " in result
        assert "天气" in result

    def test_tokenize_for_fts_hyphen(self):
        from opendb_core.utils.tokenizer import tokenize_for_fts

        result = tokenize_for_fts("gardening-related tips")
        assert "gardening" in result
        assert "related" in result
