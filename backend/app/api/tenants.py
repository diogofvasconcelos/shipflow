from fastapi import APIRouter

from app.api.deps import DbSession
from app.schemas.tenant import TenantCreate, TenantRead
from app.services.tenant import TenantService

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


@router.get("", response_model=list[TenantRead])
async def list_tenants(session: DbSession) -> list[TenantRead]:
    tenants = await TenantService(session).list_all()
    return [TenantRead.model_validate(t) for t in tenants]


@router.post("", response_model=TenantRead, status_code=201)
async def create_tenant(body: TenantCreate, session: DbSession) -> TenantRead:
    tenant = await TenantService(session).create(name=body.name, slug=body.slug)
    return TenantRead.model_validate(tenant)


@router.get("/{tenant_id}", response_model=TenantRead)
async def get_tenant(tenant_id: int, session: DbSession) -> TenantRead:
    tenant = await TenantService(session).get(tenant_id)
    return TenantRead.model_validate(tenant)
