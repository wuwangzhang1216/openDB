"""Integration tests for the SQLite storage backend.

Tests the full SQLite backend lifecycle: init, ingest, search, memory,
CJK search, and incremental re-indexing.
"""


import pytest

from opendb_core.storage.sqlite import SQLiteBackend
from opendb_core.parsers.base import Page, ParseResult


@pytest.fixture
async def backend(tmp_path) -> None:
    """Create a temporary SQLite backend."""
    db_path = tmp_path / "test.db"
    b = SQLiteBackend(db_path=db_path)
    await b.init()
    yield b
    await b.close()


def _make_parse_result(pages: list[Page]) -> ParseResult:
    return ParseResult(pages=pages)


class TestSQLiteIngestion:
    @pytest.mark.asyncio
    async def test_ingest_and_read(self, backend) -> None:
        pages = [Page(page_number=1, section_title="Intro", text="Hello world")]
        result = await backend.persist_ingestion(
            file_id="aaaa-bbbb-cccc-dddd",
            file_path="/tmp/test.txt",
            original_filename="test.txt",
            mime_type="text/plain",
            file_size=11,
            checksum="abc123",
            tags=["test"],
            merged_metadata={"source_path": "/tmp/test.txt"},
            parse_result=_make_parse_result(pages),
            full_text="[Page 1]\nHello world",
            total_lines=2,
            line_index=[0, 9],
            toc="1. Intro",
            page_line_ranges=[(1, 2)],
        )
        assert result["status"] == "ready"
        assert result["filename"] == "test.txt"

        # Read back
        text = await backend.get_file_text("aaaa-bbbb-cccc-dddd")
        assert "Hello world" in text["full_text"]

    @pytest.mark.asyncio
    async def test_duplicate_detection(self, backend) -> None:
        pages = [Page(page_number=1, section_title=None, text="Content")]
        kwargs = dict(
            file_path="/tmp/dup.txt",
            original_filename="dup.txt",
            mime_type="text/plain",
            file_size=7,
            checksum="same_hash",
            tags=[],
            merged_metadata={},
            parse_result=_make_parse_result(pages),
            full_text="Content",
            total_lines=1,
            line_index=[0],
            toc="",
            page_line_ranges=[(1, 1)],
        )
        await backend.persist_ingestion(file_id="id-1", **kwargs)
        result = await backend.persist_ingestion(file_id="id-2", **kwargs)
        assert result["status"] == "duplicate"


class TestSQLiteSearch:
    @pytest.mark.asyncio
    async def test_fts_search_english(self, backend) -> None:
        pages = [Page(page_number=1, section_title=None, text="Revenue exceeded target of 450 million")]
        await backend.persist_ingestion(
            file_id="search-1",
            file_path="/tmp/report.txt",
            original_filename="report.txt",
            mime_type="text/plain",
            file_size=100,
            checksum="search1",
            tags=[],
            merged_metadata={},
            parse_result=_make_parse_result(pages),
            full_text="Revenue exceeded target of 450 million",
            total_lines=1,
            line_index=[0],
            toc="",
            page_line_ranges=[(1, 1)],
        )
        result = await backend.search_fts("revenue target", {}, 10, 0)
        assert result["total"] > 0
        assert "report.txt" in result["results"][0]["filename"]

    @pytest.mark.asyncio
    async def test_fts_search_cjk(self, backend) -> None:
        pages = [Page(page_number=1, section_title=None, text="今天天气很好，适合出去散步")]
        await backend.persist_ingestion(
            file_id="cjk-1",
            file_path="/tmp/chinese.txt",
            original_filename="chinese.txt",
            mime_type="text/plain",
            file_size=100,
            checksum="cjk1",
            tags=[],
            merged_metadata={},
            parse_result=_make_parse_result(pages),
            full_text="今天天气很好，适合出去散步",
            total_lines=1,
            line_index=[0],
            toc="",
            page_line_ranges=[(1, 1)],
        )
        result = await backend.search_fts("天气", {}, 10, 0)
        assert result["total"] > 0
        assert "chinese.txt" in result["results"][0]["filename"]

    @pytest.mark.asyncio
    async def test_search_no_results(self, backend) -> None:
        result = await backend.search_fts("nonexistent", {}, 10, 0)
        assert result["total"] == 0
        assert result["results"] == []


class TestSQLiteMemory:
    @pytest.mark.asyncio
    async def test_store_and_recall(self, backend) -> None:
        await backend.store_memory(
            memory_id="mem-1",
            content="User prefers dark mode",
            memory_type="semantic",
            tags=["preference"],
            metadata={},
            pinned=False,
        )
        result = await backend.recall_memories(
            query="dark mode",
            memory_type=None,
            tags=None,
            limit=10,
            offset=0,
        )
        assert result["total"] > 0
        assert "dark mode" in result["results"][0]["content"]

    @pytest.mark.asyncio
    async def test_store_and_recall_cjk(self, backend) -> None:
        await backend.store_memory(
            memory_id="mem-cjk-1",
            content="用户喜欢使用中文界面",
            memory_type="semantic",
            tags=[],
            metadata={},
        )
        result = await backend.recall_memories(
            query="中文界面",
            memory_type=None,
            tags=None,
            limit=10,
            offset=0,
        )
        assert result["total"] > 0
        assert "中文" in result["results"][0]["content"]

    @pytest.mark.asyncio
    async def test_pinned_recall(self, backend) -> None:
        await backend.store_memory(
            memory_id="mem-pin",
            content="Critical system configuration",
            memory_type="procedural",
            tags=[],
            metadata={},
            pinned=True,
        )
        result = await backend.recall_memories(
            query="",
            memory_type=None,
            tags=None,
            limit=10,
            offset=0,
            pinned_only=True,
        )
        assert result["total"] > 0
        assert result["results"][0]["pinned"] is True

    @pytest.mark.asyncio
    async def test_forget_memory(self, backend) -> None:
        await backend.store_memory(
            memory_id="mem-forget",
            content="Temporary note",
            memory_type="episodic",
            tags=[],
            metadata={},
        )
        deleted = await backend.delete_memory("mem-forget")
        assert deleted is True
        mem = await backend.get_memory("mem-forget")
        assert mem is None


    @pytest.mark.asyncio
    async def test_knowledge_update_supersede(self, backend) -> None:
        """Storing a memory that overlaps an existing one should supersede it."""
        await backend.store_memory(
            memory_id="mem-orig",
            content="My favorite color is blue.",
            memory_type="semantic",
            tags=["fact"],
            metadata={},
        )
        await backend.store_memory(
            memory_id="mem-updated",
            content="My favorite color is green. I changed it from blue.",
            memory_type="semantic",
            tags=["fact"],
            metadata={},
        )
        # Should have superseded the original — only 1 memory in DB
        all_mems = await backend.list_memories(
            memory_type="semantic", tags=None, limit=100, offset=0,
        )
        color_mems = [
            m for m in all_mems["memories"] if "favorite color" in m["content"]
        ]
        assert len(color_mems) == 1, f"Expected 1 memory, got {len(color_mems)}"
        assert "green" in color_mems[0]["content"]

        # Recall should return the updated version
        result = await backend.recall_memories(
            query="favorite color",
            memory_type=None, tags=None, limit=5, offset=0,
        )
        assert result["total"] > 0
        assert "green" in result["results"][0]["content"]

    @pytest.mark.asyncio
    async def test_recall_prefers_metadata_date(self, backend) -> None:
        """Memories with newer metadata dates should rank higher."""
        await backend.store_memory(
            memory_id="old-event",
            content="Bought a Honda Civic.",
            memory_type="episodic",
            tags=[],
            metadata={"date": "2023-06-01"},
        )
        await backend.store_memory(
            memory_id="new-event",
            content="Bought a Tesla Model Y.",
            memory_type="episodic",
            tags=[],
            metadata={"date": "2024-02-01"},
        )
        result = await backend.recall_memories(
            query="Bought car",
            memory_type=None, tags=None, limit=5, offset=0,
        )
        assert len(result["results"]) >= 2
        assert "Tesla" in result["results"][0]["content"]

    @pytest.mark.asyncio
    async def test_no_false_supersede(self, backend) -> None:
        """Unrelated memories should NOT be superseded."""
        await backend.store_memory(
            memory_id="mem-a",
            content="I love Python programming.",
            memory_type="semantic",
            tags=[],
            metadata={},
        )
        await backend.store_memory(
            memory_id="mem-b",
            content="I love hiking in the mountains.",
            memory_type="semantic",
            tags=[],
            metadata={},
        )
        all_mems = await backend.list_memories(
            memory_type="semantic", tags=None, limit=100, offset=0,
        )
        assert all_mems["total"] == 2


class TestSQLiteFileCRUD:
    @pytest.mark.asyncio
    async def test_list_and_delete(self, backend) -> None:
        pages = [Page(page_number=1, section_title=None, text="Deletable")]
        await backend.persist_ingestion(
            file_id="del-1",
            file_path="/tmp/deleteme.txt",
            original_filename="deleteme.txt",
            mime_type="text/plain",
            file_size=9,
            checksum="del1",
            tags=[],
            merged_metadata={},
            parse_result=_make_parse_result(pages),
            full_text="Deletable",
            total_lines=1,
            line_index=[0],
            toc="",
            page_line_ranges=[(1, 1)],
        )
        files = await backend.list_files({}, "created_at", "DESC", 10, 0)
        assert files["total"] >= 1

        path = await backend.delete_file("del-1")
        assert path is not None

        file = await backend.get_file_by_id("del-1")
        assert file is None

    @pytest.mark.asyncio
    async def test_workspace_stats(self, backend) -> None:
        stats = await backend.get_workspace_stats()
        assert "by_status" in stats
        assert "memory" in stats
