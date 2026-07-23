"""create poll_cursors

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-19 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "poll_cursors",
        sa.Column(
            "meli_account_id", sa.Integer(), sa.ForeignKey("meli_accounts.id"), primary_key=True
        ),
        sa.Column("orders_last_polled_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("poll_cursors")
