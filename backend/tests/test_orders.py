"""T8 — orders list/detail (API.md §4). The security-critical behaviour here is
tenant isolation: a tenant never sees another tenant's orders, and a cross-tenant
detail lookup is a 404 (not a 403 — that would leak the row's existence).

Auth is faked by overriding the require_user dependency with a user we created,
so we can 'log in' as tenant A or B at will without touching session cookies.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user
from app.core.db import get_db
from app.main import create_app
from app.models.user import User
from app.repositories.meli_account import MeliAccountRepository
from app.repositories.order import OrderRepository
from app.repositories.order_item import OrderItemRepository
from app.repositories.shipment import ShipmentRepository
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository
from app.repositories.variant import VariantRepository

# --- seeding helpers -------------------------------------------------------

_next_meli_user_id = 100  # meli_user_id is globally unique — hand out distinct ones


async def _make_tenant(session: AsyncSession, slug: str):
    """A tenant + one operator user + one connected ML account."""
    global _next_meli_user_id
    _next_meli_user_id += 1
    tenant = await TenantRepository(session).create(name=slug, slug=slug)
    user = await UserRepository(session).create(
        tenant.id, email=f"op@{slug}.com", password_hash="x", role="operator"
    )
    account = await MeliAccountRepository(session).create(
        tenant.id,
        meli_user_id=_next_meli_user_id,
        nickname=slug.upper(),
        access_token="APP_USR-x",
        refresh_token="TG-x",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    return tenant, user, account


async def _seed_order(
    session: AsyncSession,
    tenant,
    account,
    *,
    meli_order_id: int,
    status: str = "paid",
    buyer: str = "COMPRADOR",
    created: datetime | None = None,
):
    when = created or datetime.now(UTC)
    order, _ = await OrderRepository(session).upsert(
        tenant.id,
        account.id,
        meli_order_id=meli_order_id,
        meli_status=status,
        total_amount=Decimal("100.00"),
        meli_created_at=when,
        meli_last_updated_at=when,
        raw={},
        buyer_nickname=buyer,
    )
    return order


# --- client fixture (authenticated as a chosen user) -----------------------


def _client_as(user: User, db_session: AsyncSession) -> AsyncClient:
    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_user] = lambda: user

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest_asyncio.fixture
async def two_tenants(db_session: AsyncSession):
    """Tenant A and tenant B, each with a user and account, committed."""
    a = await _make_tenant(db_session, "loja-a")
    b = await _make_tenant(db_session, "loja-b")
    await db_session.commit()
    return a, b


# --- tests -----------------------------------------------------------------


async def test_list_is_tenant_isolated(two_tenants, db_session):
    (tenant_a, user_a, account_a), (tenant_b, _user_b, account_b) = two_tenants
    await _seed_order(db_session, tenant_a, account_a, meli_order_id=111, buyer="ANA")
    await _seed_order(db_session, tenant_b, account_b, meli_order_id=222, buyer="BENTO")
    await db_session.commit()

    async with _client_as(user_a, db_session) as client:
        resp = await client.get("/api/orders")

    assert resp.status_code == 200
    ids = [row["meli_order_id"] for row in resp.json()["items"]]
    assert ids == [111]  # tenant B's order 222 is invisible


async def test_filter_by_status(two_tenants, db_session):
    (tenant_a, user_a, account_a), *_ = two_tenants
    await _seed_order(db_session, tenant_a, account_a, meli_order_id=1, status="paid")
    await _seed_order(db_session, tenant_a, account_a, meli_order_id=2, status="cancelled")
    await db_session.commit()

    async with _client_as(user_a, db_session) as client:
        resp = await client.get("/api/orders", params={"status": "cancelled"})

    ids = [row["meli_order_id"] for row in resp.json()["items"]]
    assert ids == [2]


async def test_filter_by_q_matches_order_id_and_buyer(two_tenants, db_session):
    (tenant_a, user_a, account_a), *_ = two_tenants
    await _seed_order(db_session, tenant_a, account_a, meli_order_id=99001, buyer="ANA")
    await _seed_order(db_session, tenant_a, account_a, meli_order_id=88002, buyer="BRUNO")
    await db_session.commit()

    async with _client_as(user_a, db_session) as client:
        by_id = await client.get("/api/orders", params={"q": "99001"})
        by_buyer = await client.get("/api/orders", params={"q": "bruno"})  # ilike: case-insensitive

    assert [r["meli_order_id"] for r in by_id.json()["items"]] == [99001]
    assert [r["meli_order_id"] for r in by_buyer.json()["items"]] == [88002]


async def test_pagination(two_tenants, db_session):
    (tenant_a, user_a, account_a), *_ = two_tenants
    for i in range(51):
        await _seed_order(db_session, tenant_a, account_a, meli_order_id=1000 + i)
    await db_session.commit()

    async with _client_as(user_a, db_session) as client:
        page1 = (await client.get("/api/orders", params={"page": 1})).json()
        page2 = (await client.get("/api/orders", params={"page": 2})).json()

    assert page1["total"] == 51
    assert len(page1["items"]) == 50
    assert len(page2["items"]) == 1


async def test_detail_full_shape(two_tenants, db_session):
    (tenant_a, user_a, account_a), *_ = two_tenants
    order = await _seed_order(db_session, tenant_a, account_a, meli_order_id=555, buyer="ANA")
    shipment = await ShipmentRepository(db_session).upsert(
        tenant_a.id,
        account_a.id,
        meli_shipment_id=44444444444,
        meli_status="ready_to_ship",
        meli_substatus="ready_to_print",
        raw={},
    )
    order.shipment_id = shipment.id
    variant = await VariantRepository(db_session).upsert(
        tenant_a.id, meli_item_id="MLB1", variation_id=1, model_name="Tênis Runner", size="41"
    )
    await OrderItemRepository(db_session).create(
        tenant_a.id,
        order.id,
        meli_item_id="MLB1",
        title="Tênis Runner Masculino",
        quantity=1,
        unit_price=Decimal("289.90"),
        variant_id=variant.id,
        size="41",
    )
    await db_session.commit()

    async with _client_as(user_a, db_session) as client:
        resp = await client.get(f"/api/orders/{order.id}")

    body = resp.json()
    assert resp.status_code == 200
    assert body["meli_order_id"] == 555
    assert body["account"]["nickname"] == "LOJA-A"
    assert body["total_amount"] == "100.00"  # money is a fixed-2dp string
    assert body["shipment"]["meli_substatus"] == "ready_to_print"
    assert body["items"][0]["unit_price"] == "289.90"
    assert body["items"][0]["variant"]["internal_code"] == variant.internal_code


async def test_detail_cross_tenant_is_404(two_tenants, db_session):
    (tenant_a, user_a, _account_a), (tenant_b, _user_b, account_b) = two_tenants
    order_b = await _seed_order(db_session, tenant_b, account_b, meli_order_id=777)
    await db_session.commit()

    # user_a asks for tenant B's order by its real id → indistinguishable from missing
    async with _client_as(user_a, db_session) as client:
        resp = await client.get(f"/api/orders/{order_b.id}")

    assert resp.status_code == 404


async def test_detail_missing_is_404(two_tenants, db_session):
    (_tenant_a, user_a, _account_a), *_ = two_tenants
    async with _client_as(user_a, db_session) as client:
        resp = await client.get("/api/orders/999999")
    assert resp.status_code == 404


async def test_list_requires_auth(db_session):
    # No require_user override here → the real dependency runs and rejects.
    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/orders")
    assert resp.status_code == 401
