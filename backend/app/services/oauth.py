from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.integrations.meli.client import MeliClient
from app.models.meli_account import MeliAccount
from app.repositories.meli_account import MeliAccountRepository, get_account_by_meli_user_id


class OAuthService:
    def __init__(self, session: AsyncSession, client: MeliClient) -> None:
        self.session = session
        self.client = client
        self.repo = MeliAccountRepository(session)

    async def complete_connection(self, tenant_id: int, code: str) -> MeliAccount:
        """Exchange the authorization code and upsert the account (ARCHITECTURE §6.1).

        meli_user_id is globally unique: reconnecting refreshes the tokens of
        the existing row; an account already owned by ANOTHER tenant is a 409.
        """
        payload = await self.client.exchange_code(code)
        me = await self.client.get_me(payload["access_token"])
        expires_at = datetime.now(UTC) + timedelta(seconds=payload["expires_in"])

        existing = await get_account_by_meli_user_id(self.session, payload["user_id"])
        if existing is not None and existing.tenant_id != tenant_id:
            raise ConflictError(
                "Conta ML já conectada em outra empresa", code="account_other_tenant"
            )

        if existing is not None:
            account = await self.repo.set_tokens(
                tenant_id,
                existing.id,
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                access_token_expires_at=expires_at,
            )
            account.nickname = me["nickname"]
            account.status = "active"
            await self.session.flush()
        else:
            account = await self.repo.create(
                tenant_id,
                meli_user_id=payload["user_id"],
                nickname=me["nickname"],
                site_id=me.get("site_id", "MLB"),
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                access_token_expires_at=expires_at,
            )

        await self.session.commit()
        return account
