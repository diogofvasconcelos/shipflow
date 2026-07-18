from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_meli_client
from app.core.db import get_db
from app.core.security import hash_password, sign_oauth_state, verify_oauth_state
from app.integrations.meli.client import MeliClient
from app.main import create_app
from app.repositories.meli_account import MeliAccountRepository
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository
from app.workers.tokens import refresh_stale_tokens
from tests.test_meli_client import REFRESH_PAYLOAD, TOKEN_URL, FakeRedis

ME_URL = "https://api.mercadolibre.com/users/me"


@pytest_asyncio.fixture
async def oauth_client(db_session: AsyncSession, db_session_factory):
    app = create_app()
    meli = MeliClient(session_factory=db_session_factory, redis=FakeRedis())

    async def override_get_db():
        yield db_session

    async def override_get_meli_client():
        yield meli

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_meli_client] = override_get_meli_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await meli.aclose()


async def _login(client: AsyncClient, db_session: AsyncSession, slug: str, *, role: str = "admin"):
    tenant = await TenantRepository(db_session).create(name=slug, slug=slug)
    await UserRepository(db_session).create(
        tenant.id,
        email=f"user@{slug}.com",
        password_hash=hash_password("s3nha-forte"),
        role=role,
    )
    await db_session.commit()
    await client.post("/login", data={"email": f"user@{slug}.com", "password": "s3nha-forte"})
    return tenant


async def _create_account(
    db_session: AsyncSession, tenant_id: int, *, meli_user_id: int, **overrides
):
    defaults = {
        "nickname": "LOJA_EXISTENTE",
        "access_token": "APP_USR-old-access",
        "refresh_token": "TG-old-refresh",
        "access_token_expires_at": datetime.now(UTC) + timedelta(hours=6),
    }
    defaults.update(overrides)
    account = await MeliAccountRepository(db_session).create(
        tenant_id, meli_user_id=meli_user_id, **defaults
    )
    await db_session.commit()
    return account


# --- GET /api/meli/oauth/start ---------------------------------------------------


async def test_oauth_start_redirects_to_ml_with_signed_state(oauth_client, db_session):
    tenant = await _login(oauth_client, db_session, "loja-start")

    response = await oauth_client.get("/api/meli/oauth/start", follow_redirects=False)

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert location.hostname == "auth.mercadolivre.com.br"
    query = parse_qs(location.query)
    assert query["response_type"] == ["code"]
    state = verify_oauth_state(query["state"][0])
    assert state["tenant_id"] == tenant.id
    assert "nonce" in state


async def test_oauth_start_requires_admin(oauth_client, db_session):
    await _login(oauth_client, db_session, "loja-oper", role="operator")

    response = await oauth_client.get("/api/meli/oauth/start", follow_redirects=False)

    assert response.status_code == 403


async def test_oauth_start_requires_authentication(oauth_client):
    response = await oauth_client.get("/api/meli/oauth/start", follow_redirects=False)
    assert response.status_code == 401


# --- GET /api/meli/oauth/callback ------------------------------------------------


@respx.mock
async def test_callback_creates_account_and_redirects_connected(
    oauth_client, db_session, db_session_factory
):
    tenant = await TenantRepository(db_session).create(name="loja-cb", slug="loja-cb")
    await db_session.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=REFRESH_PAYLOAD))
    respx.get(ME_URL).mock(
        return_value=httpx.Response(200, json={"nickname": "LOJA_NOVA", "site_id": "MLB"})
    )
    state = sign_oauth_state({"tenant_id": tenant.id, "nonce": "n1"})

    response = await oauth_client.get(
        f"/api/meli/oauth/callback?code=CODE-1&state={state}", follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/accounts?connected=1"

    async with db_session_factory() as check:
        repo = MeliAccountRepository(check)
        accounts = await repo.list_all(tenant.id)
        assert len(accounts) == 1
        assert accounts[0].nickname == "LOJA_NOVA"
        assert accounts[0].meli_user_id == REFRESH_PAYLOAD["user_id"]
        tokens = await repo.get_decrypted_tokens(tenant.id, accounts[0].id)
        assert tokens == (REFRESH_PAYLOAD["access_token"], REFRESH_PAYLOAD["refresh_token"])


async def test_callback_rejects_tampered_state(oauth_client):
    response = await oauth_client.get(
        "/api/meli/oauth/callback?code=X&state=forjado", follow_redirects=False
    )
    assert response.headers["location"] == "/accounts?error=estado_invalido"


async def test_callback_without_code_redirects_denied(oauth_client, db_session):
    tenant = await TenantRepository(db_session).create(name="loja-neg", slug="loja-neg")
    await db_session.commit()
    state = sign_oauth_state({"tenant_id": tenant.id, "nonce": "n2"})

    response = await oauth_client.get(
        f"/api/meli/oauth/callback?state={state}", follow_redirects=False
    )

    assert response.headers["location"] == "/accounts?error=autorizacao_negada"


@respx.mock
async def test_callback_blocks_account_owned_by_another_tenant(oauth_client, db_session):
    tenant_a = await TenantRepository(db_session).create(name="loja-a2", slug="loja-a2")
    tenant_b = await TenantRepository(db_session).create(name="loja-b2", slug="loja-b2")
    await _create_account(db_session, tenant_b.id, meli_user_id=REFRESH_PAYLOAD["user_id"])
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=REFRESH_PAYLOAD))
    respx.get(ME_URL).mock(return_value=httpx.Response(200, json={"nickname": "X"}))
    state = sign_oauth_state({"tenant_id": tenant_a.id, "nonce": "n3"})

    response = await oauth_client.get(
        f"/api/meli/oauth/callback?code=C&state={state}", follow_redirects=False
    )

    assert response.headers["location"] == "/accounts?error=account_other_tenant"


@respx.mock
async def test_callback_reconnection_rotates_tokens_and_reactivates(
    oauth_client, db_session, db_session_factory
):
    tenant = await TenantRepository(db_session).create(name="loja-rec", slug="loja-rec")
    account = await _create_account(db_session, tenant.id, meli_user_id=REFRESH_PAYLOAD["user_id"])
    await MeliAccountRepository(db_session).set_status(tenant.id, account.id, "reauth_required")
    await db_session.commit()
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=REFRESH_PAYLOAD))
    respx.get(ME_URL).mock(return_value=httpx.Response(200, json={"nickname": "LOJA_REC"}))
    state = sign_oauth_state({"tenant_id": tenant.id, "nonce": "n4"})

    response = await oauth_client.get(
        f"/api/meli/oauth/callback?code=C2&state={state}", follow_redirects=False
    )

    assert response.headers["location"] == "/accounts?connected=1"
    async with db_session_factory() as check:
        repo = MeliAccountRepository(check)
        persisted = await repo.get(tenant.id, account.id)
        assert persisted.status == "active"
        tokens = await repo.get_decrypted_tokens(tenant.id, account.id)
        assert tokens == (REFRESH_PAYLOAD["access_token"], REFRESH_PAYLOAD["refresh_token"])


# --- POST /api/accounts/{id}/refresh ----------------------------------------------


@respx.mock
async def test_force_refresh_endpoint_rotates_and_returns_account_json(oauth_client, db_session):
    tenant = await _login(oauth_client, db_session, "loja-fr")
    account = await _create_account(db_session, tenant.id, meli_user_id=111000111)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=REFRESH_PAYLOAD))

    response = await oauth_client.post(f"/api/accounts/{account.id}/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["last_refresh_at"] is not None
    assert "access_token_enc" not in body
    assert "refresh_token_enc" not in body


@respx.mock
async def test_force_refresh_endpoint_returns_409_on_reauth_required(oauth_client, db_session):
    tenant = await _login(oauth_client, db_session, "loja-fr409")
    account = await _create_account(db_session, tenant.id, meli_user_id=222000222)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))

    response = await oauth_client.post(f"/api/accounts/{account.id}/refresh")

    assert response.status_code == 409
    assert response.json()["code"] == "reauth_required"


@respx.mock
async def test_force_refresh_returns_502_when_ml_errors(oauth_client, db_session):
    tenant = await _login(oauth_client, db_session, "loja-fr502")
    account = await _create_account(db_session, tenant.id, meli_user_id=444000444)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_client"}))

    response = await oauth_client.post(f"/api/accounts/{account.id}/refresh")

    assert response.status_code == 502
    assert response.json()["code"] == "meli_unavailable"


async def test_force_refresh_cross_tenant_returns_404(oauth_client, db_session):
    await _login(oauth_client, db_session, "loja-fr404")
    other_tenant = await TenantRepository(db_session).create(name="loja-oth", slug="loja-oth")
    foreign = await _create_account(db_session, other_tenant.id, meli_user_id=333000333)

    response = await oauth_client.post(f"/api/accounts/{foreign.id}/refresh")

    assert response.status_code == 404


# --- refresh_stale_tokens cron ------------------------------------------------------


@respx.mock
async def test_cron_refreshes_only_stale_active_accounts(
    db_session, db_session_factory, monkeypatch
):
    tenant = await TenantRepository(db_session).create(name="loja-cron", slug="loja-cron")
    stale = await _create_account(
        db_session,
        tenant.id,
        meli_user_id=1001,
        access_token_expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    fresh = await _create_account(
        db_session,
        tenant.id,
        meli_user_id=1002,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    broken = await _create_account(
        db_session,
        tenant.id,
        meli_user_id=1003,
        access_token_expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    await MeliAccountRepository(db_session).set_status(tenant.id, broken.id, "reauth_required")
    await db_session.commit()

    refresh_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=REFRESH_PAYLOAD)
    )
    monkeypatch.setattr("app.workers.tokens.SessionLocal", db_session_factory)
    monkeypatch.setattr(
        "app.workers.tokens.MeliClient",
        lambda: MeliClient(session_factory=db_session_factory, redis=FakeRedis()),
    )

    await refresh_stale_tokens({})

    assert refresh_route.call_count == 1
    async with db_session_factory() as check:
        repo = MeliAccountRepository(check)
        stale_row = await repo.get(tenant.id, stale.id)
        fresh_row = await repo.get(tenant.id, fresh.id)
        broken_row = await repo.get(tenant.id, broken.id)
    assert stale_row.last_refresh_at is not None
    assert fresh_row.last_refresh_at is None
    assert broken_row.status == "reauth_required"
    assert broken_row.last_refresh_at is None
