from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantRepository:
    """Tenant is the root of the multi-tenancy tree, so unlike every other
    repository (see CLAUDE.md), its methods do NOT take tenant_id — there is
    nothing to scope against yet.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: int) -> Tenant | None:
        return await self.session.get(Tenant, tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        result = await self.session.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Tenant]:
        result = await self.session.execute(select(Tenant).order_by(Tenant.name))
        return list(result.scalars().all())

    async def create(self, *, name: str, slug: str) -> Tenant:
        tenant = Tenant(name=name, slug=slug)
        self.session.add(tenant)
        await self.session.flush()
        return tenant
