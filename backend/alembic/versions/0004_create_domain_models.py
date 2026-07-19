"""create domain models: variants, shipments, orders, order_items, webhook_events, event_outbox

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("meli_item_id", sa.String(length=50), nullable=False),
        sa.Column("variation_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("size", sa.String(length=20), nullable=True),
        sa.Column("seller_sku", sa.String(length=100), nullable=True),
        sa.Column("internal_code", sa.String(length=20), nullable=True, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "tenant_id", "meli_item_id", "variation_id", name="uq_variants_tenant_item_variation"
        ),
    )

    op.create_table(
        "variant_barcodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("variants.id"), nullable=False),
        sa.Column("barcode", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "barcode", name="uq_variant_barcodes_tenant_barcode"),
        sa.CheckConstraint(
            "source IN ('ean', 'internal', 'manual')", name="ck_variant_barcodes_source"
        ),
    )

    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "meli_account_id", sa.Integer(), sa.ForeignKey("meli_accounts.id"), nullable=False
        ),
        sa.Column("meli_shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("meli_status", sa.String(length=30), nullable=False),
        sa.Column("meli_substatus", sa.String(length=30), nullable=True),
        sa.Column("logistic_type", sa.String(length=30), nullable=True),
        sa.Column("carrier_name", sa.String(length=100), nullable=True),
        sa.Column("tracking_number", sa.String(length=100), nullable=True),
        sa.Column("handling_limit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "tenant_id", "meli_shipment_id", name="uq_shipments_tenant_meli_shipment"
        ),
    )
    op.create_index(
        "ix_shipments_tenant_status_substatus",
        "shipments",
        ["tenant_id", "meli_status", "meli_substatus"],
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "meli_account_id", sa.Integer(), sa.ForeignKey("meli_accounts.id"), nullable=False
        ),
        sa.Column("meli_order_id", sa.BigInteger(), nullable=False),
        sa.Column("pack_id", sa.BigInteger(), nullable=True),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id"), nullable=True),
        sa.Column("meli_status", sa.String(length=30), nullable=False),
        sa.Column("buyer_nickname", sa.String(length=100), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="BRL"),
        sa.Column("meli_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meli_last_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "meli_order_id", name="uq_orders_tenant_meli_order"),
    )
    op.create_index("ix_orders_tenant_status", "orders", ["tenant_id", "meli_status"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("meli_item_id", sa.String(length=50), nullable=False),
        sa.Column("variation_id", sa.BigInteger(), nullable=True),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("variants.id"), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=True),
        sa.Column("size", sa.String(length=20), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=500), nullable=True),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="meli"),
        sa.Column("topic", sa.String(length=50), nullable=False),
        sa.Column("resource", sa.String(length=255), nullable=False),
        sa.Column("meli_user_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="received"),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('received', 'processed', 'skipped', 'failed')",
            name="ck_webhook_events_status",
        ),
    )
    op.create_index("ix_webhook_events_topic_resource", "webhook_events", ["topic", "resource"])
    # Partial index: only "received" rows are candidates for T5's processing queue.
    op.create_index(
        "ix_webhook_events_received_pending",
        "webhook_events",
        ["status"],
        postgresql_where=sa.text("status = 'received'"),
    )

    op.create_table(
        "event_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed', 'dead')", name="ck_event_outbox_status"
        ),
    )
    op.create_index(
        "ix_event_outbox_status_next_attempt", "event_outbox", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_event_outbox_status_next_attempt", table_name="event_outbox")
    op.drop_table("event_outbox")

    op.drop_index("ix_webhook_events_received_pending", table_name="webhook_events")
    op.drop_index("ix_webhook_events_topic_resource", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")

    op.drop_index("ix_orders_tenant_status", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_shipments_tenant_status_substatus", table_name="shipments")
    op.drop_table("shipments")

    op.drop_table("variant_barcodes")
    op.drop_table("variants")
