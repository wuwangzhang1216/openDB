from fastapi import APIRouter

from opendb_core.database import get_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    return {"status": "ok", "database": result == 1}
