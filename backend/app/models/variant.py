from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Variant(Base):
    """See docs/ARCHITECTURE.md §4.3. One (item, variation) pair — the physical
    thing on a shelf. Upserted during order ingestion (T6), no listing-sync job.
    """

    __tablename__ = "variants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "meli_item_id", "variation_id", name="uq_variants_tenant_item_variation"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    meli_item_id: Mapped[str] = mapped_column(String(50), nullable=False)
    # 0 = listing without variation (footwear items are always variations in practice,
    # but the column stays NOT NULL DEFAULT 0 per §4.3 to keep the unique key total).
    variation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[str | None] = mapped_column(String(20))
    seller_sku: Mapped[str | None] = mapped_column(String(100))
    # "SFV" + zero-padded id, set right after insert (see VariantRepository.create) —
    # globally unique (not per-tenant): it's derived from the global PK sequence.
    internal_code: Mapped[str | None] = mapped_column(String(20), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
