import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.services.tenant import TenantService


async def test_create_and_get_tenant(db_session: AsyncSession):
    service = TenantService(db_session)
    tenant = await service.create(name="Loja Exemplo", slug="loja-exemplo")

    fetched = await service.get(tenant.id)
    assert fetched.slug == "loja-exemplo"
    assert fetched.name == "Loja Exemplo"


async def test_duplicate_slug_is_rejected(db_session: AsyncSession):
    service = TenantService(db_session)
    await service.create(name="Loja A", slug="loja")

    with pytest.raises(ConflictError):
        await service.create(name="Loja B", slug="loja")


async def test_get_missing_tenant_raises_not_found(db_session: AsyncSession):
    service = TenantService(db_session)
    with pytest.raises(NotFoundError):
        await service.get(999)


async def test_create_tenant_via_api(client: AsyncClient):
    response = await client.post(
        "/api/tenants", json={"name": "Loja Exemplo", "slug": "loja-exemplo"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "loja-exemplo"

    get_response = await client.get(f"/api/tenants/{body['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Loja Exemplo"


async def test_duplicate_slug_via_api_returns_409(client: AsyncClient):
    await client.post("/api/tenants", json={"name": "Loja A", "slug": "loja"})
    response = await client.post("/api/tenants", json={"name": "Loja B", "slug": "loja"})
    assert response.status_code == 409
    assert response.json()["code"] == "slug_taken"


async def test_get_unknown_tenant_via_api_returns_404(client: AsyncClient):
    response = await client.get("/api/tenants/999")
    assert response.status_code == 404


async def test_healthz(client: AsyncClient):
    response = await client.get("/healthz")
    assert response.status_code in (200, 503)
    assert "status" in response.json()
