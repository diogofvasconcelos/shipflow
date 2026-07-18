from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_token, encrypt_token
from app.models.meli_account import MeliAccount


class MeliAccountRepository:
    """tenant_id is the first argument of every method here (CLAUDE.md rule).

    The module-level get_account_by_meli_user_id below is the sanctioned
    exception already declared in docs/ARCHITECTURE.md §5 (rules 1 and 3):
    meli_user_id is globally unique, and resolving the tenant FROM it — rather
    than filtering BY an already-known tenant_id — is exactly how webhook and
    OAuth-callback routing work.

    Tokens never leave this module in plaintext: create/set_tokens encrypt on
    the way in, get_decrypted_tokens decrypts on the way out. Callers never
    import app.core.crypto directly.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: int, account_id: int) -> MeliAccount | None:
        result = await self.session.execute(
            select(MeliAccount).where(
                MeliAccount.tenant_id == tenant_id, MeliAccount.id == account_id
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self, tenant_id: int) -> list[MeliAccount]:
        result = await self.session.execute(
            select(MeliAccount)
            .where(MeliAccount.tenant_id == tenant_id)
            .order_by(MeliAccount.created_at)
        )
        return list(result.scalars().all())

    async def create(
        self,
        tenant_id: int,
        *,
        meli_user_id: int,
        nickname: str,
        site_id: str = "MLB",
        access_token: str,
        refresh_token: str,
        access_token_expires_at: datetime,
    ) -> MeliAccount:
        account = MeliAccount(
            tenant_id=tenant_id,
            meli_user_id=meli_user_id,
            nickname=nickname,
            site_id=site_id,
            status="active",
            access_token_enc=encrypt_token(access_token),
            refresh_token_enc=encrypt_token(refresh_token),
            access_token_expires_at=access_token_expires_at,
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def set_tokens(
        self,
        tenant_id: int,
        account_id: int,
        *,
        access_token: str,
        refresh_token: str,
        access_token_expires_at: datetime,
    ) -> MeliAccount | None:
        account = await self.get(tenant_id, account_id)
        if account is None:
            return None
        account.access_token_enc = encrypt_token(access_token)
        account.refresh_token_enc = encrypt_token(refresh_token)
        account.access_token_expires_at = access_token_expires_at
        account.last_refresh_at = datetime.now(UTC)
        await self.session.flush()
        return account

    async def get_decrypted_tokens(self, tenant_id: int, account_id: int) -> tuple[str, str] | None:
        account = await self.get(tenant_id, account_id)
        if account is None:
            return None
        return decrypt_token(account.access_token_enc), decrypt_token(account.refresh_token_enc)

    async def set_status(self, tenant_id: int, account_id: int, status: str) -> MeliAccount | None:
        account = await self.get(tenant_id, account_id)
        if account is None:
            return None
        account.status = status
        await self.session.flush()
        return account

    async def disable(self, tenant_id: int, account_id: int) -> MeliAccount | None:
        account = await self.get(tenant_id, account_id)
        if account is None:
            return None
        account.status = "disabled"
        await self.session.flush()
        return account


async def get_account_by_meli_user_id(
    session: AsyncSession, meli_user_id: int
) -> MeliAccount | None:
    """Sanctioned cross-tenant lookup (ARCHITECTURE §5): webhook/OAuth routing
    resolves the tenant FROM this globally-unique column, so it can't take a
    tenant_id up front — mirrors app/repositories/user.py's login exceptions.
    """
    result = await session.execute(
        select(MeliAccount).where(MeliAccount.meli_user_id == meli_user_id)
    )
    return result.scalar_one_or_none()


async def list_accounts_expiring_before(
    session: AsyncSession, cutoff: datetime
) -> list[MeliAccount]:
    """Cross-tenant by the nature of background jobs (ARCHITECTURE §5, last
    paragraph): the refresh_stale_tokens cron sweeps every active account and
    re-derives each tenant from the row itself.
    """
    result = await session.execute(
        select(MeliAccount).where(
            MeliAccount.status == "active",
            MeliAccount.access_token_expires_at < cutoff,
        )
    )
    return list(result.scalars().all())
