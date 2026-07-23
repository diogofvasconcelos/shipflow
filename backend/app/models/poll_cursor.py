from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PollCursor(Base):
    """See docs/ARCHITECTURE.md §4.6, §6.3. No tenant_id and no surrogate id: the
    spec lists only `meli_account_id UNIQUE, orders_last_polled_at` — one cursor
    row per account, so meli_account_id IS the primary key. Absence of a row means
    "never polled" (the poller defaults to a 24h lookback rather than storing a
    sentinel NULL timestamp).
    """

    __tablename__ = "poll_cursors"

    meli_account_id: Mapped[int] = mapped_column(ForeignKey("meli_accounts.id"), primary_key=True)
    orders_last_polled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
