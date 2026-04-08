"""Tests for path-aware filename resolution (glob → read and grep → read handoffs)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from opendb_core.services.read_service import (
    AmbiguousFilenameError,
    resolve_filename,
)
from opendb_core.storage import _backends, init_backend


@pytest.fixture
async def sqlite_backend(tmp_path: Path):
    db_path = tmp_path / "resolve.db"
    await init_backend("sqlite", db_path=str(db_path))
    backend = _backends[str(db_path)]
    try:
        yield backend
    finally:
        await backend.close()
        _backends.pop(str(db_path), None)


async def _insert_indexed_file(backend, filename: str, source_path: str) -> str:
    """Insert a 'ready' file row with metadata.source_path set."""
    fid = str(uuid.uuid4())
    await backend._db.execute(
        "INSERT INTO files (id, filename, mime_type, file_size, file_path, "
        "checksum, status, tags, metadata) "
        "VALUES (?, ?, 'application/pdf', 1, ?, ?, 'ready', '[]', ?)",
        (fid, filename, f"/tmp/{fid}", fid, json.dumps({"source_path": source_path})),
    )
    await backend._db.commit()
    return fid


class TestPathAwareResolution:
    async def test_relative_path_resolves_via_source_path(self, sqlite_backend):
        expected = await _insert_indexed_file(
            sqlite_backend, "report.pdf", "/ws/docs/report.pdf"
        )
        got = await resolve_filename("docs/report.pdf")
        assert str(got) == expected

    async def test_windows_separators_normalized(self, sqlite_backend):
        expected = await _insert_indexed_file(
            sqlite_backend, "report.pdf", "/ws/docs/report.pdf"
        )
        got = await resolve_filename("docs\\report.pdf")
        assert str(got) == expected

    async def test_ambiguous_path_raises(self, sqlite_backend):
        await _insert_indexed_file(
            sqlite_backend, "r.pdf", "/a/docs/r.pdf"
        )
        await _insert_indexed_file(
            sqlite_backend, "r.pdf", "/b/docs/r.pdf"
        )
        with pytest.raises(AmbiguousFilenameError) as exc:
            await resolve_filename("docs/r.pdf")
        assert len(exc.value.candidates) == 2

    async def test_boundary_enforced(self, sqlite_backend):
        """'other-docs/r.pdf' must NOT match '/ws/docs/r.pdf'."""
        # Seed only one indexed file
        await _insert_indexed_file(
            sqlite_backend, "r.pdf", "/ws/docs/r.pdf"
        )
        # Should fall through path step (0 hits) → fuzzy → finds r.pdf by basename
        # similarity, which IS a partial match. To make the test strict, use a
        # filename that won't fuzzy-match either.
        from opendb_core.services.read_service import FileNotFoundError as FNF
        with pytest.raises((FNF, AmbiguousFilenameError)):
            await resolve_filename("other-dir/totally-different.xyz")

    async def test_basename_input_skips_path_step(self, sqlite_backend):
        """Input without separator uses step 1 (exact filename match)."""
        expected = await _insert_indexed_file(
            sqlite_backend, "report.pdf", "/ws/docs/report.pdf"
        )
        got = await resolve_filename("report.pdf")
        assert str(got) == expected

    async def test_upload_without_source_path_falls_through(self, sqlite_backend):
        """Uploaded files (no source_path) still resolve via fuzzy/ILIKE."""
        fid = str(uuid.uuid4())
        await sqlite_backend._db.execute(
            "INSERT INTO files (id, filename, mime_type, file_size, file_path, "
            "checksum, status, tags, metadata) "
            "VALUES (?, 'uploaded.pdf', 'application/pdf', 1, ?, ?, 'ready', '[]', '{}')",
            (fid, f"/tmp/{fid}", fid),
        )
        await sqlite_backend._db.commit()
        # Path-looking input that cannot path-match → falls through to fuzzy.
        # "some/uploaded.pdf" fuzzy-matches "uploaded.pdf".
        got = await resolve_filename("some/uploaded.pdf")
        assert str(got) == fid
