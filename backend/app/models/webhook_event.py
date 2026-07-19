from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.db_types import JSONVariant


class WebhookEvent(Base):
    """See docs/ARCHITECTURE.md §4.6, §6.2. Deliberately has NO tenant_id: intake
    happens before tenant resolution — an unknown meli_user_id is a valid
    status='skipped' outcome, so the row can't require a tenant up front.

    The partial index below needs BOTH postgresql_where and sqlite_where: without
    sqlite_where, SQLite silently builds a full (non-partial) index instead of
    erroring — don't "fix" this by removing one, both are required for parity.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_events_topic_resource", "topic", "resource"),
        Index(
            "ix_webhook_events_received_pending",
            "status",
            postgresql_where=text("status = 'received'"),
            sqlite_where=text("status = 'received'"),
        ),
        CheckConstraint(
            "status IN ('received', 'processed', 'skipped', 'failed')",
            name="ck_webhook_events_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, server_default="meli")
    topic: Mapped[str] = mapped_column(String(50), nullable=False)
    resource: Mapped[str] = mapped_column(String(255), nullable=False)
    meli_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="received")
    error: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
