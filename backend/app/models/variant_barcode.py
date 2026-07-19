from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class VariantBarcode(Base):
    """See docs/ARCHITECTURE.md §4.3. N barcodes per variant — a factory EAN and an
    internal label may coexist. No updated_at: rows are added/removed, never edited.
    """

    __tablename__ = "variant_barcodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "barcode", name="uq_variant_barcodes_tenant_barcode"),
        CheckConstraint(
            "source IN ('ean', 'internal', 'manual')", name="ck_variant_barcodes_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
