"""Unit tests for the ingestion pipeline.

These tests mock the database layer and exercise the orchestration logic
in ingest_service.py, including error paths and duplicate handling.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services.ingest_service import ingest_file


def _make_mock_pool(fetchrow_return=None):
    """Create a mock pool with a properly wired async context manager for acquire()."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.execute = AsyncMock()

    # Make conn.transaction() return an async context manager
    @asynccontextmanager
    async def mock_transaction():
        yield

    mock_conn.transaction = mock_transaction

    # Make pool.acquire() return an async context manager
    @asynccontextmanager
    async def mock_acquire():
        yield mock_conn

    mock_pool = AsyncMock()
    mock_pool.acquire = mock_acquire

    return mock_pool, mock_conn


class TestIngestFileValidation:
    @pytest.mark.asyncio
    async def test_rejects_oversized_file(self):
        """Files exceeding max_file_size are rejected immediately."""
        content = b"x" * (settings.max_file_size + 1)
        with pytest.raises(ValueError, match="exceeds max"):
            await ingest_file(content, "big.txt", "text/plain")


class TestIngestFileDuplicate:
    @pytest.mark.asyncio
    async def test_duplicate_detected_by_checksum(self, tmp_path: Path):
        """When a file with matching checksum exists, return duplicate status."""
        content = b"hello duplicate world"
        mock_pool, _ = _make_mock_pool(
            fetchrow_return={"id": "existing-uuid", "filename": "original.txt"}
        )

        async def fake_get_pool():
            return mock_pool

        with patch("app.services.ingest_service.get_pool", side_effect=fake_get_pool):
            with patch("app.services.ingest_service.settings") as mock_settings:
                mock_settings.max_file_size = settings.max_file_size
                mock_settings.file_storage_path = tmp_path
                mock_settings.ocr_enabled = False
                mock_settings.ocr_languages = settings.ocr_languages

                result = await ingest_file(content, "test.txt", "text/plain")

        assert result["status"] == "duplicate"
        assert result["id"] == "existing-uuid"


class TestIngestFileCleanup:
    @pytest.mark.asyncio
    async def test_cleans_up_on_parse_failure(self, tmp_path: Path):
        """When parsing fails, file directory is cleaned up."""
        content = b"some content"
        mock_pool, _ = _make_mock_pool(fetchrow_return=None)

        async def fake_get_pool():
            return mock_pool

        with patch("app.services.ingest_service.get_pool", side_effect=fake_get_pool):
            with patch("app.services.ingest_service.settings") as mock_settings:
                mock_settings.max_file_size = settings.max_file_size
                mock_settings.file_storage_path = tmp_path
                mock_settings.ocr_enabled = False
                mock_settings.ocr_languages = settings.ocr_languages

                with patch(
                    "app.services.ingest_service.parse_file",
                    side_effect=RuntimeError("parse boom"),
                ):
                    with pytest.raises(RuntimeError, match="parse boom"):
                        await ingest_file(content, "bad.txt", "text/plain")

        # All file_id-based directories should be cleaned up
        remaining_dirs = list(tmp_path.iterdir())
        assert len(remaining_dirs) == 0, f"Expected cleanup, but found: {remaining_dirs}"
