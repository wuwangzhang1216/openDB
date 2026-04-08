"""Storage backend factory.

Usage in application startup::

    from app.storage import init_backend, close_backend

    await init_backend("postgres")   # server mode (default)
    await init_backend("sqlite", db_path=".opendb/metadata.db")  # embedded mode

Then anywhere in the service layer::

    from app.storage import get_backend
    backend = get_backend()
    result = await backend.check_duplicate(checksum)
"""

from __future__ import annotations

import logging

from app.storage.base import StorageBackend

logger = logging.getLogger(__name__)

_backend: StorageBackend | None = None


async def init_backend(
    backend_type: str = "postgres",
    **kwargs,
) -> None:
    """Initialise and register the global storage backend.

    Args:
        backend_type: ``"postgres"`` (default) or ``"sqlite"``.
        **kwargs: Passed to the backend constructor.
            For ``"sqlite"``: ``db_path`` (str | Path).
    """
    global _backend

    if backend_type == "sqlite":
        from app.storage.sqlite import SQLiteBackend
        db_path = kwargs.get("db_path", ".opendb/metadata.db")
        _backend = SQLiteBackend(db_path=db_path)
        await _backend.init()
        logger.info("Using SQLite backend at %s", db_path)
    else:
        from app.storage.postgres import PostgresBackend
        _backend = PostgresBackend()
        await _backend.init()
        logger.info("Using PostgreSQL backend")


def get_backend() -> StorageBackend:
    """Return the active storage backend.

    Falls back to a PostgresBackend (using the existing global pool) if
    ``init_backend()`` was never called — preserves backward compatibility
    with any code that initialises the pool via ``app.database.init_pool()``
    but hasn't been updated to call ``init_backend()`` yet.
    """
    if _backend is None:
        from app.storage.postgres import PostgresBackend
        return PostgresBackend()
    return _backend


async def close_backend() -> None:
    """Close the active backend gracefully."""
    global _backend
    if _backend is not None:
        await _backend.close()
        _backend = None
