import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.event_outbox import EventOutboxRepository
from app.repositories.tenant import TenantRepository
from app.repositories.webhook_event import WebhookEventRepository


async def _create_tenant(db_session: AsyncSession, slug: str):
    tenant = await TenantRepository(db_session).create(name=slug, slug=slug)
    await db_session.commit()
    return tenant


# --- webhook_events (no tenant_id — not tenant-owned) -------------------------------


async def test_webhook_event_create_and_get_round_trip(db_session: AsyncSession):
    repo = WebhookEventRepository(db_session)

    event = await repo.create(
        topic="orders_v2",
        resource="/orders/2000003508419500",
        meli_user_id=123456789,
        payload={"resource": "/orders/2000003508419500", "user_id": 123456789},
    )
    await db_session.commit()

    assert event.status == "received"
    assert event.provider == "meli"
    assert event.processed_at is None

    fetched = await repo.get(event.id)
    assert fetched is not None
    assert fetched.topic == "orders_v2"
    assert fetched.payload["user_id"] == 123456789


async def test_webhook_event_accepts_explicit_status(db_session: AsyncSession):
    repo = WebhookEventRepository(db_session)

    event = await repo.create(
        topic="orders_v2",
        resource="/orders/1",
        meli_user_id=999,
        payload={},
        status="skipped",
    )
    await db_session.commit()

    assert event.status == "skipped"


async def test_webhook_event_get_unknown_id_returns_none(db_session: AsyncSession):
    assert await WebhookEventRepository(db_session).get(999999) is None


# --- event_outbox --------------------------------------------------------------------


async def test_add_outbox_event_writes_pending_row_with_occurred_at(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-outbox-a")
    repo = EventOutboxRepository(db_session)

    row = await repo.add_outbox_event(
        tenant.id, "new_order", {"order_id": 42, "meli_order_id": 2000003508419500}
    )
    await db_session.commit()

    assert row.status == "pending"
    assert row.occurred_at is not None
    assert row.version == 1
    assert row.attempts == 0
    assert row.delivered_at is None
    assert row.payload["order_id"] == 42


async def test_add_outbox_event_generates_a_fresh_uuid_each_time(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-outbox-b")
    repo = EventOutboxRepository(db_session)

    row1 = await repo.add_outbox_event(tenant.id, "new_order", {"a": 1})
    row2 = await repo.add_outbox_event(tenant.id, "new_order", {"a": 2})
    await db_session.commit()

    assert isinstance(row1.event_id, uuid.UUID)
    assert isinstance(row2.event_id, uuid.UUID)
    assert row1.event_id != row2.event_id


async def test_event_outbox_is_isolated_by_tenant(db_session: AsyncSession):
    from sqlalchemy import select

    from app.models.event_outbox import EventOutbox

    tenant_a = await _create_tenant(db_session, "loja-outbox-c")
    tenant_b = await _create_tenant(db_session, "loja-outbox-d")
    repo = EventOutboxRepository(db_session)
    await repo.add_outbox_event(tenant_a.id, "new_order", {"x": 1})
    await db_session.commit()

    result = await db_session.execute(
        select(EventOutbox).where(EventOutbox.tenant_id == tenant_b.id)
    )
    assert result.scalars().all() == []

    result = await db_session.execute(
        select(EventOutbox).where(EventOutbox.tenant_id == tenant_a.id)
    )
    assert len(result.scalars().all()) == 1
