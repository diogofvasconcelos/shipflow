import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.db_types import JSONVariant


class EventOutbox(Base):
    """Transactional outbox for EventHub (docs/ARCHITECTURE.md §4.6, §9). Delivery
    (T16) is post-v1 — rows are written now and accumulate as backfill history.

    event_id: the doc lists `DEFAULT gen_random_uuid()` (Postgres-only function).
    Models must stay SQLite-compatible for tests (CLAUDE.md), so this uses the
    dialect-portable Uuid type with a Python-side default instead — every insert
    through the ORM gets a fresh UUID regardless of dialect, with no dependency on
    a Postgres extension/version providing gen_random_uuid().
    """

    __tablename__ = "event_outbox"
    __table_args__ = (
        Index("ix_event_outbox_status_next_attempt", "status", "next_attempt_at"),
        CheckConstraint(
            "status IN ('pending', 'delivered', 'failed', 'dead')", name="ck_event_outbox_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, server_default="1")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(String(500))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
