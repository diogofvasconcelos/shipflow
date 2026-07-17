from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.repositories.meli_account import MeliAccountRepository, get_account_by_meli_user_id
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository

EXPIRES = datetime.now(UTC) + timedelta(hours=6)


async def _create_tenant(db_session: AsyncSession, slug: str):
    tenant = await TenantRepository(db_session).create(name=slug, slug=slug)
    await db_session.commit()
    return tenant


async def _create_account(
    db_session: AsyncSession, tenant_id: int, *, meli_user_id: int, nickname: str
):
    account = await MeliAccountRepository(db_session).create(
        tenant_id,
        meli_user_id=meli_user_id,
        nickname=nickname,
        access_token="APP_USR-access-token",
        refresh_token="TG-refresh-token",
        access_token_expires_at=EXPIRES,
    )
    await db_session.commit()
    return account


async def _login(client: AsyncClient, db_session: AsyncSession, tenant_slug: str, email: str):
    tenant = await TenantRepository(db_session).create(name=tenant_slug, slug=tenant_slug)
    await UserRepository(db_session).create(
        tenant.id, email=email, password_hash=hash_password("s3nha-forte"), role="admin"
    )
    await db_session.commit()
    await client.post("/login", data={"email": email, "password": "s3nha-forte"})
    return tenant


# --- encryption round-trip -------------------------------------------------------


async def test_tokens_are_encrypted_at_rest_and_decrypt_correctly(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-crypto")
    account = await _create_account(
        db_session, tenant.id, meli_user_id=111111111, nickname="LOJA_CRYPTO"
    )

    assert account.access_token_enc != "APP_USR-access-token"
    assert account.refresh_token_enc != "TG-refresh-token"

    repo = MeliAccountRepository(db_session)
    tokens = await repo.get_decrypted_tokens(tenant.id, account.id)

    assert tokens == ("APP_USR-access-token", "TG-refresh-token")


async def test_set_tokens_rotates_and_stamps_last_refresh_at(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-refresh")
    account = await _create_account(
        db_session, tenant.id, meli_user_id=222222222, nickname="LOJA_REFRESH"
    )
    assert account.last_refresh_at is None

    repo = MeliAccountRepository(db_session)
    updated = await repo.set_tokens(
        tenant.id,
        account.id,
        access_token="APP_USR-new-token",
        refresh_token="TG-new-refresh",
        access_token_expires_at=EXPIRES,
    )
    await db_session.commit()

    assert updated is not None
    assert updated.last_refresh_at is not None
    tokens = await repo.get_decrypted_tokens(tenant.id, account.id)
    assert tokens == ("APP_USR-new-token", "TG-new-refresh")


# --- constraints -------------------------------------------------------------------


async def test_meli_user_id_is_globally_unique_across_tenants(db_session: AsyncSession):
    tenant_a = await _create_tenant(db_session, "loja-unique-a")
    tenant_b = await _create_tenant(db_session, "loja-unique-b")
    await _create_account(db_session, tenant_a.id, meli_user_id=333333333, nickname="A")

    with pytest.raises(IntegrityError):
        await MeliAccountRepository(db_session).create(
            tenant_b.id,
            meli_user_id=333333333,
            nickname="B",
            access_token="x",
            refresh_token="y",
            access_token_expires_at=EXPIRES,
        )
        await db_session.commit()


# --- tenant isolation ----------------------------------------------------------------


async def test_account_of_tenant_a_is_invisible_via_tenant_b_repository_call(
    db_session: AsyncSession,
):
    tenant_a = await _create_tenant(db_session, "loja-iso-a")
    tenant_b = await _create_tenant(db_session, "loja-iso-b")
    account = await _create_account(db_session, tenant_a.id, meli_user_id=444444444, nickname="ISO")

    repo = MeliAccountRepository(db_session)
    assert await repo.get(tenant_b.id, account.id) is None
    assert await repo.get(tenant_a.id, account.id) is not None


async def test_get_account_by_meli_user_id_resolves_tenant_globally(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-webhook")
    account = await _create_account(
        db_session, tenant.id, meli_user_id=555555555, nickname="WEBHOOK"
    )

    found = await get_account_by_meli_user_id(db_session, 555555555)

    assert found is not None
    assert found.id == account.id
    assert found.tenant_id == tenant.id
    assert await get_account_by_meli_user_id(db_session, 999999999) is None


# --- API: tokens never serialized ---------------------------------------------------


async def test_list_accounts_api_never_includes_token_fields(
    client: AsyncClient, db_session: AsyncSession
):
    tenant = await _login(client, db_session, "loja-api", "dono@example.com")
    await _create_account(db_session, tenant.id, meli_user_id=666666666, nickname="LOJA_API")

    response = await client.get("/api/accounts")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["nickname"] == "LOJA_API"
    assert set(item.keys()) == {
        "id",
        "meli_user_id",
        "nickname",
        "site_id",
        "status",
        "access_token_expires_at",
        "last_refresh_at",
        "created_at",
    }
    assert "APP_USR-access-token" not in response.text
    assert "TG-refresh-token" not in response.text


async def test_list_accounts_api_requires_authentication(client: AsyncClient):
    response = await client.get("/api/accounts", follow_redirects=False)
    assert response.status_code == 401


async def test_accounts_page_shows_empty_state(client: AsyncClient, db_session: AsyncSession):
    await _login(client, db_session, "loja-empty", "vazia@example.com")

    response = await client.get("/accounts")

    assert response.status_code == 200
    assert "Nenhuma conta conectada" in response.text


async def test_accounts_page_lists_connected_accounts(
    client: AsyncClient, db_session: AsyncSession
):
    tenant = await _login(client, db_session, "loja-page", "pagina@example.com")
    await _create_account(db_session, tenant.id, meli_user_id=777777777, nickname="LOJA_PAGE")

    response = await client.get("/accounts")

    assert response.status_code == 200
    assert "LOJA_PAGE" in response.text
    assert 'hx-delete="/api/accounts/' in response.text
    assert "Nenhuma conta conectada" not in response.text


# --- soft-disable --------------------------------------------------------------------


async def test_delete_account_soft_disables_instead_of_deleting_row(
    client: AsyncClient, db_session: AsyncSession
):
    tenant = await _login(client, db_session, "loja-disable", "disable@example.com")
    account = await _create_account(
        db_session, tenant.id, meli_user_id=888888888, nickname="LOJA_DISABLE"
    )

    response = await client.delete(f"/api/accounts/{account.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"

    still_there = await MeliAccountRepository(db_session).get(tenant.id, account.id)
    assert still_there is not None
    assert still_there.status == "disabled"


async def test_delete_unknown_account_returns_404(client: AsyncClient, db_session: AsyncSession):
    await _login(client, db_session, "loja-404", "notfound@example.com")

    response = await client.delete("/api/accounts/999999")

    assert response.status_code == 404
