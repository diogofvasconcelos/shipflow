from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.db_types import JSONVariant


class Shipment(Base):
    """See docs/ARCHITECTURE.md §4.2. The printable/checkable unit — labels are
    issued per shipment, not per order. meli_status/meli_substatus/logistic_type
    are ML's raw vocabulary (no CHECK constraint): mirroring ML's words verbatim
    means new values ML introduces never break an insert (§4.2 "single source of
    truth for state").
    """

    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "meli_shipment_id", name="uq_shipments_tenant_meli_shipment"),
        Index("ix_shipments_tenant_status_substatus", "tenant_id", "meli_status", "meli_substatus"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    meli_account_id: Mapped[int] = mapped_column(ForeignKey("meli_accounts.id"), nullable=False)
    meli_shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    meli_status: Mapped[str] = mapped_column(String(30), nullable=False)
    meli_substatus: Mapped[str | None] = mapped_column(String(30))
    logistic_type: Mapped[str | None] = mapped_column(String(30))
    carrier_name: Mapped[str | None] = mapped_column(String(100))
    tracking_number: Mapped[str | None] = mapped_column(String(100))
    handling_limit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
