from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.meli import client as client_module
from app.integrations.meli.client import MeliClient
from app.integrations.meli.errors import MeliError, MeliReauthRequired
from app.repositories.meli_account import MeliAccountRepository
from app.repositories.tenant import TenantRepository

TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

REFRESH_PAYLOAD = {
    "access_token": "APP_USR-new-access",
    "refresh_token": "TG-new-refresh",
    "expires_in": 21600,
    "user_id": 123456789,
}


class FakeRedis:
    """SET NX EX + DELETE — the only redis surface the refresh lock uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def aclose(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _meli_app_credentials():
    settings = get_settings()
    original = (settings.meli_client_id, settings.meli_client_secret, settings.meli_redirect_uri)
    settings.meli_client_id = "APP123"
    settings.meli_client_secret = "SECRET456"
    settings.meli_redirect_uri = "https://shipflow.test/api/meli/oauth/callback"
    yield
    settings.meli_client_id, settings.meli_client_secret, settings.meli_redirect_uri = original


@pytest.fixture
def meli_client(db_session_factory):
    return MeliClient(session_factory=db_session_factory, redis=FakeRedis())


async def create_account(
    db_session: AsyncSession, *, expires_in: timedelta, meli_user_id: int = 123456789
):
    tenant = await TenantRepository(db_session).create(name="loja-ml", slug="loja-ml")
    account = await MeliAccountRepository(db_session).create(
        tenant.id,
        meli_user_id=meli_user_id,
        nickname="LOJA_ML",
        access_token="APP_USR-old-access",
        refresh_token="TG-old-refresh",
        access_token_expires_at=datetime.now(UTC) + expires_in,
    )
    await db_session.commit()
    return account


@pytest.fixture
def sleep_recorder(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    return delays


# --- OAuth grants ---------------------------------------------------------------


@respx.mock
async def test_exchange_code_sends_authorization_code_grant(meli_client):
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=REFRESH_PAYLOAD))

    payload = await meli_client.exchange_code("THE-CODE")

    assert payload == REFRESH_PAYLOAD
    form = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "THE-CODE"
    assert form["client_id"] == "APP123"
    assert form["client_secret"] == "SECRET456"
    assert form["redirect_uri"] == "https://shipflow.test/api/meli/oauth/callback"


# --- Lazy refresh ----------------------------------------------------------------


@respx.mock
async def test_lazy_refresh_rotates_tokens_and_uses_new_access_token(
    db_session, db_session_factory, meli_client
):
    account = await create_account(db_session, expires_in=timedelta(minutes=2))
    refresh_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=REFRESH_PAYLOAD)
    )
    order_route = respx.get("https://api.mercadolibre.com/orders/777").mock(
        return_value=httpx.Response(200, json={"id": 777})
    )

    result = await meli_client.get_order(account, 777)

    assert result == {"id": 777}
    assert refresh_route.call_count == 1
    auth_header = order_route.calls.last.request.headers["Authorization"]
    assert auth_header == "Bearer APP_USR-new-access"

    # Rotated pair persisted — ML refresh tokens are single-use.
    async with db_session_factory() as check_session:
        tokens = await MeliAccountRepository(check_session).get_decrypted_tokens(
            account.tenant_id, account.id
        )
    assert tokens == ("APP_USR-new-access", "TG-new-refresh")


@respx.mock
async def test_fresh_token_is_used_without_any_refresh_call(db_session, meli_client):
    account = await create_account(db_session, expires_in=timedelta(hours=6))
    refresh_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=REFRESH_PAYLOAD)
    )
    order_route = respx.get("https://api.mercadolibre.com/orders/1").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    await meli_client.get_order(account, 1)

    assert refresh_route.call_count == 0
    assert order_route.calls.last.request.headers["Authorization"] == "Bearer APP_USR-old-access"


# --- 401 → refresh → retry-once ---------------------------------------------------


@respx.mock
async def test_401_triggers_one_refresh_and_one_retry(db_session, meli_client):
    account = await create_account(db_session, expires_in=timedelta(hours=6))
    refresh_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=REFRESH_PAYLOAD)
    )
    order_route = respx.get("https://api.mercadolibre.com/orders/5").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"id": 5}),
        ]
    )

    result = await meli_client.get_order(account, 5)

    assert result == {"id": 5}
    assert refresh_route.call_count == 1
    assert order_route.call_count == 2
    retry_auth = order_route.calls.last.request.headers["Authorization"]
    assert retry_auth == "Bearer APP_USR-new-access"


# --- invalid_grant → reauth_required ----------------------------------------------


@respx.mock
async def test_invalid_grant_flips_account_to_reauth_required(
    db_session, db_session_factory, meli_client
):
    account = await create_account(db_session, expires_in=timedelta(minutes=2))
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))

    with pytest.raises(MeliReauthRequired):
        await meli_client.refresh_token(account, force=True)

    assert account.status == "reauth_required"
    async with db_session_factory() as check_session:
        persisted = await MeliAccountRepository(check_session).get(account.tenant_id, account.id)
    assert persisted.status == "reauth_required"


# --- 429 handling ------------------------------------------------------------------


@respx.mock
async def test_429_honors_retry_after_header(db_session, meli_client, sleep_recorder):
    account = await create_account(db_session, expires_in=timedelta(hours=6))
    respx.get("https://api.mercadolibre.com/orders/9").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"id": 9}),
        ]
    )

    result = await meli_client.get_order(account, 9)

    assert result == {"id": 9}
    assert sleep_recorder == [7.0]


@respx.mock
async def test_429_without_retry_after_backs_off_exponentially_then_raises(
    db_session, meli_client, sleep_recorder
):
    account = await create_account(db_session, expires_in=timedelta(hours=6))
    respx.get("https://api.mercadolibre.com/orders/9").mock(return_value=httpx.Response(429))

    with pytest.raises(MeliError):
        await meli_client.get_order(account, 9)

    # 5 attempts → 4 backoff sleeps of base 1, 2, 4, 8 (+ jitter < 1s each).
    assert len(sleep_recorder) == 4
    for delay, base in zip(sleep_recorder, [1, 2, 4, 8], strict=True):
        assert base <= delay < base + 1


@respx.mock
async def test_5xx_retries_then_raises(db_session, meli_client, sleep_recorder):
    account = await create_account(db_session, expires_in=timedelta(hours=6))
    route = respx.get("https://api.mercadolibre.com/shipments/3").mock(
        return_value=httpx.Response(502)
    )

    with pytest.raises(MeliError):
        await meli_client.get_shipment(account, 3)

    assert route.call_count == 3


# --- Single-flight refresh under concurrency ---------------------------------------


@respx.mock
async def test_concurrent_calls_trigger_exactly_one_refresh(db_session, meli_client):
    import asyncio

    account = await create_account(db_session, expires_in=timedelta(minutes=2))
    refresh_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=REFRESH_PAYLOAD)
    )
    respx.get("https://api.mercadolibre.com/orders/42").mock(
        return_value=httpx.Response(200, json={"id": 42})
    )

    results = await asyncio.gather(
        meli_client.get_order(account, 42),
        meli_client.get_order(account, 42),
    )

    assert results == [{"id": 42}, {"id": 42}]
    assert refresh_route.call_count == 1


# --- Label PDF ----------------------------------------------------------------------


@respx.mock
async def test_get_label_pdf_returns_raw_bytes(db_session, meli_client):
    account = await create_account(db_session, expires_in=timedelta(hours=6))
    route = respx.get("https://api.mercadolibre.com/shipment_labels").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 fake")
    )

    pdf = await meli_client.get_label_pdf(account, 44444444444)

    assert pdf == b"%PDF-1.4 fake"
    params = dict(route.calls.last.request.url.params)
    assert params == {"shipment_ids": "44444444444", "response_type": "pdf"}
