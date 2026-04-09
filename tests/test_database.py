import pytest

from opendb_core import database


class TestGetPool:
    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self) -> None:
        """get_pool() must raise RuntimeError when pool is None."""
        original = database.pool
        database.pool = None
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                await database.get_pool()
        finally:
            database.pool = original


class TestClosePool:
    @pytest.mark.asyncio
    async def test_close_sets_none(self) -> None:
        """close_pool() sets the global pool to None."""
        original = database.pool
        database.pool = None  # Simulate already-closed state
        await database.close_pool()
        assert database.pool is None
        database.pool = original
