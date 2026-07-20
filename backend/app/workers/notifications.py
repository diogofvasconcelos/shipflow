"""Arq job that processes one received webhook_events row: resolve tenant, route
by topic, run the shared ingestion pipeline. At-least-once delivery, so it must be
idempotent — the pipeline's upserts + stale-skip guarantee that (ARCHITECTURE §6.2).
"""

import logging
from datetime import UTC, datetime

from app.core.db import SessionLocal
from app.core.logging import log_context
from app.integrations.meli.client import MeliClient
from app.repositories.meli_account import get_account_by_meli_user_id
from app.repositories.webhook_event import WebhookEventRepository
from app.services.ingestion import IngestionService

logger = logging.getLogger("app.workers.notifications")

MAX_TRIES = 5


def _resource_id(resource: str) -> int:
    return int(resource.rstrip("/").split("/")[-1])


async def process_meli_notification(ctx: dict, webhook_event_id: int) -> None:
    session_factory = ctx.get("session_factory", SessionLocal)
    client = ctx.get("meli_client") or MeliClient()
    owns_client = "meli_client" not in ctx
    try:
        async with session_factory() as session:
            events = WebhookEventRepository(session)
            event = await events.get(webhook_event_id)
            if event is None or event.status == "processed":
                return  # idempotent: nothing to do / already done

            account = await get_account_by_meli_user_id(session, event.meli_user_id)
            if account is None:
                event.status = "skipped"
                event.processed_at = datetime.now(UTC)
                await session.commit()
                return

            with log_context(tenant_id=account.tenant_id):
                ingestion = IngestionService(session, client)
                meli_id = _resource_id(event.resource)
                if event.topic == "orders_v2":
                    await ingestion.ingest_order(account, meli_id)
                elif event.topic == "shipments":
                    await ingestion.ingest_shipment(account, meli_id)
                else:
                    event.status = "skipped"
                    event.processed_at = datetime.now(UTC)
                    await session.commit()
                    return

                event.status = "processed"
                event.processed_at = datetime.now(UTC)
                await session.commit()
    except Exception as exc:
        # Final attempt → mark failed (poller is the backstop); re-raise so Arq retries.
        if ctx.get("job_try", 1) >= MAX_TRIES:
            await _mark_failed(session_factory, webhook_event_id, str(exc))
        raise
    finally:
        if owns_client:
            await client.aclose()


async def _mark_failed(session_factory, webhook_event_id: int, error: str) -> None:
    async with session_factory() as session:
        event = await WebhookEventRepository(session).get(webhook_event_id)
        if event is not None:
            event.status = "failed"
            event.error = error[:500]
            event.processed_at = datetime.now(UTC)
            await session.commit()
