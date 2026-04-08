"""Storage backend factory.

Usage in application startup::

    from opendb_core.storage import init_backend, close_backend

    await init_backend("postgres")   # server mode (default)
    await init_backend("sqlite", db_path=".opendb/metadata.db")  # embedded mode

Then anywhere in the service layer::

    from opendb_core.storage import get_backend
    backend = get_backend()
    result = await backend.check_duplicate(checksum)
"""

from __future__ import annotations

import logging

from opendb_core.storage.base import StorageBackend

logger = logging.getLogger(__name__)

# Keyed by db_path (str) for SQLite or "postgres" for server mode.
# Supports multiple concurrent workspaces without overwriting each other.
_backends: dict[str, StorageBackend] = {}

# The "active" backend key — set by the most recent init_backend() call.
# Used by get_backend() when no explicit key is provided.
_active_key: str | None = None


async def init_backend(
    backend_type: str = "postgres",
    **kwargs,
) -> None:
    """Initialise and register a storage backend.

    Multiple SQLite backends (one per workspace) can coexist. Each is keyed
    by its ``db_path``. Calling ``init_backend`` with the same ``db_path``
    twice is a no-op — the existing backend is reused.

    Args:
        backend_type: ``"postgres"`` (default) or ``"sqlite"``.
        **kwargs: Passed to the backend constructor.
            For ``"sqlite"``: ``db_path`` (str | Path).
    """
    global _active_key

    if backend_type == "sqlite":
        from opendb_core.storage.sqlite import SQLiteBackend
        db_path = kwargs.get("db_path", ".opendb/metadata.db")
        key = str(db_path)

        if key in _backends:
            _active_key = key
            logger.debug("Reusing existing SQLite backend at %s", db_path)
            return

        backend = SQLiteBackend(db_path=db_path)
        await backend.init()
        _backends[key] = backend
        _active_key = key
        logger.info("Using SQLite backend at %s", db_path)
    else:
        key = "postgres"

        if key in _backends:
            _active_key = key
            logger.debug("Reusing existing PostgreSQL backend")
            return

        from opendb_core.storage.postgres import PostgresBackend
        backend = PostgresBackend()
        await backend.init()
        _backends[key] = backend
        _active_key = key
        logger.info("Using PostgreSQL backend")


def get_backend(key: str | None = None) -> StorageBackend:
    """Return a storage backend.

    Args:
        key: Explicit backend key (db_path for SQLite, ``"postgres"`` for
             server mode). When *None*, returns the most recently
             initialised backend.

    Falls back to a PostgresBackend (using the existing global pool) if
    no backend has been initialised — preserves backward compatibility.
    """
    lookup = key or _active_key

    if lookup is not None and lookup in _backends:
        return _backends[lookup]

    if _backends:
        # Return the active or any available backend
        if _active_key and _active_key in _backends:
            return _backends[_active_key]
        return next(iter(_backends.values()))

    from opendb_core.storage.postgres import PostgresBackend
    return PostgresBackend()


async def close_backend(key: str | None = None) -> None:
    """Close a backend gracefully.

    Args:
        key: Explicit backend key to close. When *None*, closes the most
             recently initialised backend.
    """
    global _active_key

    lookup = key or _active_key
    if lookup is not None and lookup in _backends:
        backend = _backends.pop(lookup)
        await backend.close()
        if _active_key == lookup:
            _active_key = next(iter(_backends), None) if _backends else None
        logger.info("Closed backend: %s", lookup)
    elif not key and _backends:
        # Close all if no specific key and no active key
        for k, b in list(_backends.items()):
            await b.close()
        _backends.clear()
        _active_key = None
        logger.info("Closed all backends")
