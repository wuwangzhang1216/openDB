"""Tests for lightweight code symbols, import links, and context bundles."""

from __future__ import annotations

import pytest

from opendb_core.parsers.base import Page, ParseResult
from opendb_core.services.context_service import build_context
from opendb_core.storage import close_backend, get_backend, init_backend
from opendb_core.storage.sqlite import SQLiteBackend
from opendb_core.utils.text import assemble_text
from opendb_core.utils.code_intel import extract_code_intel


def _parse_code(text: str) -> tuple[ParseResult, str, list[int], list[tuple[int, int]]]:
    result = ParseResult(pages=[Page(page_number=1, section_title=None, text=text)])
    full_text, line_index, _toc, page_line_ranges = assemble_text(result.pages, "text/x-python")
    return result, full_text, line_index, page_line_ranges


async def _ingest_code(
    backend: SQLiteBackend,
    *,
    file_id: str,
    filename: str,
    source_path: str,
    text: str,
) -> None:
    parse_result, full_text, line_index, page_line_ranges = _parse_code(text)
    await backend.persist_ingestion(
        file_id=file_id,
        file_path=f"/tmp/{filename}",
        original_filename=filename,
        mime_type="text/x-python",
        file_size=len(text),
        checksum=f"checksum-{file_id}",
        tags=[],
        merged_metadata={"source_path": source_path},
        parse_result=parse_result,
        full_text=full_text,
        total_lines=len(line_index),
        line_index=line_index,
        toc="",
        page_line_ranges=page_line_ranges,
    )


@pytest.fixture
async def backend(tmp_path):
    db_path = tmp_path / "code-context.db"
    b = SQLiteBackend(db_path=db_path)
    await b.init()
    yield b
    await b.close()


def test_extracts_python_symbols_and_import_links() -> None:
    symbols, links = extract_code_intel(
        "from opendb_core.storage.sqlite import SQLiteBackend\n\n"
        "class SearchService:\n"
        "    def run(self, query):\n"
        "        return query\n",
        filename="service.py",
        source_path="/workspace/app/service.py",
    )

    assert {s["qualified_name"] for s in symbols} == {
        "SearchService",
        "SearchService.run",
    }
    assert "opendb_core/storage/sqlite.py" in {link["target"] for link in links}


class TestCodeContext:
    @pytest.mark.asyncio
    async def test_ingest_indexes_symbols_for_targeted_lookup(self, backend) -> None:
        await _ingest_code(
            backend,
            file_id="worker",
            filename="worker.py",
            source_path="/workspace/pkg/worker.py",
            text=(
                "def do_work(item):\n"
                "    \"\"\"Process one queued item.\"\"\"\n"
                "    return item.upper()\n"
            ),
        )

        rows = await backend.search_code_symbols("do_work", limit=5)

        assert len(rows) == 1
        assert rows[0]["qualified_name"] == "do_work"
        assert rows[0]["source_path"] == "/workspace/pkg/worker.py"

        await backend.delete_file("worker")
        assert await backend.search_code_symbols("do_work", limit=5) == []

    @pytest.mark.asyncio
    async def test_python_imports_feed_existing_backlink_graph(self, backend) -> None:
        await _ingest_code(
            backend,
            file_id="worker",
            filename="worker.py",
            source_path="/workspace/pkg/worker.py",
            text="def do_work(item):\n    return item\n",
        )
        await _ingest_code(
            backend,
            file_id="runner",
            filename="runner.py",
            source_path="/workspace/pkg/runner.py",
            text="from pkg.worker import do_work\n\nresult = do_work('x')\n",
        )

        assert await backend.get_backlink_counts(["worker"]) == {"worker": 1}

    @pytest.mark.asyncio
    async def test_context_returns_symbol_snippets_without_reading_whole_file(self, tmp_path) -> None:
        db_path = tmp_path / "context-service.db"
        await init_backend("sqlite", db_path=db_path)
        try:
            backend = get_backend()
            await _ingest_code(
                backend,
                file_id="worker",
                filename="worker.py",
                source_path="/workspace/pkg/worker.py",
                text=(
                    "def do_work(item):\n"
                    "    \"\"\"Process one queued item.\"\"\"\n"
                    "    return item.upper()\n\n"
                    "def unrelated():\n"
                    "    return 'noise'\n"
                ),
            )

            result = await build_context("do_work", limit=3)
        finally:
            await close_backend(key=str(db_path))

        assert result["stats"]["symbol_count"] == 1
        assert result["symbols"][0]["qualified_name"] == "do_work"
        assert "def do_work" in result["snippets"][0]["text"]
        assert "def unrelated" not in result["snippets"][0]["text"]

    @pytest.mark.asyncio
    async def test_init_backfills_symbols_for_existing_indexed_code(self, tmp_path) -> None:
        db_path = tmp_path / "upgrade.db"
        text = "from pkg.worker import do_work\n\ndef old_symbol():\n    return do_work('x')\n"
        parse_result, full_text, line_index, page_line_ranges = _parse_code(text)

        backend = SQLiteBackend(db_path=db_path)
        await backend.init()
        try:
            await backend._db.execute(
                """
                INSERT INTO files
                    (id, filename, mime_type, file_size, file_path, checksum, status, tags, metadata)
                VALUES (?, ?, ?, ?, ?, ?, 'ready', '[]', ?)
                """,
                (
                    "old-runner",
                    "runner.py",
                    "text/x-python",
                    len(text),
                    "/tmp/runner.py",
                    "checksum-old-runner",
                    '{"source_path": "/workspace/pkg/runner.py"}',
                ),
            )
            await backend._db.execute(
                "INSERT INTO file_text (file_id, full_text, total_lines, line_index, toc) "
                "VALUES (?, ?, ?, ?, '')",
                ("old-runner", full_text, len(line_index), str(line_index)),
            )
            await backend._db.execute(
                """
                INSERT INTO pages (file_id, page_number, text, line_start, line_end)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "old-runner",
                    1,
                    parse_result.pages[0].text,
                    page_line_ranges[0][0],
                    page_line_ranges[0][1],
                ),
            )
            await backend._db.execute(
                """
                INSERT INTO file_links (from_file_id, target, link_type, context)
                VALUES (?, ?, 'markdown', 'existing link')
                """,
                ("old-runner", "README.md"),
            )
            await backend._db.commit()
        finally:
            await backend.close()

        reopened = SQLiteBackend(db_path=db_path)
        await reopened.init()
        try:
            rows = await reopened.search_code_symbols("old_symbol", limit=5)
            async with reopened._db.execute(
                "SELECT target, link_type FROM file_links WHERE from_file_id = ?",
                ("old-runner",),
            ) as cur:
                links = {(row["target"], row["link_type"]) for row in await cur.fetchall()}
        finally:
            await reopened.close()

        assert [row["qualified_name"] for row in rows] == ["old_symbol"]
        assert ("README.md", "markdown") in links
        assert ("pkg/worker.py", "import") in links
