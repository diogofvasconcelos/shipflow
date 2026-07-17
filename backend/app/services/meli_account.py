from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.meli_account import MeliAccount
from app.repositories.meli_account import MeliAccountRepository


class MeliAccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = MeliAccountRepository(session)

    async def list_accounts(self, tenant_id: int) -> list[MeliAccount]:
        return await self.repo.list_all(tenant_id)

    async def disable_account(self, tenant_id: int, account_id: int) -> MeliAccount:
        account = await self.repo.disable(tenant_id, account_id)
        if account is None:
            raise NotFoundError("Conta ML não encontrada")
        await self.session.commit()
        return account
