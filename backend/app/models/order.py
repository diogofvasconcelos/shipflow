from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.db_types import JSONVariant


class Order(Base):
    """See docs/ARCHITECTURE.md §4.2. Exists for listing/metrics/event payloads —
    batch/picking/check logic hangs off shipments, not orders. meli_status is ML's
    raw vocabulary (no CHECK — see shipment.py for the same reasoning).
    """

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "meli_order_id", name="uq_orders_tenant_meli_order"),
        Index("ix_orders_tenant_status", "tenant_id", "meli_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    meli_account_id: Mapped[int] = mapped_column(ForeignKey("meli_accounts.id"), nullable=False)
    meli_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pack_id: Mapped[int | None] = mapped_column(BigInteger)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"))
    meli_status: Mapped[str] = mapped_column(String(30), nullable=False)
    buyer_nickname: Mapped[str | None] = mapped_column(String(100))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="BRL")
    meli_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meli_last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
