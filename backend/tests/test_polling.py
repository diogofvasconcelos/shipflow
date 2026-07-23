import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_arq_pool
from app.core.db import get_db
from app.core.security import hash_password
from app.integrations.meli.errors import MeliError
from app.main import create_app
from app.models.order import Order
from app.repositories.meli_account import MeliAccountRepository
from app.repositories.poll_cursor import PollCursorRepository
from app.repositories.shipment import ShipmentRepository
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository
from app.workers.polling import poll_orders, sync_open_shipments
from tests.test_webhooks import FakeArqPool

FIXTURES = Path(__file__).parent / "fixtures" / "meli"

ORDER_SINGLE = 2000003508419500
PACK_A = 2000003508419601
PACK_B = 2000003508419602
SHIPMENT_S1 = 44444444444
SHIPMENT_S2 = 55555555555


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakePollingClient:
    """Only implements what the polling worker + IngestionService touch:
    search_orders (paginated), get_order, get_shipment."""

    def __init__(self, pages=None, orders=None, shipments=None):
        self.pages = pages or []
        self.orders = orders or {}
        self.shipments = shipments or {}
        self.search_calls: list[tuple[int, datetime, int]] = []
        self.get_order_calls: list[int] = []
        self.get_shipment_calls: list[int] = []
        self.fail_order_id: int | None = None
        self.fail_search = False

    async def search_orders(self, account, from_dt, offset=0):
        self.search_calls.append((account.id, from_dt, offset))
        if self.fail_search:
            raise MeliError("search boom")
        return self.pages[len(self.search_calls) - 1]

    async def get_order(self, account, meli_order_id):
        self.get_order_calls.append(meli_order_id)
        if meli_order_id == self.fail_order_id:
            raise MeliError("ingest boom")
        return deepcopy(self.orders[meli_order_id])

    async def get_shipment(self, account, meli_shipment_id):
        self.get_shipment_calls.append(meli_shipment_id)
        return deepcopy(self.shipments[meli_shipment_id])

    async def aclose(self):
        pass


def _page(order_ids: list[int], total: int) -> dict:
    return {"results": [{"id": oid} for oid in order_ids], "paging": {"total": total}}


def _utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes on read; normalize for comparison."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@pytest_asyncio.fixture
async def sync_client(db_session: AsyncSession):
    """Own app instance (mirrors tests/test_webhooks.py's webhook_client): the
    shared `client` fixture builds its own app too, so overriding app.main.app
    would target the wrong instance."""
    app = create_app()
    pool = FakeArqPool()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_arq_pool] = lambda: pool

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, pool


async def _seed_account(db_session: AsyncSession, slug: str, meli_user_id: int, status="active"):
    tenant = await TenantRepository(db_session).create(name=slug, slug=slug)
    account = await MeliAccountRepository(db_session).create(
        tenant.id,
        meli_user_id=meli_user_id,
        nickname=slug.upper(),
        access_token="APP_USR-x",
        refresh_token="TG-x",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    if status != "active":
        await MeliAccountRepository(db_session).set_status(tenant.id, account.id, status)
    await db_session.commit()
    return account


# --- account filtering -------------------------------------------------------------


async def test_poll_orders_skips_inactive_accounts(db_session, db_session_factory):
    active = await _seed_account(db_session, "loja-active", 1001)
    await _seed_account(db_session, "loja-disabled", 1002, status="disabled")
    client = FakePollingClient(pages=[_page([], 0)])

    await poll_orders({"session_factory": db_session_factory, "meli_client": client})

    assert len(client.search_calls) == 1
    assert client.search_calls[0][0] == active.id


# --- lookback windows ----------------------------------------------------------------


async def test_poll_orders_first_run_uses_24h_lookback(db_session, db_session_factory):
    await _seed_account(db_session, "loja-first", 1003)
    client = FakePollingClient(pages=[_page([], 0)])

    before = datetime.now(UTC)
    await poll_orders({"session_factory": db_session_factory, "meli_client": client})
    after = datetime.now(UTC)

    from_dt = client.search_calls[0][1]
    assert (before - timedelta(hours=24)) <= from_dt <= (after - timedelta(hours=24))


async def test_poll_orders_uses_cursor_minus_10min_overlap(db_session, db_session_factory):
    account = await _seed_account(db_session, "loja-cursor", 1004)
    known_cursor = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    await PollCursorRepository(db_session).set_cursor(account.id, known_cursor)
    await db_session.commit()
    client = FakePollingClient(pages=[_page([], 0)])

    await poll_orders({"session_factory": db_session_factory, "meli_client": client})

    assert client.search_calls[0][1] == known_cursor - timedelta(minutes=10)


# --- cursor advancement --------------------------------------------------------------


async def test_cursor_advances_only_after_full_success(db_session, db_session_factory):
    account = await _seed_account(db_session, "loja-success", 1005)
    client = FakePollingClient(
        pages=[_page([ORDER_SINGLE], 1)],
        orders={ORDER_SINGLE: load("order_single.json")},
        shipments={SHIPMENT_S1: load("shipment_s1.json")},
    )

    before = datetime.now(UTC)
    await poll_orders({"session_factory": db_session_factory, "meli_client": client})

    async with db_session_factory() as check:
        cursor = await PollCursorRepository(check).get(account.id)
    assert cursor is not None
    assert _utc(cursor) >= before


async def test_cursor_does_not_advance_when_a_pass_fails(db_session, db_session_factory):
    account = await _seed_account(db_session, "loja-fail", 1006)
    known_cursor = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    await PollCursorRepository(db_session).set_cursor(account.id, known_cursor)
    await db_session.commit()

    client = FakePollingClient(
        pages=[_page([ORDER_SINGLE], 1)], orders={ORDER_SINGLE: load("order_single.json")}
    )
    client.fail_order_id = ORDER_SINGLE

    await poll_orders({"session_factory": db_session_factory, "meli_client": client})

    async with db_session_factory() as check:
        cursor = await PollCursorRepository(check).get(account.id)
    assert _utc(cursor) == known_cursor  # unchanged


# --- pagination ------------------------------------------------------------------


async def test_poll_orders_paginates_fully(db_session, db_session_factory):
    await _seed_account(db_session, "loja-page", 1007)
    client = FakePollingClient(
        pages=[_page([PACK_A], 2), _page([PACK_B], 2)],
        orders={PACK_A: load("order_pack_a.json"), PACK_B: load("order_pack_b.json")},
        shipments={SHIPMENT_S2: load("shipment_s2.json")},
    )

    await poll_orders({"session_factory": db_session_factory, "meli_client": client})

    assert [c[2] for c in client.search_calls] == [0, 1]  # offsets
    async with db_session_factory() as check:
        orders = (await check.execute(select(Order))).scalars().all()
    assert {o.meli_order_id for o in orders} == {PACK_A, PACK_B}


# --- overlap / idempotency ----------------------------------------------------------


async def test_overlapping_polls_cause_no_duplicate_orders(db_session, db_session_factory):
    await _seed_account(db_session, "loja-overlap", 1008)
    order_payload = load("order_single.json")

    def make_client():
        return FakePollingClient(
            pages=[_page([ORDER_SINGLE], 1)],
            orders={ORDER_SINGLE: deepcopy(order_payload)},
            shipments={SHIPMENT_S1: load("shipment_s1.json")},
        )

    ctx = {"session_factory": db_session_factory, "meli_client": make_client()}
    await poll_orders(ctx)
    ctx["meli_client"] = make_client()  # next tick, same underlying data (overlap window)
    await poll_orders(ctx)

    async with db_session_factory() as check:
        orders = (await check.execute(select(Order))).scalars().all()
    assert len(orders) == 1  # idempotent upsert (stale-skip) proven in T6


async def test_search_failure_aborts_pass_without_advancing_cursor(db_session, db_session_factory):
    account = await _seed_account(db_session, "loja-search-fail", 1010)
    known_cursor = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    await PollCursorRepository(db_session).set_cursor(account.id, known_cursor)
    await db_session.commit()
    client = FakePollingClient()
    client.fail_search = True

    await poll_orders({"session_factory": db_session_factory, "meli_client": client})

    async with db_session_factory() as check:
        cursor = await PollCursorRepository(check).get(account.id)
    assert _utc(cursor) == known_cursor


async def test_poll_orders_creates_and_closes_its_own_client_when_none_given(db_session_factory):
    # No accounts seeded: exercises the owns_client=True branch (real MeliClient
    # instantiated + closed) without making any actual network call.
    await poll_orders({"session_factory": db_session_factory})


# --- sync_open_shipments ---------------------------------------------------------


async def test_sync_open_shipments_only_targets_ready_to_ship(db_session, db_session_factory):
    account = await _seed_account(db_session, "loja-sync", 1009)
    repo = ShipmentRepository(db_session)
    await repo.upsert(
        account.tenant_id,
        account.id,
        meli_shipment_id=SHIPMENT_S1,
        meli_status="ready_to_ship",
        raw={},
    )
    await repo.upsert(
        account.tenant_id,
        account.id,
        meli_shipment_id=SHIPMENT_S2,
        meli_status="shipped",
        raw={},
    )
    await db_session.commit()
    client = FakePollingClient(shipments={SHIPMENT_S1: load("shipment_s1.json")})

    await sync_open_shipments({"session_factory": db_session_factory, "meli_client": client})

    assert client.get_shipment_calls == [SHIPMENT_S1]


async def test_sync_open_shipments_continues_after_one_failure(db_session, db_session_factory):
    account = await _seed_account(db_session, "loja-sync-fail", 1011)
    repo = ShipmentRepository(db_session)
    await repo.upsert(
        account.tenant_id,
        account.id,
        meli_shipment_id=SHIPMENT_S1,
        meli_status="ready_to_ship",
        raw={},
    )
    await repo.upsert(
        account.tenant_id,
        account.id,
        meli_shipment_id=SHIPMENT_S2,
        meli_status="ready_to_ship",
        raw={},
    )
    await db_session.commit()
    # Only SHIPMENT_S2's fixture is provided — S1 raises KeyError inside get_shipment,
    # proving one bad shipment doesn't stop the rest of the sweep.
    client = FakePollingClient(shipments={SHIPMENT_S2: load("shipment_s2.json")})

    await sync_open_shipments({"session_factory": db_session_factory, "meli_client": client})

    assert set(client.get_shipment_calls) == {SHIPMENT_S1, SHIPMENT_S2}


# --- POST /api/orders/sync ---------------------------------------------------------


async def test_manual_sync_endpoint_enqueues_poll_orders(sync_client, db_session):
    client, pool = sync_client
    tenant = await TenantRepository(db_session).create(name="loja-manual", slug="loja-manual")
    await UserRepository(db_session).create(
        tenant.id,
        email="op@loja-manual.com",
        password_hash=hash_password("s3nha-forte"),
        role="operator",
    )
    await db_session.commit()
    await client.post("/login", data={"email": "op@loja-manual.com", "password": "s3nha-forte"})

    response = await client.post("/api/orders/sync")

    assert response.status_code == 202
    assert response.json() == {"detail": "sync enfileirado"}
    assert pool.jobs == [("poll_orders", ())]


async def test_manual_sync_endpoint_requires_authentication(sync_client):
    client, _pool = sync_client
    response = await client.post("/api/orders/sync")
    assert response.status_code == 401
