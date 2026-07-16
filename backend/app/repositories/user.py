from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """tenant_id is the first argument of every method here (CLAUDE.md rule).

    The two module-level functions below are the sanctioned exceptions,
    documented in docs/ARCHITECTURE.md §5: login and session loading happen
    before a tenant_id is known, so tenant is derived from the matched row
    itself — the same pattern webhooks use to resolve tenant from meli_user_id.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: int, user_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.tenant_id == tenant_id, User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, tenant_id: int, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.tenant_id == tenant_id, User.email == email)
        )
        return result.scalar_one_or_none()

    async def create(self, tenant_id: int, *, email: str, password_hash: str, role: str) -> User:
        user = User(tenant_id=tenant_id, email=email, password_hash=password_hash, role=role)
        self.session.add(user)
        await self.session.flush()
        return user


async def find_user_for_login(session: AsyncSession, email: str) -> User | None:
    """Sanctioned cross-tenant lookup #1: login has no tenant context yet."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalars().first()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    """Sanctioned cross-tenant lookup #2: the session cookie is server-signed
    and carries only user_id (never a client-supplied tenant_id); tenant_id is
    re-derived from this row, per the tenancy rule in CLAUDE.md.
    """
    return await session.get(User, user_id)
