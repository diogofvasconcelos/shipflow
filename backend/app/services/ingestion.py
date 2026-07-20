"""The single ingestion pipeline both the webhook worker and the poller (T7) call
(ARCHITECTURE §6.2, §6.3). Fetches the resource from ML and upserts idempotently.

Never commits — the caller owns the transaction so that the order upsert and its
new_order outbox row land atomically (§9 transactional outbox).
"""

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.meli.client import MeliClient
from app.models.meli_account import MeliAccount
from app.models.order import Order
from app.models.shipment import Shipment
from app.repositories.event_outbox import EventOutboxRepository
from app.repositories.order import OrderRepository
from app.repositories.order_item import OrderItemRepository
from app.repositories.shipment import ShipmentRepository
from app.repositories.variant import VariantRepository

logger = logging.getLogger("app.services.ingestion")

SIZE_ATTRIBUTE_KEYS = ("SIZE", "TAMANHO")


def _to_utc(dt: datetime) -> datetime:
    """Normalize to UTC-aware. ML sends offset datetimes; SQLite returns naive on
    read (assumed already-UTC because we store UTC), Postgres returns aware. Storing
    and comparing in UTC keeps the stale-skip correct across both dialects."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _parse_dt(value: str) -> datetime:
    return _to_utc(datetime.fromisoformat(value))


def _money(value) -> Decimal:
    return Decimal(str(value))


def _extract_size(item: dict) -> str | None:
    for key in ("variation_attributes", "attribute_combinations"):
        for attr in item.get(key) or []:
            ident = (attr.get("id") or "").upper()
            name = (attr.get("name") or "").upper()
            if ident in SIZE_ATTRIBUTE_KEYS or name in SIZE_ATTRIBUTE_KEYS:
                value = attr.get("value_name")
                if value:
                    return str(value)
    digits = re.sub(r"\D", "", item.get("seller_custom_field") or "")
    return digits or None


class IngestionService:
    def __init__(self, session: AsyncSession, client: MeliClient) -> None:
        self.session = session
        self.client = client
        self.orders = OrderRepository(session)
        self.order_items = OrderItemRepository(session)
        self.shipments = ShipmentRepository(session)
        self.variants = VariantRepository(session)
        self.outbox = EventOutboxRepository(session)

    async def ingest_order(self, account: MeliAccount, meli_order_id: int) -> tuple[Order, bool]:
        tenant_id = account.tenant_id
        payload = await self.client.get_order(account, meli_order_id)
        fetched_updated = _parse_dt(payload["date_last_updated"])

        existing = await self.orders.get_by_meli_order_id(tenant_id, meli_order_id)
        if existing is not None and fetched_updated <= _to_utc(existing.meli_last_updated_at):
            logger.info("order %s skipped (stale update)", meli_order_id)
            return existing, False

        order, created = await self.orders.upsert(
            tenant_id,
            account.id,
            meli_order_id=payload["id"],
            meli_status=payload["status"],
            total_amount=_money(payload["total_amount"]),
            currency=payload.get("currency_id", "BRL"),
            buyer_nickname=(payload.get("buyer") or {}).get("nickname"),
            pack_id=payload.get("pack_id"),
            meli_created_at=_parse_dt(payload["date_created"]),
            meli_last_updated_at=fetched_updated,
            raw=payload,
        )

        item_payloads = await self._replace_items(tenant_id, order, payload)

        shipping_id = (payload.get("shipping") or {}).get("id")
        if shipping_id:
            shipment = await self.ingest_shipment(account, shipping_id)
            order.shipment_id = shipment.id
            await self.session.flush()

        if created:
            await self.outbox.add_outbox_event(
                tenant_id, "new_order", self._new_order_payload(account, order, item_payloads)
            )

        return order, created

    async def ingest_shipment(self, account: MeliAccount, meli_shipment_id: int) -> Shipment:
        payload = await self.client.get_shipment(account, meli_shipment_id)
        handling_raw = (payload.get("lead_time") or {}).get("estimated_handling_limit") or {}
        handling_at = handling_raw.get("date")
        carrier = payload.get("tracking_method") or (payload.get("carrier_info") or {}).get("name")
        return await self.shipments.upsert(
            account.tenant_id,
            account.id,
            meli_shipment_id=payload["id"],
            meli_status=payload["status"],
            meli_substatus=payload.get("substatus"),
            logistic_type=payload.get("logistic_type"),
            carrier_name=carrier,
            tracking_number=payload.get("tracking_number"),
            handling_limit_at=_parse_dt(handling_at) if handling_at else None,
            raw=payload,
        )

    async def _replace_items(self, tenant_id: int, order: Order, payload: dict) -> list[dict]:
        await self.order_items.delete_by_order(tenant_id, order.id)
        parsed: list[dict] = []
        for line in payload.get("order_items") or []:
            item = line.get("item") or {}
            variation_id = item.get("variation_id") or 0
            size = _extract_size(item)
            seller_sku = item.get("seller_sku") or item.get("seller_custom_field")
            variant = await self.variants.upsert(
                tenant_id,
                meli_item_id=item["id"],
                variation_id=variation_id,
                model_name=item.get("title") or "",
                size=size,
                seller_sku=seller_sku,
            )
            fields = {
                "meli_item_id": item["id"],
                "variation_id": item.get("variation_id"),
                "title": item.get("title") or "",
                "seller_sku": seller_sku,
                "size": size,
                "quantity": line.get("quantity", 1),
                "unit_price": _money(line.get("unit_price", 0)),
                "thumbnail_url": item.get("thumbnail"),
            }
            await self.order_items.create(tenant_id, order.id, variant_id=variant.id, **fields)
            parsed.append(fields)
        return parsed

    @staticmethod
    def _new_order_payload(account: MeliAccount, order: Order, items: list[dict]) -> dict:
        return {
            "order_id": order.id,
            "meli_order_id": order.meli_order_id,
            "pack_id": order.pack_id,
            "meli_account": {
                "id": account.id,
                "meli_user_id": account.meli_user_id,
                "nickname": account.nickname,
            },
            "status": order.meli_status,
            "total_amount": f"{order.total_amount:.2f}",
            "currency": order.currency,
            "buyer_nickname": order.buyer_nickname,
            "items": [
                {
                    "meli_item_id": it["meli_item_id"],
                    "variation_id": it["variation_id"],
                    "title": it["title"],
                    "seller_sku": it["seller_sku"],
                    "size": it["size"],
                    "quantity": it["quantity"],
                    "unit_price": f"{it['unit_price']:.2f}",
                }
                for it in items
            ],
            "meli_created_at": _to_utc(order.meli_created_at).isoformat(),
        }
