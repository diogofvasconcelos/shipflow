"""SQLAlchemy models, one module per table. Schema is specified in
docs/ARCHITECTURE.md §4 — column names and constraints there are binding.

Every model MUST be imported here so Base.metadata sees it (Alembic
autogenerate and the test fixtures both rely on this).
"""

from app.models.event_outbox import EventOutbox
from app.models.meli_account import MeliAccount
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.poll_cursor import PollCursor
from app.models.shipment import Shipment
from app.models.tenant import Tenant
from app.models.user import User
from app.models.variant import Variant
from app.models.variant_barcode import VariantBarcode
from app.models.webhook_event import WebhookEvent

__all__ = [
    "EventOutbox",
    "MeliAccount",
    "Order",
    "OrderItem",
    "PollCursor",
    "Shipment",
    "Tenant",
    "User",
    "Variant",
    "VariantBarcode",
    "WebhookEvent",
]
