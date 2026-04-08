"""StorageBackend Protocol — the interface all backends must implement.

Both the PostgreSQL and SQLite backends satisfy this contract, allowing
the service layer to remain backend-agnostic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """All database operations needed by the MuseDB service layer."""

    async def init(self) -> None:
        """Initialize the backend (create pool, open connection, create schema)."""
        ...

    async def close(self) -> None:
        """Tear down the backend gracefully."""
        ...

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def check_duplicate(self, checksum: str) -> dict | None:
        """Return existing file record if a duplicate exists, else None."""
        ...

    async def persist_ingestion(
        self,
        *,
        file_id: str,
        file_path: str,
        original_filename: str,
        mime_type: str,
        file_size: int,
        checksum: str,
        tags: list[str],
        merged_metadata: dict,
        parse_result,
        full_text: str,
        total_lines: int,
        line_index: list[int],
        toc: str,
        page_line_ranges: list[tuple[int, int]],
    ) -> dict:
        """Atomically write parsed file data into storage.

        Returns the file record dict on success, or a duplicate record dict
        if a checksum collision is detected during the write.
        """
        ...

    async def mark_file_failed(self, file_id: str, error: str) -> None:
        """Mark a file as failed with an error message."""
        ...

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_file_text(self, file_id: str) -> dict:
        """Return {full_text, total_lines, line_index, toc} for a file."""
        ...

    async def get_total_pages(self, file_id: str) -> int:
        """Return the number of pages/slides/sheets for a file."""
        ...

    async def get_page_line_ranges(
        self, file_id: str, page_numbers: list[int]
    ) -> list[tuple[int, int]]:
        """Return [(line_start, line_end), ...] for the given page numbers."""
        ...

    async def get_page_by_section_title(
        self, file_id: str, title: str
    ) -> list[int]:
        """Return page numbers whose section_title matches title (ILIKE)."""
        ...

    async def get_file_info(self, file_id: str) -> dict:
        """Return {file_path, mime_type, filename} for a file."""
        ...

    async def get_sheet_names_for_pages(
        self, file_id: str, page_nums: list[int]
    ) -> list[str]:
        """Return distinct base sheet names for the given page numbers."""
        ...

    # ------------------------------------------------------------------
    # Filename resolution
    # ------------------------------------------------------------------

    async def find_file_exact(self, filename: str) -> str | None:
        """Exact filename match → file UUID string, or None."""
        ...

    async def find_file_by_uuid(self, file_id_str: str) -> str | None:
        """UUID lookup → file UUID string if it exists, or None."""
        ...

    async def find_files_fuzzy(self, filename: str) -> list[dict]:
        """Fuzzy filename match → list of {id, filename, sim} dicts (top 5)."""
        ...

    async def find_files_ilike(self, pattern: str) -> list[dict]:
        """Substring match (ILIKE %pattern%) → list of {id, filename} dicts."""
        ...

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_fts(
        self, query: str, filters: dict, limit: int, offset: int
    ) -> dict:
        """Full-text search → {total, results[...]}.

        Handles both Latin and CJK queries (CJK tokenized via jieba).
        """
        ...

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def batch_check_duplicates(self, checksums: list[str]) -> set[str]:
        """Return the subset of checksums already in storage (status=ready)."""
        ...

    # ------------------------------------------------------------------
    # Files CRUD
    # ------------------------------------------------------------------

    async def list_files(
        self,
        filters: dict,
        sort_field: str,
        sort_dir: str,
        limit: int,
        offset: int,
    ) -> dict:
        """List files with optional filters → {total, files[...]}."""
        ...

    async def get_file_by_id(self, file_id: str) -> dict | None:
        """Return full file record or None."""
        ...

    async def delete_file(self, file_id: str) -> str | None:
        """Delete file record (cascades to pages + file_text).

        Returns the file_path so the caller can clean up disk storage.
        Returns None if the file does not exist.
        """
        ...

    # ------------------------------------------------------------------
    # Agent Memory
    # ------------------------------------------------------------------

    async def store_memory(
        self,
        *,
        memory_id: str,
        content: str,
        memory_type: str,
        tags: list[str],
        metadata: dict,
    ) -> dict:
        """Store a memory entry. Returns the memory record dict."""
        ...

    async def recall_memories(
        self,
        query: str,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
    ) -> dict:
        """FTS search on memories with time-decay scoring.

        Returns {total, results[...]} where each result includes a ``score``
        field that combines FTS relevance with recency decay.
        """
        ...

    async def get_memory(self, memory_id: str) -> dict | None:
        """Return a single memory record by UUID, or None."""
        ...

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory by UUID. Returns True if deleted, False if not found."""
        ...

    async def list_memories(
        self,
        memory_type: str | None,
        tags: list[str] | None,
        limit: int,
        offset: int,
    ) -> dict:
        """List memories with optional filters. Returns {total, memories[...]}."""
        ...
