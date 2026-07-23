"""Polling safety net (ARCHITECTURE §6.3): webhooks fail silently (deploys, tunnel
drops, ML giving up retries, subscription misconfig). One pipeline, two feeders —
this module never re-implements upsert logic, it only drives IngestionService.
"""

import logging
from datetime import UTC, datetime, timedelta

from app.core.db import SessionLocal
from app.core.logging import log_context
from app.integrations.meli.client import MeliClient
from app.integrations.meli.errors import MeliError
from app.models.meli_account import MeliAccount
from app.repositories.meli_account import MeliAccountRepository, list_active_accounts
from app.repositories.poll_cursor import PollCursorRepository
from app.repositories.shipment import list_ready_to_ship_shipments
from app.services.ingestion import IngestionService

logger = logging.getLogger("app.workers.polling")

OVERLAP = timedelta(minutes=10)
FIRST_RUN_LOOKBACK = timedelta(hours=24)


def _to_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes on read; Postgres timestamptz returns
    aware ones. Normalize before doing arithmetic (same pattern as
    app/services/ingestion.py's stale-skip)."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _client(ctx: dict) -> tuple[MeliClient, bool]:
    client = ctx.get("meli_client")
    return (client, False) if client is not None else (MeliClient(), True)


async def poll_orders(ctx: dict) -> None:
    session_factory = ctx.get("session_factory", SessionLocal)
    client, owns_client = _client(ctx)
    try:
        async with session_factory() as session:
            accounts = await list_active_accounts(session)
        for account in accounts:
            with log_context(tenant_id=account.tenant_id):
                await _poll_account_orders(session_factory, client, account)
    finally:
        if owns_client:
            await client.aclose()


async def _poll_account_orders(session_factory, client: MeliClient, account: MeliAccount) -> None:
    async with session_factory() as session:
        cursor = await PollCursorRepository(session).get(account.id)

    poll_started_at = datetime.now(UTC)
    since = (
        (_to_utc(cursor) - OVERLAP)
        if cursor is not None
        else (poll_started_at - FIRST_RUN_LOOKBACK)
    )

    offset = 0
    while True:
        try:
            page = await client.search_orders(account, since, offset)
        except MeliError:
            logger.exception("poll_orders: search failed for account %s", account.id)
            return  # abort this account's pass — cursor does NOT advance

        results = page.get("results") or []
        if not results:
            break

        for order in results:
            try:
                async with session_factory() as session:
                    await IngestionService(session, client).ingest_order(account, order["id"])
                    await session.commit()
            except Exception:
                logger.exception(
                    "poll_orders: ingest failed for account %s order %s", account.id, order["id"]
                )
                return  # abort this account's pass — cursor does NOT advance

        offset += len(results)
        total = (page.get("paging") or {}).get("total", offset)
        if offset >= total:
            break

    async with session_factory() as session:
        await PollCursorRepository(session).set_cursor(account.id, poll_started_at)
        await session.commit()


async def sync_open_shipments(ctx: dict) -> None:
    session_factory = ctx.get("session_factory", SessionLocal)
    client, owns_client = _client(ctx)
    try:
        async with session_factory() as session:
            shipments = await list_ready_to_ship_shipments(session)

        for shipment in shipments:
            with log_context(tenant_id=shipment.tenant_id):
                try:
                    async with session_factory() as session:
                        # meli_account_id is FK-guaranteed and accounts are only
                        # ever soft-disabled, never hard-deleted — always resolves.
                        account = await MeliAccountRepository(session).get(
                            shipment.tenant_id, shipment.meli_account_id
                        )
                        await IngestionService(session, client).ingest_shipment(
                            account, shipment.meli_shipment_id
                        )
                        await session.commit()
                except Exception:
                    logger.exception("sync_open_shipments: failed for shipment %s", shipment.id)
    finally:
        if owns_client:
            await client.aclose()
