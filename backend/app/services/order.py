"""Read-side orchestration for the orders screen (T8, API.md §4).

The router stays thin: it calls one of these methods and returns the dict. All
the multi-table assembly (order + account + shipment + items + variant) lives
here, because touching four repositories to build one response is orchestration,
not routing. Money is emitted as a fixed-2dp string (API.md); datetimes are left
as datetime objects — FastAPI serializes them to ISO for the JSON endpoints, and
the Jinja `brdate` filter converts them to America/Sao_Paulo for the screens.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meli_account import MeliAccount
from app.models.order_item import OrderItem
from app.models.shipment import Shipment
from app.models.variant import Variant
from app.repositories.meli_account import MeliAccountRepository
from app.repositories.order import OrderRepository
from app.repositories.order_item import OrderItemRepository
from app.repositories.shipment import ShipmentRepository
from app.repositories.variant import VariantRepository

# A shipment whose seller handling deadline is under this close gets the red badge.
URGENT_WINDOW = timedelta(hours=4)


def _money(value: Decimal) -> str:
    """289.9 -> '289.90'. ML money is always shown with 2 decimals (API.md)."""
    return f"{value:.2f}"


def _to_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; Postgres returns aware. Normalize before
    comparing against now() (the recurring gotcha from T6/T7)."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _is_urgent(handling_limit_at: datetime | None, now: datetime) -> bool:
    if handling_limit_at is None:
        return False
    return _to_utc(handling_limit_at) - now < URGENT_WINDOW


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_detail(self, tenant_id: int, order_id: int) -> dict | None:
        """Full order detail (API.md §4). Returns None when the order does not
        exist OR belongs to another tenant — the router turns that into a 404, so
        a cross-tenant id is indistinguishable from a missing one (no existence
        leak)."""
        order = await OrderRepository(self.session).get(tenant_id, order_id)
        if order is None:
            return None

        account = await MeliAccountRepository(self.session).get(tenant_id, order.meli_account_id)
        shipment = (
            await ShipmentRepository(self.session).get(tenant_id, order.shipment_id)
            if order.shipment_id is not None
            else None
        )
        items = await OrderItemRepository(self.session).list_by_order(tenant_id, order.id)

        variants: dict[int, Variant] = {}
        for item in items:
            if item.variant_id is not None and item.variant_id not in variants:
                variant = await VariantRepository(self.session).get(tenant_id, item.variant_id)
                if variant is not None:
                    variants[item.variant_id] = variant

        return {
            "id": order.id,
            "meli_order_id": order.meli_order_id,
            "pack_id": order.pack_id,
            "account": _account_dict(account),
            "meli_status": order.meli_status,
            "buyer_nickname": order.buyer_nickname,
            "total_amount": _money(order.total_amount),
            "currency": order.currency,
            "meli_created_at": order.meli_created_at,
            "shipment": _shipment_detail_dict(shipment),
            "items": [_item_dict(i, variants.get(i.variant_id)) for i in items],
        }

    async def list(
        self,
        tenant_id: int,
        *,
        status: str | None = None,
        account_id: int | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """One page of the tenant's orders, each row carrying just what the table
        renders (account, buyer, items, status, shipment deadline). Related rows
        are batch-loaded to avoid a per-order query storm."""
        orders, total = await OrderRepository(self.session).list_orders(
            tenant_id,
            status=status,
            account_id=account_id,
            q=q,
            page=page,
            page_size=page_size,
        )

        account_ids = list({o.meli_account_id for o in orders})
        shipment_ids = list({o.shipment_id for o in orders if o.shipment_id is not None})
        order_ids = [o.id for o in orders]

        account_rows = await MeliAccountRepository(self.session).list_by_ids(tenant_id, account_ids)
        accounts = {a.id: a for a in account_rows}
        shipment_rows = await ShipmentRepository(self.session).list_by_ids(tenant_id, shipment_ids)
        shipments = {s.id: s for s in shipment_rows}
        items_by_order: dict[int, list[OrderItem]] = {oid: [] for oid in order_ids}
        for item in await OrderItemRepository(self.session).list_by_orders(tenant_id, order_ids):
            items_by_order[item.order_id].append(item)

        now = datetime.now(UTC)
        rows = [
            {
                "id": o.id,
                "meli_order_id": o.meli_order_id,
                "pack_id": o.pack_id,
                "account": _account_dict(accounts.get(o.meli_account_id)),
                "buyer_nickname": o.buyer_nickname,
                "meli_status": o.meli_status,
                "meli_created_at": o.meli_created_at,
                "items": [
                    {"title": i.title, "size": i.size, "quantity": i.quantity}
                    for i in items_by_order[o.id]
                ],
                "shipment": _shipment_row_dict(shipments.get(o.shipment_id), now),
            }
            for o in orders
        ]

        return {"items": rows, "total": total, "page": page, "page_size": page_size}


def _account_dict(account: MeliAccount | None) -> dict | None:
    if account is None:
        return None
    return {"id": account.id, "nickname": account.nickname}


def _shipment_detail_dict(shipment: Shipment | None) -> dict | None:
    if shipment is None:
        return None
    return {
        "id": shipment.id,
        "meli_shipment_id": shipment.meli_shipment_id,
        "meli_status": shipment.meli_status,
        "meli_substatus": shipment.meli_substatus,
        "logistic_type": shipment.logistic_type,
        "carrier_name": shipment.carrier_name,
        "tracking_number": shipment.tracking_number,
        "handling_limit_at": shipment.handling_limit_at,
    }


def _shipment_row_dict(shipment: Shipment | None, now: datetime) -> dict | None:
    if shipment is None:
        return None
    return {
        "meli_status": shipment.meli_status,
        "meli_substatus": shipment.meli_substatus,
        "handling_limit_at": shipment.handling_limit_at,
        "urgent": _is_urgent(shipment.handling_limit_at, now),
    }


def _item_dict(item: OrderItem, variant: Variant | None) -> dict:
    return {
        "id": item.id,
        "meli_item_id": item.meli_item_id,
        "variation_id": item.variation_id,
        "title": item.title,
        "seller_sku": item.seller_sku,
        "size": item.size,
        "quantity": item.quantity,
        "unit_price": _money(item.unit_price),
        "variant": _variant_dict(variant),
    }


def _variant_dict(variant: Variant | None) -> dict | None:
    if variant is None:
        return None
    return {
        "id": variant.id,
        "model_name": variant.model_name,
        "size": variant.size,
        "internal_code": variant.internal_code,
    }
