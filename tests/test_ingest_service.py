"""Unit tests for the ingestion pipeline.

These tests mock the storage backend and exercise the orchestration logic
in ingest_service.py, including error paths and duplicate handling.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opendb_core.config import settings
from opendb_core.services.ingest_service import ingest_file


def _make_mock_backend(check_duplicate_return=None):
    """Create a mock StorageBackend for unit testing."""
    mock = AsyncMock()
    mock.check_duplicate = AsyncMock(return_value=check_duplicate_return)
    mock.persist_ingestion = AsyncMock(return_value={
        "id": "new-uuid",
        "filename": "test.txt",
        "status": "ready",
    })
    mock.mark_file_failed = AsyncMock()
    return mock


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
        duplicate_record = {
            "id": "existing-uuid",
            "filename": "original.txt",
            "status": "duplicate",
            "detail": "File with identical content already exists",
        }
        mock_backend = _make_mock_backend(check_duplicate_return=duplicate_record)

        with patch("opendb_core.services.ingest_service.get_backend", return_value=mock_backend):
            with patch("opendb_core.services.ingest_service.settings") as mock_settings:
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
        mock_backend = _make_mock_backend(check_duplicate_return=None)

        with patch("opendb_core.services.ingest_service.get_backend", return_value=mock_backend):
            with patch("opendb_core.services.ingest_service.settings") as mock_settings:
                mock_settings.max_file_size = settings.max_file_size
                mock_settings.file_storage_path = tmp_path
                mock_settings.ocr_enabled = False
                mock_settings.ocr_languages = settings.ocr_languages

                with patch(
                    "opendb_core.services.ingest_service.parse_file",
                    side_effect=RuntimeError("parse boom"),
                ):
                    with pytest.raises(RuntimeError, match="parse boom"):
                        await ingest_file(content, "bad.txt", "text/plain")

        remaining_dirs = list(tmp_path.iterdir())
        assert len(remaining_dirs) == 0, f"Expected cleanup, but found: {remaining_dirs}"
