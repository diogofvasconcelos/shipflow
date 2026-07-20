from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.meli_account import MeliAccountRepository
from app.repositories.order import OrderRepository
from app.repositories.order_item import OrderItemRepository
from app.repositories.shipment import ShipmentRepository
from app.repositories.tenant import TenantRepository

EXPIRES = datetime.now(UTC) + timedelta(hours=6)


async def _create_tenant_with_account(db_session: AsyncSession, slug: str, meli_user_id: int):
    tenant = await TenantRepository(db_session).create(name=slug, slug=slug)
    account = await MeliAccountRepository(db_session).create(
        tenant.id,
        meli_user_id=meli_user_id,
        nickname=slug.upper(),
        access_token="APP_USR-x",
        refresh_token="TG-x",
        access_token_expires_at=EXPIRES,
    )
    await db_session.commit()
    return tenant, account


# --- shipments -------------------------------------------------------------------


async def test_duplicate_meli_shipment_id_per_tenant_is_rejected(db_session: AsyncSession):
    from app.models.shipment import Shipment

    tenant, account = await _create_tenant_with_account(db_session, "loja-ship-a", 111)

    db_session.add(
        Shipment(
            tenant_id=tenant.id,
            meli_account_id=account.id,
            meli_shipment_id=999999,
            meli_status="ready_to_ship",
            raw={},
        )
    )
    await db_session.commit()

    db_session.add(
        Shipment(
            tenant_id=tenant.id,
            meli_account_id=account.id,
            meli_shipment_id=999999,
            meli_status="ready_to_ship",
            raw={},
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_shipment_of_tenant_a_is_invisible_via_tenant_b_repository_call(
    db_session: AsyncSession,
):
    from app.models.shipment import Shipment

    tenant_a, account_a = await _create_tenant_with_account(db_session, "loja-ship-b", 112)
    tenant_b, _ = await _create_tenant_with_account(db_session, "loja-ship-c", 113)

    shipment = Shipment(
        tenant_id=tenant_a.id,
        meli_account_id=account_a.id,
        meli_shipment_id=555555,
        meli_status="ready_to_ship",
        raw={"foo": "bar"},
    )
    db_session.add(shipment)
    await db_session.commit()

    repo = ShipmentRepository(db_session)
    assert await repo.get(tenant_b.id, shipment.id) is None
    assert await repo.get(tenant_a.id, shipment.id) is not None
    assert await repo.get_by_meli_shipment_id(tenant_b.id, 555555) is None
    assert await repo.get_by_meli_shipment_id(tenant_a.id, 555555) is not None


# --- orders ------------------------------------------------------------------------


async def test_duplicate_meli_order_id_per_tenant_is_rejected(db_session: AsyncSession):
    from app.models.order import Order

    tenant, account = await _create_tenant_with_account(db_session, "loja-order-a", 211)
    now = datetime.now(UTC)

    db_session.add(
        Order(
            tenant_id=tenant.id,
            meli_account_id=account.id,
            meli_order_id=2000000000001,
            meli_status="paid",
            total_amount=Decimal("100.00"),
            meli_created_at=now,
            meli_last_updated_at=now,
            raw={},
        )
    )
    await db_session.commit()

    db_session.add(
        Order(
            tenant_id=tenant.id,
            meli_account_id=account.id,
            meli_order_id=2000000000001,
            meli_status="paid",
            total_amount=Decimal("50.00"),
            meli_created_at=now,
            meli_last_updated_at=now,
            raw={},
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_order_of_tenant_a_is_invisible_via_tenant_b_repository_call(
    db_session: AsyncSession,
):
    from app.models.order import Order

    tenant_a, account_a = await _create_tenant_with_account(db_session, "loja-order-b", 212)
    tenant_b, _ = await _create_tenant_with_account(db_session, "loja-order-c", 213)
    now = datetime.now(UTC)

    order = Order(
        tenant_id=tenant_a.id,
        meli_account_id=account_a.id,
        meli_order_id=2000000000002,
        meli_status="paid",
        total_amount=Decimal("289.90"),
        currency="BRL",
        meli_created_at=now,
        meli_last_updated_at=now,
        raw={"id": 2000000000002},
    )
    db_session.add(order)
    await db_session.commit()

    repo = OrderRepository(db_session)
    assert await repo.get(tenant_b.id, order.id) is None
    assert await repo.get(tenant_a.id, order.id) is not None
    assert await repo.get_by_meli_order_id(tenant_b.id, 2000000000002) is None
    fetched = await repo.get_by_meli_order_id(tenant_a.id, 2000000000002)
    assert fetched is not None
    assert fetched.currency == "BRL"


# --- order_items --------------------------------------------------------------------


async def test_order_item_created_with_nullable_variant_and_listed_by_order(
    db_session: AsyncSession,
):
    from app.models.order import Order

    tenant, account = await _create_tenant_with_account(db_session, "loja-item-a", 311)
    now = datetime.now(UTC)
    order = Order(
        tenant_id=tenant.id,
        meli_account_id=account.id,
        meli_order_id=2000000000003,
        meli_status="paid",
        total_amount=Decimal("289.90"),
        meli_created_at=now,
        meli_last_updated_at=now,
        raw={},
    )
    db_session.add(order)
    await db_session.flush()

    item_repo = OrderItemRepository(db_session)
    item = await item_repo.create(
        tenant.id,
        order.id,
        meli_item_id="MLB3333333333",
        title="Tênis Runner Preto",
        quantity=1,
        unit_price=Decimal("289.90"),
        size="41",
    )
    await db_session.commit()

    assert item.variant_id is None
    items = await item_repo.list_by_order(tenant.id, order.id)
    assert len(items) == 1
    assert items[0].title == "Tênis Runner Preto"


async def test_order_items_are_isolated_by_tenant(db_session: AsyncSession):
    from app.models.order import Order

    tenant_a, account_a = await _create_tenant_with_account(db_session, "loja-item-b", 312)
    tenant_b, _ = await _create_tenant_with_account(db_session, "loja-item-c", 313)
    now = datetime.now(UTC)
    order = Order(
        tenant_id=tenant_a.id,
        meli_account_id=account_a.id,
        meli_order_id=2000000000004,
        meli_status="paid",
        total_amount=Decimal("10.00"),
        meli_created_at=now,
        meli_last_updated_at=now,
        raw={},
    )
    db_session.add(order)
    await db_session.flush()
    await OrderItemRepository(db_session).create(
        tenant_a.id,
        order.id,
        meli_item_id="MLB1",
        title="X",
        quantity=1,
        unit_price=Decimal("10.00"),
    )
    await db_session.commit()

    items_wrong_tenant = await OrderItemRepository(db_session).list_by_order(tenant_b.id, order.id)
    assert items_wrong_tenant == []
