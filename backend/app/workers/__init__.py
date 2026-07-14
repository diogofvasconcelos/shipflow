"""Arq worker settings and job registry. See docs/ARCHITECTURE.md §8.

Jobs are added task by task (T5 onward in docs/ORCHESTRATION.md). Each job is a
thin wrapper that calls into app.services — no business logic lives here.
"""

from arq.connections import RedisSettings

from app.core.config import get_settings

settings = get_settings()


async def startup(ctx: dict) -> None:
    pass


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    functions: list = []
    cron_jobs: list = []
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
