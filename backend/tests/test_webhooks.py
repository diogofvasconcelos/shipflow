from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_arq_pool
from app.core.config import get_settings
from app.core.db import get_db
from app.main import create_app
from app.models.webhook_event import WebhookEvent
from app.repositories.meli_account import MeliAccountRepository
from app.repositories.tenant import TenantRepository

APP_ID = "5503910054141466"


class FakeArqPool:
    """Models the two independent Redis surfaces the webhook touches: the dedup
    SET NX and enqueue_job. Each can be made to fail on its own — §10 treats them
    as separate failure points."""

    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.jobs: list[tuple] = []
        self.set_raises = False
        self.enqueue_raises = False

    async def set(self, key, value, nx=False, ex=None):
        if self.set_raises:
            raise ConnectionError("redis down")
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True

    async def enqueue_job(self, name, *args, **kwargs):
        if self.enqueue_raises:
            raise ConnectionError("redis down")
        self.jobs.append((name, args))
        return SimpleNamespace(job_id="1")


@pytest.fixture
def arq_pool():
    return FakeArqPool()


@pytest.fixture(autouse=True)
def _fixed_meli_client_id():
    settings = get_settings()
    original = settings.meli_client_id
    settings.meli_client_id = APP_ID
    yield
    settings.meli_client_id = original


@pytest_asyncio.fixture
async def webhook_client(db_session: AsyncSession, arq_pool: FakeArqPool):
    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_arq_pool] = lambda: arq_pool

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_account(db_session: AsyncSession, meli_user_id: int) -> None:
    tenant = await TenantRepository(db_session).create(name="loja-wh", slug="loja-wh")
    await MeliAccountRepository(db_session).create(
        tenant.id,
        meli_user_id=meli_user_id,
        nickname="LOJA_WH",
        access_token="APP_USR-x",
        refresh_token="TG-x",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    await db_session.commit()


def _body(**over):
    body = {
        "resource": "/orders/2000003508419500",
        "user_id": 123456789,
        "topic": "orders_v2",
        "application_id": int(APP_ID),
        "attempts": 1,
        "sent": "2026-07-13T14:00:00Z",
    }
    body.update(over)
    return body


async def _events(db_session: AsyncSession) -> list[WebhookEvent]:
    result = await db_session.execute(select(WebhookEvent))
    return list(result.scalars().all())


# --- happy path ------------------------------------------------------------------


async def test_happy_path_persists_received_and_enqueues(webhook_client, db_session, arq_pool):
    await _seed_account(db_session, 123456789)

    response = await webhook_client.post("/webhooks/meli", json=_body())

    assert response.status_code == 200
    assert response.json() == {}
    events = await _events(db_session)
    assert len(events) == 1
    assert events[0].status == "received"
    assert arq_pool.jobs == [("process_meli_notification", (events[0].id,))]


# --- dedup -----------------------------------------------------------------------


async def test_duplicate_within_window_is_skipped_and_not_enqueued(
    webhook_client, db_session, arq_pool
):
    await _seed_account(db_session, 123456789)

    first = await webhook_client.post("/webhooks/meli", json=_body())
    second = await webhook_client.post("/webhooks/meli", json=_body())

    assert first.status_code == 200
    assert second.status_code == 200
    events = await _events(db_session)
    statuses = sorted(e.status for e in events)
    assert statuses == ["received", "skipped"]
    assert len(arq_pool.jobs) == 1  # only the first enqueued


# --- validation ------------------------------------------------------------------


async def test_foreign_application_id_is_skipped_200(webhook_client, db_session, arq_pool):
    await _seed_account(db_session, 123456789)

    response = await webhook_client.post("/webhooks/meli", json=_body(application_id=999999999))

    assert response.status_code == 200
    events = await _events(db_session)
    assert len(events) == 1
    assert events[0].status == "skipped"
    assert arq_pool.jobs == []


async def test_unknown_user_id_is_skipped_200(webhook_client, db_session, arq_pool):
    # no account seeded
    response = await webhook_client.post("/webhooks/meli", json=_body(user_id=42))

    assert response.status_code == 200
    events = await _events(db_session)
    assert len(events) == 1
    assert events[0].status == "skipped"
    assert arq_pool.jobs == []


# --- redis failure modes ---------------------------------------------------------


async def test_redis_dedup_down_still_receives_and_enqueues(webhook_client, db_session, arq_pool):
    await _seed_account(db_session, 123456789)
    arq_pool.set_raises = True  # dedup layer unavailable

    response = await webhook_client.post("/webhooks/meli", json=_body())

    assert response.status_code == 200
    events = await _events(db_session)
    assert len(events) == 1
    assert events[0].status == "received"
    assert len(arq_pool.jobs) == 1


async def test_enqueue_failure_returns_500(webhook_client, db_session, arq_pool):
    await _seed_account(db_session, 123456789)
    arq_pool.enqueue_raises = True

    response = await webhook_client.post("/webhooks/meli", json=_body())

    assert response.status_code == 500
    # the received row is still durably persisted (poller/redelivery reprocess it)
    events = await _events(db_session)
    assert len(events) == 1
    assert events[0].status == "received"


async def test_non_json_body_returns_200_without_crashing(webhook_client, db_session):
    response = await webhook_client.post("/webhooks/meli", content=b"not json")

    assert response.status_code == 200
    assert await _events(db_session) == []
