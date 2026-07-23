from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shipment import Shipment


class ShipmentRepository:
    """tenant_id is the first argument of every method here (CLAUDE.md rule)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: int, shipment_id: int) -> Shipment | None:
        result = await self.session.execute(
            select(Shipment).where(Shipment.tenant_id == tenant_id, Shipment.id == shipment_id)
        )
        return result.scalar_one_or_none()

    async def get_by_meli_shipment_id(
        self, tenant_id: int, meli_shipment_id: int
    ) -> Shipment | None:
        result = await self.session.execute(
            select(Shipment).where(
                Shipment.tenant_id == tenant_id, Shipment.meli_shipment_id == meli_shipment_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        tenant_id: int,
        meli_account_id: int,
        *,
        meli_shipment_id: int,
        meli_status: str,
        raw: dict,
        meli_substatus: str | None = None,
        logistic_type: str | None = None,
        carrier_name: str | None = None,
        tracking_number: str | None = None,
        handling_limit_at: datetime | None = None,
    ) -> Shipment:
        """Insert or update by (tenant_id, meli_shipment_id). Idempotent, so a pack
        of N orders that share one shipment all converge to the same row."""
        shipment = await self.get_by_meli_shipment_id(tenant_id, meli_shipment_id)
        if shipment is None:
            shipment = Shipment(
                tenant_id=tenant_id,
                meli_account_id=meli_account_id,
                meli_shipment_id=meli_shipment_id,
            )
            self.session.add(shipment)

        shipment.meli_status = meli_status
        shipment.meli_substatus = meli_substatus
        shipment.logistic_type = logistic_type
        shipment.carrier_name = carrier_name
        shipment.tracking_number = tracking_number
        shipment.handling_limit_at = handling_limit_at
        shipment.raw = raw
        await self.session.flush()
        return shipment


async def list_ready_to_ship_shipments(session: AsyncSession) -> list[Shipment]:
    """Cross-tenant by the nature of background jobs (ARCHITECTURE §5, last
    paragraph): sync_open_shipments (T7) sweeps every tenant's ready_to_ship
    shipments and re-derives tenant_id from each row.

    Scope note: ARCHITECTURE §6.3 also says "belongs to a non-terminal batch",
    but print_batches/batch_shipments don't exist until T9 — that clause can't
    be implemented yet. ready_to_ship is already the currently-useful bounded
    set (shipments whose LOCAL copy might be stale because we missed a webhook
    transition); extend this query with the batch-membership OR-clause once T9
    lands (tracked in docs/ARCHITECTURE.md §6.3 and docs/ORCHESTRATION.md T9).
    """
    result = await session.execute(select(Shipment).where(Shipment.meli_status == "ready_to_ship"))
    return list(result.scalars().all())
