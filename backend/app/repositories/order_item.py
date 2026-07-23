from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_item import OrderItem


class OrderItemRepository:
    """tenant_id is the first argument of every method here (CLAUDE.md rule)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_order(self, tenant_id: int, order_id: int) -> list[OrderItem]:
        result = await self.session.execute(
            select(OrderItem).where(
                OrderItem.tenant_id == tenant_id, OrderItem.order_id == order_id
            )
        )
        return list(result.scalars().all())

    async def list_by_orders(self, tenant_id: int, order_ids: list[int]) -> list[OrderItem]:
        """Batch version of list_by_order — one query for a whole page of orders,
        so the list screen doesn't fire N per-order queries."""
        if not order_ids:
            return []
        result = await self.session.execute(
            select(OrderItem).where(
                OrderItem.tenant_id == tenant_id, OrderItem.order_id.in_(order_ids)
            )
        )
        return list(result.scalars().all())

    async def delete_by_order(self, tenant_id: int, order_id: int) -> None:
        """Ingestion replaces an order's items wholesale (delete + recreate). Only
        reached when an order genuinely changed, since repeat notifications hit the
        stale-skip before any item work happens (T6)."""
        await self.session.execute(
            delete(OrderItem).where(
                OrderItem.tenant_id == tenant_id, OrderItem.order_id == order_id
            )
        )

    async def create(
        self,
        tenant_id: int,
        order_id: int,
        *,
        meli_item_id: str,
        title: str,
        quantity: int,
        unit_price: Decimal,
        variation_id: int | None = None,
        variant_id: int | None = None,
        seller_sku: str | None = None,
        size: str | None = None,
        thumbnail_url: str | None = None,
    ) -> OrderItem:
        item = OrderItem(
            tenant_id=tenant_id,
            order_id=order_id,
            meli_item_id=meli_item_id,
            variation_id=variation_id,
            variant_id=variant_id,
            title=title,
            seller_sku=seller_sku,
            size=size,
            quantity=quantity,
            unit_price=unit_price,
            thumbnail_url=thumbnail_url,
        )
        self.session.add(item)
        await self.session.flush()
        return item
