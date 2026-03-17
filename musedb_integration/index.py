"""Index trigger utilities for MuseDB integration.

These functions are designed to be called with asyncio.create_task()
for non-blocking indexing. All functions are safe to call when MuseDB
is unavailable — they silently return without error.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from musedb_integration.client import MuseDBClient

logger = logging.getLogger(__name__)


async def ensure_indexed(client: MuseDBClient, workspace: str) -> None:
    """Ensure a workspace directory is indexed and watched by MuseDB.

    Idempotent: checks existing watchers first, only triggers index if needed.
    Safe to call with asyncio.create_task() — never raises.
    """
    try:
        if not await client.is_available():
            return

        workspace = str(Path(workspace).resolve())

        # Check if already watching
        watchers = await client.list_watchers()
        if watchers:
            for w in watchers:
                watcher_path = w.get("path", "")
                if _is_same_directory(watcher_path, workspace):
                    logger.debug("MuseDB already watching: %s", workspace)
                    return

        # Trigger indexing + watching
        logger.info("MuseDB indexing workspace: %s", workspace)
        result = await client.index_directory(workspace)
        if result:
            ingested = result.get("ingested", 0)
            total = result.get("total_files", 0)
            logger.info("MuseDB indexed %d/%d files in %s", ingested, total, workspace)
    except Exception as e:
        logger.debug("MuseDB ensure_indexed failed: %s", e)


async def index_file(client: MuseDBClient, file_path: str) -> None:
    """Upload a single file to MuseDB for indexing.

    Safe to call with asyncio.create_task() — never raises.
    """
    try:
        if not await client.is_available():
            return

        fp = Path(file_path)
        if not fp.exists():
            return

        logger.debug("MuseDB indexing file: %s", file_path)
        await client.upload_file(file_path)
    except Exception as e:
        logger.debug("MuseDB index_file failed: %s", e)


def _is_same_directory(path_a: str, path_b: str) -> bool:
    """Check if two paths refer to the same directory (normalized)."""
    try:
        a = os.path.normcase(os.path.normpath(os.path.abspath(path_a)))
        b = os.path.normcase(os.path.normpath(os.path.abspath(path_b)))
        return a == b
    except (TypeError, ValueError):
        return False
