from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class MeliAccount(Base):
    """See docs/ARCHITECTURE.md §4.1. Tokens are Fernet-encrypted at rest — never
    decrypted outside app/core/crypto.py (see docs/ARCHITECTURE.md §11).
    """

    __tablename__ = "meli_accounts"
    __table_args__ = (
        UniqueConstraint("meli_user_id", name="uq_meli_accounts_meli_user_id"),
        CheckConstraint(
            "status IN ('active', 'reauth_required', 'disabled')",
            name="ck_meli_accounts_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    meli_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nickname: Mapped[str] = mapped_column(String(255), nullable=False)
    site_id: Mapped[str] = mapped_column(String(10), nullable=False, server_default="MLB")
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    # NULL until the first refresh_token grant succeeds — token exchange at account
    # creation uses the authorization code grant, not a refresh, so it doesn't count.
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
