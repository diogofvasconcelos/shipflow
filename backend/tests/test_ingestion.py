import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.meli.errors import MeliError
from app.models.event_outbox import EventOutbox
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.shipment import Shipment
from app.models.variant import Variant
from app.models.webhook_event import WebhookEvent
from app.repositories.meli_account import MeliAccountRepository
from app.repositories.order import OrderRepository
from app.repositories.tenant import TenantRepository
from app.repositories.webhook_event import WebhookEventRepository
from app.services.ingestion import IngestionService
from app.workers.notifications import process_meli_notification

FIXTURES = Path(__file__).parent / "fixtures" / "meli"

ORDER_SINGLE = 2000003508419500
SHIPMENT_S1 = 44444444444
SHIPMENT_S2 = 55555555555
PACK_A = 2000003508419601
PACK_B = 2000003508419602


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeMeliClient:
    """Stands in for the T3 client — the ingestion service only calls get_order /
    get_shipment. Deep-copies so stored `raw` never aliases the fixture."""

    def __init__(self, orders=None, shipments=None):
        self.orders = orders or {}
        self.shipments = shipments or {}
        self.order_calls = 0
        self.shipment_calls = 0
        self.raise_on_order = False

    async def get_order(self, account, meli_order_id):
        self.order_calls += 1
        if self.raise_on_order:
            raise MeliError("boom")
        return deepcopy(self.orders[meli_order_id])

    async def get_shipment(self, account, meli_shipment_id):
        self.shipment_calls += 1
        return deepcopy(self.shipments[meli_shipment_id])

    async def aclose(self):
        pass


async def _seed_account(db_session: AsyncSession, meli_user_id: int = 123456789):
    tenant = await TenantRepository(db_session).create(name="loja-ing", slug="loja-ing")
    account = await MeliAccountRepository(db_session).create(
        tenant.id,
        meli_user_id=meli_user_id,
        nickname="LOJA_ING",
        access_token="APP_USR-x",
        refresh_token="TG-x",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    await db_session.commit()
    return account


async def _count(db_session: AsyncSession, model) -> int:
    result = await db_session.execute(select(func.count()).select_from(model))
    return result.scalar_one()


def _single_client():
    return FakeMeliClient(
        orders={ORDER_SINGLE: load("order_single.json")},
        shipments={SHIPMENT_S1: load("shipment_s1.json")},
    )


# --- idempotency -----------------------------------------------------------------


async def test_ingest_order_is_idempotent_and_emits_one_new_order_event(db_session):
    account = await _seed_account(db_session)
    client = _single_client()
    svc = IngestionService(db_session, client)

    _, created1 = await svc.ingest_order(account, ORDER_SINGLE)
    await db_session.commit()
    _, created2 = await svc.ingest_order(account, ORDER_SINGLE)
    await db_session.commit()

    assert created1 is True
    assert created2 is False  # second run hits the stale-skip
    assert await _count(db_session, Order) == 1
    assert await _count(db_session, OrderItem) == 1
    assert await _count(db_session, Variant) == 1
    assert await _count(db_session, Shipment) == 1

    outbox = (await db_session.execute(select(EventOutbox))).scalars().all()
    assert len(outbox) == 1
    assert outbox[0].event_type == "new_order"
    assert outbox[0].payload["meli_order_id"] == ORDER_SINGLE
    assert outbox[0].payload["total_amount"] == "289.90"
    assert outbox[0].payload["meli_account"]["nickname"] == "LOJA_ING"


async def test_processing_five_times_yields_identical_state(db_session):
    account = await _seed_account(db_session)
    svc = IngestionService(db_session, _single_client())

    for _ in range(5):
        await svc.ingest_order(account, ORDER_SINGLE)
        await db_session.commit()

    assert await _count(db_session, Order) == 1
    assert await _count(db_session, EventOutbox) == 1


# --- stale-skip ------------------------------------------------------------------


async def test_stale_update_is_skipped(db_session):
    account = await _seed_account(db_session)
    client = _single_client()
    svc = IngestionService(db_session, client)
    await svc.ingest_order(account, ORDER_SINGLE)
    await db_session.commit()

    # A re-delivery carrying an OLDER snapshot with a changed status must not overwrite.
    stale = load("order_single.json")
    stale["date_last_updated"] = "2026-07-13T13:00:00.000-03:00"
    stale["status"] = "cancelled"
    client.orders[ORDER_SINGLE] = stale

    await svc.ingest_order(account, ORDER_SINGLE)
    await db_session.commit()

    order = await OrderRepository(db_session).get_by_meli_order_id(account.tenant_id, ORDER_SINGLE)
    assert order.meli_status == "paid"


async def test_newer_update_overwrites(db_session):
    account = await _seed_account(db_session)
    client = _single_client()
    svc = IngestionService(db_session, client)
    await svc.ingest_order(account, ORDER_SINGLE)
    await db_session.commit()

    newer = load("order_single.json")
    newer["date_last_updated"] = "2026-07-13T15:00:00.000-03:00"
    newer["status"] = "cancelled"
    client.orders[ORDER_SINGLE] = newer

    await svc.ingest_order(account, ORDER_SINGLE)
    await db_session.commit()

    order = await OrderRepository(db_session).get_by_meli_order_id(account.tenant_id, ORDER_SINGLE)
    assert order.meli_status == "cancelled"


# --- pack linking ----------------------------------------------------------------


async def test_pack_orders_share_one_shipment(db_session):
    account = await _seed_account(db_session)
    client = FakeMeliClient(
        orders={PACK_A: load("order_pack_a.json"), PACK_B: load("order_pack_b.json")},
        shipments={SHIPMENT_S2: load("shipment_s2.json")},
    )
    svc = IngestionService(db_session, client)

    await svc.ingest_order(account, PACK_A)
    await svc.ingest_order(account, PACK_B)
    await db_session.commit()

    assert await _count(db_session, Shipment) == 1
    shipment = (await db_session.execute(select(Shipment))).scalar_one()
    orders = (await db_session.execute(select(Order))).scalars().all()
    assert len(orders) == 2
    assert {o.shipment_id for o in orders} == {shipment.id}
    assert shipment.carrier_name == "Loggi"


# --- size extraction -------------------------------------------------------------


async def test_size_extracted_from_variation_attribute(db_session):
    account = await _seed_account(db_session)
    svc = IngestionService(db_session, _single_client())
    await svc.ingest_order(account, ORDER_SINGLE)
    await db_session.commit()

    variant = (await db_session.execute(select(Variant))).scalar_one()
    assert variant.size == "41"
    assert variant.model_name == "Tênis Runner Masculino Preto"
    assert variant.internal_code.startswith("SFV")


async def test_shipment_fields_captured(db_session):
    account = await _seed_account(db_session)
    svc = IngestionService(db_session, _single_client())
    await svc.ingest_order(account, ORDER_SINGLE)
    await db_session.commit()

    shipment = (await db_session.execute(select(Shipment))).scalar_one()
    assert shipment.meli_status == "ready_to_ship"
    assert shipment.meli_substatus == "ready_to_print"
    assert shipment.logistic_type == "drop_off"
    assert shipment.carrier_name == "Correios"
    assert shipment.handling_limit_at is not None


# --- worker routing --------------------------------------------------------------


async def _make_webhook_event(db_session, *, topic, resource, meli_user_id=123456789):
    event = await WebhookEventRepository(db_session).create(
        topic=topic, resource=resource, meli_user_id=meli_user_id, payload={}
    )
    await db_session.commit()
    return event


async def test_worker_routes_orders_v2_and_marks_processed(db_session, db_session_factory):
    await _seed_account(db_session)
    event = await _make_webhook_event(
        db_session, topic="orders_v2", resource=f"/orders/{ORDER_SINGLE}"
    )
    ctx = {
        "session_factory": db_session_factory,
        "meli_client": _single_client(),
        "job_try": 1,
    }

    await process_meli_notification(ctx, event.id)

    async with db_session_factory() as check:
        refreshed = await WebhookEventRepository(check).get(event.id)
        assert refreshed.status == "processed"
        assert refreshed.processed_at is not None
        assert await _count(check, Order) == 1


async def test_worker_routes_shipments_topic(db_session, db_session_factory):
    await _seed_account(db_session)
    event = await _make_webhook_event(
        db_session, topic="shipments", resource=f"/shipments/{SHIPMENT_S1}"
    )
    ctx = {
        "session_factory": db_session_factory,
        "meli_client": _single_client(),
        "job_try": 1,
    }

    await process_meli_notification(ctx, event.id)

    async with db_session_factory() as check:
        assert (await WebhookEventRepository(check).get(event.id)).status == "processed"
        assert await _count(check, Shipment) == 1


async def test_worker_marks_failed_on_final_attempt(db_session, db_session_factory):
    await _seed_account(db_session)
    event = await _make_webhook_event(
        db_session, topic="orders_v2", resource=f"/orders/{ORDER_SINGLE}"
    )
    client = _single_client()
    client.raise_on_order = True
    ctx = {"session_factory": db_session_factory, "meli_client": client, "job_try": 5}

    with pytest.raises(MeliError):
        await process_meli_notification(ctx, event.id)

    async with db_session_factory() as check:
        refreshed = await WebhookEventRepository(check).get(event.id)
        assert refreshed.status == "failed"
        assert refreshed.error


async def test_worker_unknown_account_marks_skipped(db_session, db_session_factory):
    # webhook_events row whose meli_user_id has no account (deleted between intake/processing)
    event = await _make_webhook_event(
        db_session, topic="orders_v2", resource=f"/orders/{ORDER_SINGLE}", meli_user_id=999
    )
    ctx = {"session_factory": db_session_factory, "meli_client": _single_client(), "job_try": 1}

    await process_meli_notification(ctx, event.id)

    async with db_session_factory() as check:
        assert (await WebhookEventRepository(check).get(event.id)).status == "skipped"


async def test_worker_unknown_topic_marks_skipped(db_session, db_session_factory):
    await _seed_account(db_session)
    event = await _make_webhook_event(db_session, topic="questions", resource="/questions/123")
    ctx = {"session_factory": db_session_factory, "meli_client": _single_client(), "job_try": 1}

    await process_meli_notification(ctx, event.id)

    async with db_session_factory() as check:
        assert (await WebhookEventRepository(check).get(event.id)).status == "skipped"
        assert await _count(check, Order) == 0


async def test_size_falls_back_to_seller_custom_field_digits(db_session):
    account = await _seed_account(db_session)
    payload = load("order_single.json")
    del payload["order_items"][0]["item"]["variation_attributes"]
    payload["order_items"][0]["item"]["seller_custom_field"] = "TENIS-42"
    client = FakeMeliClient(
        orders={ORDER_SINGLE: payload}, shipments={SHIPMENT_S1: load("shipment_s1.json")}
    )

    await IngestionService(db_session, client).ingest_order(account, ORDER_SINGLE)
    await db_session.commit()

    variant = (await db_session.execute(select(Variant))).scalar_one()
    assert variant.size == "42"


async def test_worker_already_processed_is_noop(db_session, db_session_factory):
    await _seed_account(db_session)
    event = await _make_webhook_event(
        db_session, topic="orders_v2", resource=f"/orders/{ORDER_SINGLE}"
    )
    client = _single_client()
    ctx = {"session_factory": db_session_factory, "meli_client": client, "job_try": 1}
    await process_meli_notification(ctx, event.id)
    calls_after_first = client.order_calls

    await process_meli_notification(ctx, event.id)  # re-run same notification

    assert client.order_calls == calls_after_first  # no re-fetch
    async with db_session_factory() as check:
        assert await _count(check, WebhookEvent) == 1
