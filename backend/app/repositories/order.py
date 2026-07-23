from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, cast, func, or_, select
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

    async def list_orders(
        self,
        tenant_id: int,
        *,
        status: str | None = None,
        account_id: int | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Order], int]:
        """One page of the tenant's orders plus the total match count (for
        pagination). Newest first. `q` matches meli_order_id OR buyer_nickname;
        `status` filters the raw meli_status. tenant_id is always in the WHERE,
        so cross-tenant rows are simply never returned."""
        filters = [Order.tenant_id == tenant_id]
        if status:
            filters.append(Order.meli_status == status)
        if account_id:
            filters.append(Order.meli_account_id == account_id)
        if q:
            like = f"%{q}%"
            filters.append(
                or_(cast(Order.meli_order_id, String).ilike(like), Order.buyer_nickname.ilike(like))
            )

        total = await self.session.scalar(select(func.count()).select_from(Order).where(*filters))
        result = await self.session.execute(
            select(Order)
            .where(*filters)
            .order_by(Order.meli_created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return list(result.scalars().all()), total or 0

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
