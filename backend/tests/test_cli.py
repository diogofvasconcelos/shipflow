import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import seed_admin
from app.core.security import verify_password
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository


async def test_seed_admin_creates_tenant_and_admin(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr("app.cli.SessionLocal", lambda: db_session)

    await seed_admin("loja-seed", "dono@example.com", "s3nha-forte")

    tenant = await TenantRepository(db_session).get_by_slug("loja-seed")
    assert tenant is not None

    user = await UserRepository(db_session).get_by_email(tenant.id, "dono@example.com")
    assert user is not None
    assert user.role == "admin"
    assert verify_password("s3nha-forte", user.password_hash)


async def test_seed_admin_reuses_existing_tenant(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr("app.cli.SessionLocal", lambda: db_session)
    await TenantRepository(db_session).create(name="loja-existente", slug="loja-existente")
    await db_session.commit()

    await seed_admin("loja-existente", "admin2@example.com", "outra-senha")

    tenants = await TenantRepository(db_session).list_all()
    assert len([t for t in tenants if t.slug == "loja-existente"]) == 1


async def test_seed_admin_rejects_duplicate_email_in_same_tenant(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr("app.cli.SessionLocal", lambda: db_session)
    await seed_admin("loja-dup", "repetido@example.com", "senha1")

    with pytest.raises(SystemExit):
        await seed_admin("loja-dup", "repetido@example.com", "senha2")
