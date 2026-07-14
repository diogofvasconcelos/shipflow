from fastapi import APIRouter, Response
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(session: DbSession, response: Response) -> dict:
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    redis_ok = True
    try:
        redis = Redis.from_url(get_settings().redis_url)
        await redis.ping()
        await redis.aclose()
    except Exception:
        redis_ok = False

    if not (db_ok and redis_ok):
        response.status_code = 503
    return {"status": "ok" if (db_ok and redis_ok) else "degraded", "db": db_ok, "redis": redis_ok}
