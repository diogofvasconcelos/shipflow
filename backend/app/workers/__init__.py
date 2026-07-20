"""Arq worker settings and job registry. See docs/ARCHITECTURE.md §8.

Jobs are added task by task (T5 onward in docs/ORCHESTRATION.md). Each job is a
thin wrapper that calls into app.services — no business logic lives here.
"""

from arq import cron, func
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers.notifications import process_meli_notification
from app.workers.tokens import refresh_stale_tokens

settings = get_settings()


async def startup(ctx: dict) -> None:
    pass


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    functions: list = [func(process_meli_notification, max_tries=5)]
    cron_jobs: list = [cron(refresh_stale_tokens, minute={0, 30})]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
