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

    async def upsert_shipment_from_payload(
        self, tenant_id: int, meli_account_id: int, payload: dict
    ) -> Shipment:
        """Implemented in T6 — see docs/ORCHESTRATION.md."""
        raise NotImplementedError("Implemented in T6 — see docs/ORCHESTRATION.md")
