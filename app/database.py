import asyncio
import logging

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

pool: asyncpg.Pool | None = None


async def init_pool():
    global pool
    last_err = None
    for attempt in range(3):
        try:
            pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=settings.db_pool_min,
                max_size=settings.db_pool_max,
                command_timeout=60,
            )
            return
        except (OSError, asyncpg.PostgresError) as e:
            last_err = e
            wait = 2 ** attempt
            logger.warning(
                "DB connection attempt %d failed: %s. Retrying in %ds...",
                attempt + 1, e, wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError(f"Failed to connect to database after 3 attempts: {last_err}")


async def close_pool():
    global pool
    if pool:
        await pool.close()
        pool = None


async def get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    return pool
