"""Webhook intake (ARCHITECTURE §6.2): the whole point is speed and durability —
validate, insert one row, enqueue, return. Zero ML calls, no heavy queries.

Idempotency of PROCESSING (T6) is the real guarantee; the Redis dedup here is a
best-effort optimization that degrades to a no-op when Redis is unavailable.
"""

import logging

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories.meli_account import get_account_by_meli_user_id
from app.repositories.webhook_event import WebhookEventRepository

logger = logging.getLogger("app.services.webhook_intake")

DEDUP_TTL_SECONDS = 30
PROCESS_JOB = "process_meli_notification"


class EnqueueFailedError(Exception):
    """Redis enqueue failed — the router returns 500 so ML redelivers later."""


class WebhookIntakeService:
    def __init__(self, session: AsyncSession, pool: ArqRedis) -> None:
        self.session = session
        self.pool = pool
        self.events = WebhookEventRepository(session)
        self.settings = get_settings()

    async def intake(self, body: dict) -> None:
        topic = body.get("topic", "")
        resource = body.get("resource", "")
        user_id = body.get("user_id")
        application_id = body.get("application_id")

        # (1) foreign application → skipped (never 4xx; ML retries forever otherwise).
        if str(application_id) != self.settings.meli_client_id:
            await self._record(topic, resource, user_id, body, "skipped")
            return

        # (2) unknown seller → skipped (tenant can't be resolved).
        account = await get_account_by_meli_user_id(self.session, user_id) if user_id else None
        if account is None:
            await self._record(topic, resource, user_id, body, "skipped")
            return

        # (3) Redis collapse of duplicate bursts; on Redis error, proceed WITHOUT dedup.
        try:
            first = await self.pool.set(
                f"meli:notif:{topic}:{resource}", "1", nx=True, ex=DEDUP_TTL_SECONDS
            )
            if not first:
                await self._record(topic, resource, user_id, body, "skipped")
                return
        except Exception:
            logger.warning("redis dedup unavailable; proceeding without collapse")

        # (4) durable receipt, committed BEFORE enqueue so the worker can load it.
        event = await self._record(topic, resource, user_id, body, "received")

        # (5) enqueue; failure → 500 (ML redelivers, poller re-converges).
        try:
            await self.pool.enqueue_job(PROCESS_JOB, event.id)
        except Exception as exc:
            raise EnqueueFailedError from exc

    async def _record(self, topic, resource, user_id, body, status):
        event = await self.events.create(
            topic=topic,
            resource=resource,
            meli_user_id=user_id or 0,
            payload=body,
            status=status,
        )
        await self.session.commit()
        return event
