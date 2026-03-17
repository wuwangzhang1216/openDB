"""Unit tests for the directory watch service."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.watch_service import (
    WatchEntry,
    _IngestHandler,
    _watchers,
    _watchers_lock,
    get_watch,
    list_watches,
    start_watch,
    stop_all,
    stop_watch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cleanup_watchers():
    """Remove all watchers without joining observers (test cleanup)."""
    stop_all()


# ---------------------------------------------------------------------------
# WatchEntry dataclass
# ---------------------------------------------------------------------------


class TestWatchEntry:
    def test_defaults(self):
        from watchdog.observers import Observer
        obs = Observer()
        entry = WatchEntry(
            id="abc123",
            path=Path("/tmp/test"),
            tags=["ops"],
            metadata=None,
            observer=obs,
        )
        assert entry.id == "abc123"
        assert entry.ingested == 0
        assert entry.failed == 0
        assert entry.skipped == 0
        assert entry.created_at > 0
        obs.stop()


# ---------------------------------------------------------------------------
# _IngestHandler debounce logic
# ---------------------------------------------------------------------------


class TestIngestHandlerDebounce:
    def test_first_event_passes(self):
        handler = _IngestHandler.__new__(_IngestHandler)
        handler._last_seen = {}
        handler._lock = __import__("threading").Lock()
        assert handler._should_process("/some/file.txt") is True

    def test_rapid_duplicate_blocked(self):
        handler = _IngestHandler.__new__(_IngestHandler)
        handler._last_seen = {"/file.txt": time.time()}
        handler._lock = __import__("threading").Lock()
        assert handler._should_process("/file.txt") is False

    def test_after_debounce_window_passes(self):
        handler = _IngestHandler.__new__(_IngestHandler)
        handler._last_seen = {"/file.txt": time.time() - 5.0}
        handler._lock = __import__("threading").Lock()
        assert handler._should_process("/file.txt") is True


# ---------------------------------------------------------------------------
# start_watch / stop_watch / list / get
# ---------------------------------------------------------------------------


class TestWatchLifecycle:
    @pytest.fixture(autouse=True)
    def cleanup(self):
        yield
        _cleanup_watchers()

    def test_start_and_list(self, tmp_path: Path):
        loop = asyncio.new_event_loop()
        try:
            watch_id = start_watch(tmp_path, tags=["test"], loop=loop)
            assert isinstance(watch_id, str)
            assert len(watch_id) == 12

            watches = list_watches()
            assert len(watches) == 1
            assert watches[0]["id"] == watch_id
            assert watches[0]["path"] == str(tmp_path)
            assert watches[0]["tags"] == ["test"]
            assert watches[0]["ingested"] == 0
        finally:
            loop.close()

    def test_get_watch(self, tmp_path: Path):
        loop = asyncio.new_event_loop()
        try:
            watch_id = start_watch(tmp_path, loop=loop)
            info = get_watch(watch_id)
            assert info is not None
            assert info["id"] == watch_id
            assert info["path"] == str(tmp_path)
        finally:
            loop.close()

    def test_get_nonexistent_returns_none(self):
        assert get_watch("nonexistent") is None

    def test_stop_watch(self, tmp_path: Path):
        loop = asyncio.new_event_loop()
        try:
            watch_id = start_watch(tmp_path, loop=loop)
            assert stop_watch(watch_id) is True
            assert get_watch(watch_id) is None
            assert list_watches() == []
        finally:
            loop.close()

    def test_stop_nonexistent_returns_false(self):
        assert stop_watch("nonexistent") is False

    def test_duplicate_watch_returns_same_id(self, tmp_path: Path):
        loop = asyncio.new_event_loop()
        try:
            id1 = start_watch(tmp_path, loop=loop)
            id2 = start_watch(tmp_path, loop=loop)
            assert id1 == id2
            assert len(list_watches()) == 1
        finally:
            loop.close()

    def test_stop_all(self, tmp_path: Path):
        loop = asyncio.new_event_loop()
        try:
            d1 = tmp_path / "a"
            d2 = tmp_path / "b"
            d1.mkdir()
            d2.mkdir()
            start_watch(d1, loop=loop)
            start_watch(d2, loop=loop)
            assert len(list_watches()) == 2

            stop_all()
            assert list_watches() == []
        finally:
            loop.close()

    def test_max_watchers_enforced(self, tmp_path: Path):
        loop = asyncio.new_event_loop()
        try:
            with patch("app.services.watch_service.settings") as mock_settings:
                mock_settings.watch_max_watchers = 2
                mock_settings.index_exclude_patterns = []

                d1 = tmp_path / "a"
                d2 = tmp_path / "b"
                d3 = tmp_path / "c"
                d1.mkdir()
                d2.mkdir()
                d3.mkdir()

                start_watch(d1, loop=loop)
                start_watch(d2, loop=loop)

                with pytest.raises(ValueError, match="Maximum number of watchers"):
                    start_watch(d3, loop=loop)
        finally:
            loop.close()
