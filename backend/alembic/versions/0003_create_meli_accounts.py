"""create meli_accounts

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-16 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meli_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("meli_user_id", sa.BigInteger(), nullable=False),
        sa.Column("nickname", sa.String(length=255), nullable=False),
        sa.Column("site_id", sa.String(length=10), nullable=False, server_default="MLB"),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("meli_user_id", name="uq_meli_accounts_meli_user_id"),
        sa.CheckConstraint(
            "status IN ('active', 'reauth_required', 'disabled')",
            name="ck_meli_accounts_status",
        ),
    )
    op.create_index("ix_meli_accounts_tenant_id", "meli_accounts", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_meli_accounts_tenant_id", table_name="meli_accounts")
    op.drop_table("meli_accounts")
