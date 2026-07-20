from datetime import datetime
from decimal import Decimal

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

    async def upsert(
        self,
        tenant_id: int,
        meli_account_id: int,
        *,
        meli_order_id: int,
        meli_status: str,
        total_amount: Decimal,
        meli_created_at: datetime,
        meli_last_updated_at: datetime,
        raw: dict,
        pack_id: int | None = None,
        buyer_nickname: str | None = None,
        currency: str = "BRL",
    ) -> tuple[Order, bool]:
        """Insert or update by (tenant_id, meli_order_id). Returns (order, created).
        `created` drives the once-per-order new_order outbox event (T6/§9).
        shipment_id is linked separately after the shipment is ingested.
        """
        order = await self.get_by_meli_order_id(tenant_id, meli_order_id)
        created = order is None
        if order is None:
            order = Order(
                tenant_id=tenant_id,
                meli_account_id=meli_account_id,
                meli_order_id=meli_order_id,
            )
            self.session.add(order)

        order.meli_status = meli_status
        order.total_amount = total_amount
        order.currency = currency
        order.buyer_nickname = buyer_nickname
        order.pack_id = pack_id
        order.meli_created_at = meli_created_at
        order.meli_last_updated_at = meli_last_updated_at
        order.raw = raw
        await self.session.flush()
        return order, created
