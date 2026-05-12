"""Tests for opt-in eval capture and lightweight file links."""

from __future__ import annotations

import pytest

from opendb_core.config import settings
from opendb_core.parsers.base import Page, ParseResult
from opendb_core.services.search_service import search_files
from opendb_core.storage import close_backend, get_backend, init_backend
from opendb_core.storage.sqlite import SQLiteBackend
from opendb_core.utils.link_extractor import extract_file_links


@pytest.fixture
async def backend(tmp_path):
    db_path = tmp_path / "test.db"
    b = SQLiteBackend(db_path=db_path)
    await b.init()
    yield b
    await b.close()


def _parse(text: str) -> ParseResult:
    return ParseResult(pages=[Page(page_number=1, section_title=None, text=text)])


async def _ingest(
    backend: SQLiteBackend,
    *,
    file_id: str,
    filename: str,
    source_path: str,
    text: str,
) -> None:
    await backend.persist_ingestion(
        file_id=file_id,
        file_path=f"/tmp/{filename}",
        original_filename=filename,
        mime_type="text/markdown",
        file_size=len(text),
        checksum=f"checksum-{file_id}",
        tags=[],
        merged_metadata={"source_path": source_path},
        parse_result=_parse(text),
        full_text=text,
        total_lines=text.count("\n") + 1,
        line_index=[0],
        toc="",
        page_line_ranges=[(1, text.count("\n") + 1)],
    )


class TestEvalCapture:
    @pytest.mark.asyncio
    async def test_log_and_export_eval_capture(self, backend) -> None:
        await backend.log_eval_capture(
            tool_name="search",
            query="revenue target",
            result_ids=["file-1"],
            result_count=1,
            latency_ms=7,
            metadata={"limit": 10},
        )

        rows = await backend.export_eval_captures(limit=10)

        assert len(rows) == 1
        assert rows[0]["tool_name"] == "search"
        assert rows[0]["query"] == "revenue target"
        assert rows[0]["result_ids"] == ["file-1"]
        assert rows[0]["metadata"] == {"limit": 10}


class TestFileLinks:
    def test_extracts_local_markdown_wiki_and_path_links(self) -> None:
        links = extract_file_links(
            "See [Guide](guide.md), [[notes/today]], [Root](../README.md), and src/app.py:12.",
            source_path="/workspace/docs/current.md",
        )

        targets = {link["target"] for link in links}
        assert "/workspace/docs/guide.md" in targets
        assert "/workspace/docs/notes/today.md" in targets
        assert "/workspace/README.md" in targets
        assert "src/app.py" in targets

    @pytest.mark.asyncio
    async def test_ingest_records_file_links_and_backlink_counts(self, backend) -> None:
        await _ingest(
            backend,
            file_id="target",
            filename="target.md",
            source_path="/workspace/docs/target.md",
            text="Needle target content",
        )
        await _ingest(
            backend,
            file_id="source",
            filename="source.md",
            source_path="/workspace/docs/source.md",
            text="Read [Target](target.md) for more detail.",
        )

        counts = await backend.get_backlink_counts(["target"])

        assert counts == {"target": 1}

    @pytest.mark.asyncio
    async def test_later_target_ingest_reconciles_unresolved_links(self, backend) -> None:
        await _ingest(
            backend,
            file_id="source-first",
            filename="source-first.md",
            source_path="/workspace/docs/source-first.md",
            text="Read [Target](target-later.md) for more detail.",
        )
        assert await backend.get_backlink_counts(["target-later"]) == {}

        await _ingest(
            backend,
            file_id="target-later",
            filename="target-later.md",
            source_path="/workspace/docs/target-later.md",
            text="Needle target content",
        )

        assert await backend.get_backlink_counts(["target-later"]) == {"target-later": 1}

    @pytest.mark.asyncio
    async def test_reindexed_target_restores_inbound_links(self, backend) -> None:
        await _ingest(
            backend,
            file_id="target-old",
            filename="target.md",
            source_path="/workspace/docs/target.md",
            text="Needle target content",
        )
        await _ingest(
            backend,
            file_id="source-reindex",
            filename="source-reindex.md",
            source_path="/workspace/docs/source-reindex.md",
            text="Read [Target](target.md).",
        )
        assert await backend.get_backlink_counts(["target-old"]) == {"target-old": 1}

        await backend.delete_file("target-old")
        assert await backend.get_backlink_counts(["target-old"]) == {}

        await _ingest(
            backend,
            file_id="target-new",
            filename="target.md",
            source_path="/workspace/docs/target.md",
            text="Needle target content v2",
        )

        assert await backend.get_backlink_counts(["target-new"]) == {"target-new": 1}

    @pytest.mark.asyncio
    async def test_backlink_boost_is_opt_in(self, backend) -> None:
        await _ingest(
            backend,
            file_id="distractor-boost",
            filename="distractor-boost.md",
            source_path="/workspace/docs/distractor-boost.md",
            text="Needle ranking target",
        )
        await _ingest(
            backend,
            file_id="target-boost",
            filename="target-boost.md",
            source_path="/workspace/docs/target-boost.md",
            text="Needle ranking target",
        )
        await _ingest(
            backend,
            file_id="source-boost",
            filename="source-boost.md",
            source_path="/workspace/docs/source-boost.md",
            text="Read [Target](target-boost.md).",
        )

        old_enabled = settings.backlink_boost_enabled
        old_weight = settings.backlink_boost_weight
        try:
            settings.backlink_boost_enabled = False
            base = await backend.search_fts("needle", {}, 10, 0)

            settings.backlink_boost_enabled = True
            settings.backlink_boost_weight = 0.5
            boosted = await backend.search_fts("needle", {}, 10, 0)
        finally:
            settings.backlink_boost_enabled = old_enabled
            settings.backlink_boost_weight = old_weight

        assert base["results"][0]["file_id"] == "distractor-boost"
        assert boosted["results"][0]["file_id"] == "target-boost"

    @pytest.mark.asyncio
    async def test_service_search_captures_when_enabled(self, tmp_path) -> None:
        db_path = tmp_path / "service-capture.db"
        old_capture = settings.eval_capture_enabled
        try:
            await init_backend("sqlite", db_path=db_path)
            backend = get_backend()
            await _ingest(
                backend,
                file_id="capture-file",
                filename="capture.md",
                source_path="/workspace/docs/capture.md",
                text="Capture needle content",
            )

            settings.eval_capture_enabled = True
            result = await search_files("capture needle", limit=5)
            rows = await backend.export_eval_captures(limit=10)
        finally:
            settings.eval_capture_enabled = old_capture
            await close_backend(key=str(db_path))

        assert result["total"] == 1
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "search"
        assert rows[0]["query"] == "capture needle"
        assert rows[0]["result_ids"] == ["capture-file"]
