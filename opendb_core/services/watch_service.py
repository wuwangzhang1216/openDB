"""Directory watch service.

Uses watchdog to monitor directories for file changes and automatically
ingests new or modified files. Each watcher runs an Observer in a background
thread; file-system events are debounced and dispatched to the async
ingestion pipeline via an asyncio queue.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from opendb_core.config import settings
from opendb_core.services.index_service import _has_parser, _is_excluded

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class WatchEntry:
    """Tracks one active directory watcher."""

    id: str
    path: Path
    tags: list[str] | None
    metadata: dict | None
    observer: Observer
    created_at: float = field(default_factory=time.time)
    ingested: int = 0
    failed: int = 0
    skipped: int = 0


# In-memory registry of active watchers (lost on restart)
_watchers: dict[str, WatchEntry] = {}
_watchers_lock = Lock()

# Module-level event loop reference, set by start_watch()
_loop: asyncio.AbstractEventLoop | None = None

# Background consumer task per watcher
_consumer_tasks: dict[str, asyncio.Task] = {}

# Asyncio queues per watcher (watch_id -> queue)
_queues: dict[str, asyncio.Queue] = {}


# ---------------------------------------------------------------------------
# Debounced event handler
# ---------------------------------------------------------------------------

# Minimum seconds between re-ingesting the same file path
_DEBOUNCE_SECONDS = 2.0


class _IngestHandler(FileSystemEventHandler):
    """Watchdog handler that puts file paths onto an asyncio queue."""

    def __init__(self, watch_id: str, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self.watch_id = watch_id
        self.queue = queue
        self.loop = loop
        self._last_seen: dict[str, float] = {}
        self._lock = Lock()

    def _should_process(self, path_str: str) -> bool:
        """Debounce: skip if the same path was seen within _DEBOUNCE_SECONDS."""
        now = time.time()
        with self._lock:
            last = self._last_seen.get(path_str, 0.0)
            if now - last < _DEBOUNCE_SECONDS:
                return False
            self._last_seen[path_str] = now
            return True

    def _enqueue(self, event: FileSystemEvent) -> None:
        src = event.src_path
        path = Path(src)

        # Skip directories
        if path.is_dir():
            return

        # Get watch entry for exclusion check
        with _watchers_lock:
            entry = _watchers.get(self.watch_id)
        if entry is None:
            return

        # Skip excluded files
        try:
            rel = path.relative_to(entry.path)
        except ValueError:
            return
        if _is_excluded(rel, settings.index_exclude_patterns):
            return

        # Debounce
        if not self._should_process(src):
            return

        # Thread-safe put onto the asyncio queue
        self.loop.call_soon_threadsafe(self.queue.put_nowait, path)

    def on_created(self, event: FileSystemEvent) -> None:
        self._enqueue(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._enqueue(event)


# ---------------------------------------------------------------------------
# Background consumer: pulls paths from queue and ingests them
# ---------------------------------------------------------------------------

async def _consume_queue(watch_id: str, queue: asyncio.Queue) -> None:
    """Long-running task that ingests files as they appear on the queue."""
    import magic as _magic
    from opendb_core.services.ingest_service import ingest_local_file

    while True:
        path: Path = await queue.get()
        try:
            if not path.exists() or not path.is_file():
                continue

            # Check MIME / parser support
            try:
                mime = _magic.from_file(str(path), mime=True)
            except OSError:
                logger.debug("watch %s: cannot detect MIME for %s", watch_id, path)
                continue
            if not _has_parser(mime):
                logger.debug("watch %s: unsupported MIME %s for %s", watch_id, mime, path)
                continue

            with _watchers_lock:
                entry = _watchers.get(watch_id)
            if entry is None:
                break  # watcher was removed

            result = await ingest_local_file(
                source_path=path,
                tags=entry.tags,
                metadata=entry.metadata,
            )
            status = result.get("status", "")
            with _watchers_lock:
                entry = _watchers.get(watch_id)
                if entry is not None:
                    if status == "ready":
                        entry.ingested += 1
                    elif status == "duplicate":
                        entry.skipped += 1
                    else:
                        entry.failed += 1

            logger.info(
                "watch %s: ingested %s -> %s", watch_id, path.name, status
            )

        except Exception as e:
            logger.error("watch %s: failed to ingest %s: %s", watch_id, path, e, exc_info=True)
            with _watchers_lock:
                entry = _watchers.get(watch_id)
                if entry is not None:
                    entry.failed += 1
        finally:
            queue.task_done()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_watch(
    dir_path: Path,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> str:
    """Start watching *dir_path* for file changes.

    Returns a watch_id. Must be called from an async context (or pass *loop*).
    """
    global _loop

    if loop is None:
        loop = asyncio.get_running_loop()
    _loop = loop

    with _watchers_lock:
        if len(_watchers) >= settings.watch_max_watchers:
            raise ValueError(
                f"Maximum number of watchers ({settings.watch_max_watchers}) reached"
            )

        # Check if already watching this directory
        for entry in _watchers.values():
            if entry.path == dir_path:
                return entry.id

    watch_id = uuid.uuid4().hex[:12]
    queue: asyncio.Queue = asyncio.Queue()

    handler = _IngestHandler(watch_id, queue, loop)
    observer = Observer()
    observer.schedule(handler, str(dir_path), recursive=True)
    observer.daemon = True
    observer.start()

    entry = WatchEntry(
        id=watch_id,
        path=dir_path,
        tags=tags,
        metadata=metadata,
        observer=observer,
    )

    with _watchers_lock:
        _watchers[watch_id] = entry

    _queues[watch_id] = queue
    task = loop.create_task(_consume_queue(watch_id, queue))
    _consumer_tasks[watch_id] = task

    logger.info("Started watching %s (id=%s)", dir_path, watch_id)
    return watch_id


def stop_watch(watch_id: str) -> bool:
    """Stop a watcher by ID. Returns True if it existed."""
    with _watchers_lock:
        entry = _watchers.pop(watch_id, None)
    if entry is None:
        return False

    entry.observer.stop()
    entry.observer.join(timeout=5)

    task = _consumer_tasks.pop(watch_id, None)
    if task is not None:
        if not task.done():
            task.cancel()
        # If the consumer task was scheduled on a loop that never ran (e.g.
        # in unit tests that create a fresh loop and close it without ever
        # running it), the wrapped coroutine never reaches its first await
        # and Python emits a "coroutine '_consume_queue' was never awaited"
        # warning when the task is garbage-collected. Explicitly closing the
        # coroutine here marks it as cleanly finished and silences the warning.
        try:
            coro = task.get_coro()
            if coro is not None:
                coro.close()
        except Exception:
            pass

    _queues.pop(watch_id, None)

    logger.info("Stopped watching %s (id=%s)", entry.path, watch_id)
    return True


def stop_all() -> None:
    """Stop all active watchers. Called during shutdown."""
    with _watchers_lock:
        ids = list(_watchers.keys())
    for wid in ids:
        stop_watch(wid)


def list_watches() -> list[dict]:
    """Return info about all active watchers."""
    with _watchers_lock:
        entries = list(_watchers.values())
    return [
        {
            "id": e.id,
            "path": str(e.path),
            "tags": e.tags,
            "created_at": e.created_at,
            "ingested": e.ingested,
            "failed": e.failed,
            "skipped": e.skipped,
        }
        for e in entries
    ]


def get_watch(watch_id: str) -> dict | None:
    """Return info about a single watcher, or None."""
    with _watchers_lock:
        entry = _watchers.get(watch_id)
    if entry is None:
        return None
    return {
        "id": entry.id,
        "path": str(entry.path),
        "tags": entry.tags,
        "created_at": entry.created_at,
        "ingested": entry.ingested,
        "failed": entry.failed,
        "skipped": entry.skipped,
    }
