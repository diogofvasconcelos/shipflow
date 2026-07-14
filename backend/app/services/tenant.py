from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.tenant import Tenant
from app.repositories.tenant import TenantRepository


class TenantService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TenantRepository(session)

    async def create(self, *, name: str, slug: str) -> Tenant:
        if await self.repo.get_by_slug(slug) is not None:
            raise ConflictError(f"Slug '{slug}' já está em uso", code="slug_taken")
        tenant = await self.repo.create(name=name, slug=slug)
        await self.session.commit()
        return tenant

    async def get(self, tenant_id: int) -> Tenant:
        tenant = await self.repo.get(tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant não encontrado")
        return tenant

    async def list_all(self) -> list[Tenant]:
        return await self.repo.list_all()
