"""Index trigger utilities for OpenDB integration.

These functions are designed to be called with asyncio.create_task()
for non-blocking indexing. All functions are safe to call when OpenDB
is unavailable — they silently return without error.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from opendb_integration.client import OpenDBClient

logger = logging.getLogger(__name__)


async def ensure_indexed(client: OpenDBClient, workspace: str) -> None:
    """Ensure a workspace directory is indexed and watched by OpenDB.

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
                    logger.debug("OpenDB already watching: %s", workspace)
                    return

        # Trigger indexing + watching
        logger.info("OpenDB indexing workspace: %s", workspace)
        result = await client.index_directory(workspace)
        if result:
            ingested = result.get("ingested", 0)
            total = result.get("total_files", 0)
            logger.info("OpenDB indexed %d/%d files in %s", ingested, total, workspace)
    except Exception as e:
        logger.debug("OpenDB ensure_indexed failed: %s", e)


async def index_file(client: OpenDBClient, file_path: str) -> None:
    """Upload a single file to OpenDB for indexing.

    Safe to call with asyncio.create_task() — never raises.
    """
    try:
        if not await client.is_available():
            return

        fp = Path(file_path)
        if not fp.exists():
            return

        logger.debug("OpenDB indexing file: %s", file_path)
        await client.upload_file(file_path)
    except Exception as e:
        logger.debug("OpenDB index_file failed: %s", e)


def _is_same_directory(path_a: str, path_b: str) -> bool:
    """Check if two paths refer to the same directory (normalized)."""
    try:
        a = os.path.normcase(os.path.normpath(os.path.abspath(path_a)))
        b = os.path.normcase(os.path.normpath(os.path.abspath(path_b)))
        return a == b
    except (TypeError, ValueError):
        return False
