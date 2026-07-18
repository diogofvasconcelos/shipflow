from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UpstreamError
from app.integrations.meli.client import MeliClient
from app.integrations.meli.errors import MeliError, MeliReauthRequired
from app.models.meli_account import MeliAccount
from app.repositories.meli_account import MeliAccountRepository


class MeliAccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = MeliAccountRepository(session)

    async def list_accounts(self, tenant_id: int) -> list[MeliAccount]:
        return await self.repo.list_all(tenant_id)

    async def force_refresh(
        self, tenant_id: int, account_id: int, client: MeliClient
    ) -> MeliAccount:
        """Health-check refresh (API.md §2). The client persists the rotated
        tokens in its own session; re-read here to return the fresh row.
        """
        account = await self.repo.get(tenant_id, account_id)
        if account is None:
            raise NotFoundError("Conta ML não encontrada")
        try:
            await client.refresh_token(account, force=True)
        except MeliReauthRequired as exc:
            raise ConflictError("Conta precisa ser reconectada", code="reauth_required") from exc
        except MeliError as exc:
            raise UpstreamError(
                "Falha ao comunicar com o Mercado Livre", code="meli_unavailable"
            ) from exc
        self.session.expire(account)
        return await self.repo.get(tenant_id, account_id)

    async def disable_account(self, tenant_id: int, account_id: int) -> MeliAccount:
        account = await self.repo.disable(tenant_id, account_id)
        if account is None:
            raise NotFoundError("Conta ML não encontrada")
        await self.session.commit()
        return account
