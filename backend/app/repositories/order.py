from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order


class OrderRepository:
    """tenant_id is the first argument of every method here (CLAUDE.md rule)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: int, order_id: int) -> Order | None:
        result = await self.session.execute(
            select(Order).where(Order.tenant_id == tenant_id, Order.id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_meli_order_id(self, tenant_id: int, meli_order_id: int) -> Order | None:
        result = await self.session.execute(
            select(Order).where(Order.tenant_id == tenant_id, Order.meli_order_id == meli_order_id)
        )
        return result.scalar_one_or_none()

    async def upsert_order_from_payload(
        self, tenant_id: int, meli_account_id: int, payload: dict
    ) -> Order:
        """Implemented in T6 — see docs/ORCHESTRATION.md."""
        raise NotImplementedError("Implemented in T6 — see docs/ORCHESTRATION.md")
