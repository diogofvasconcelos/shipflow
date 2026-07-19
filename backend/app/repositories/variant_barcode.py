from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.variant_barcode import VariantBarcode


class VariantBarcodeRepository:
    """tenant_id is the first argument of every method here (CLAUDE.md rule)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_barcode(self, tenant_id: int, barcode: str) -> VariantBarcode | None:
        result = await self.session.execute(
            select(VariantBarcode).where(
                VariantBarcode.tenant_id == tenant_id, VariantBarcode.barcode == barcode
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, tenant_id: int, *, variant_id: int, barcode: str, source: str
    ) -> VariantBarcode:
        row = VariantBarcode(
            tenant_id=tenant_id, variant_id=variant_id, barcode=barcode, source=source
        )
        self.session.add(row)
        await self.session.flush()
        return row
