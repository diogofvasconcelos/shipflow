from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.variant import Variant


class VariantRepository:
    """tenant_id is the first argument of every method here (CLAUDE.md rule)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: int, variant_id: int) -> Variant | None:
        result = await self.session.execute(
            select(Variant).where(Variant.tenant_id == tenant_id, Variant.id == variant_id)
        )
        return result.scalar_one_or_none()

    async def get_by_meli_item_variation(
        self, tenant_id: int, meli_item_id: str, variation_id: int = 0
    ) -> Variant | None:
        result = await self.session.execute(
            select(Variant).where(
                Variant.tenant_id == tenant_id,
                Variant.meli_item_id == meli_item_id,
                Variant.variation_id == variation_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        tenant_id: int,
        *,
        meli_item_id: str,
        variation_id: int = 0,
        model_name: str,
        size: str | None = None,
        seller_sku: str | None = None,
    ) -> Variant:
        """internal_code needs the PK, so this is insert -> flush -> format ->
        flush again, all in one transaction (no commit — that's the caller's job,
        per docs/ARCHITECTURE.md §4.3: 'SFV' + zero-padded id, e.g. 'SFV000012').
        """
        variant = Variant(
            tenant_id=tenant_id,
            meli_item_id=meli_item_id,
            variation_id=variation_id,
            model_name=model_name,
            size=size,
            seller_sku=seller_sku,
        )
        self.session.add(variant)
        await self.session.flush()
        variant.internal_code = f"SFV{variant.id:06d}"
        await self.session.flush()
        return variant

    async def upsert(
        self,
        tenant_id: int,
        *,
        meli_item_id: str,
        variation_id: int = 0,
        model_name: str,
        size: str | None = None,
        seller_sku: str | None = None,
    ) -> Variant:
        """Find-or-create by (tenant_id, meli_item_id, variation_id). New rows get
        an internal_code via create(); existing rows have their editable fields
        refreshed from the latest order data.
        """
        variant = await self.get_by_meli_item_variation(tenant_id, meli_item_id, variation_id)
        if variant is None:
            return await self.create(
                tenant_id,
                meli_item_id=meli_item_id,
                variation_id=variation_id,
                model_name=model_name,
                size=size,
                seller_sku=seller_sku,
            )
        variant.model_name = model_name
        variant.size = size
        variant.seller_sku = seller_sku
        await self.session.flush()
        return variant
