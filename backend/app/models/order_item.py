from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OrderItem(Base):
    """See docs/ARCHITECTURE.md §4.2. Deliberately has no created_at/updated_at —
    not listed in the spec: an order item is a snapshot written once at ingestion,
    never edited in place (a re-ingest upserts the parent order's item rows anew).
    """

    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_order_id", "order_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    meli_item_id: Mapped[str] = mapped_column(String(50), nullable=False)
    variation_id: Mapped[int | None] = mapped_column(BigInteger)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    seller_sku: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[str | None] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
