import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.tenant import TenantRepository
from app.repositories.variant import VariantRepository
from app.repositories.variant_barcode import VariantBarcodeRepository


async def _create_tenant(db_session: AsyncSession, slug: str):
    tenant = await TenantRepository(db_session).create(name=slug, slug=slug)
    await db_session.commit()
    return tenant


# --- internal_code generation ----------------------------------------------------


async def test_internal_code_is_sfv_plus_zero_padded_id(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-var-a")

    variant = await VariantRepository(db_session).create(
        tenant.id, meli_item_id="MLB1", model_name="Tênis Runner Preto", size="41"
    )
    await db_session.commit()

    assert variant.internal_code == f"SFV{variant.id:06d}"
    assert variant.internal_code.startswith("SFV")
    assert len(variant.internal_code) == 9


async def test_internal_code_is_unique_globally_across_tenants(db_session: AsyncSession):
    from app.models.variant import Variant

    tenant_a = await _create_tenant(db_session, "loja-var-b")
    tenant_b = await _create_tenant(db_session, "loja-var-c")

    variant_a = await VariantRepository(db_session).create(
        tenant_a.id, meli_item_id="MLB1", model_name="Modelo A"
    )
    await db_session.commit()

    # Directly forcing a duplicate internal_code (bypassing the repository) must
    # still be rejected by the DB constraint — it's a UNIQUE column, not scoped.
    colliding = Variant(
        tenant_id=tenant_b.id,
        meli_item_id="MLB2",
        variation_id=0,
        model_name="Modelo B",
        internal_code=variant_a.internal_code,
    )
    db_session.add(colliding)
    with pytest.raises(IntegrityError):
        await db_session.commit()


# --- constraints -------------------------------------------------------------------


async def test_duplicate_item_variation_per_tenant_is_rejected(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-var-d")
    repo = VariantRepository(db_session)
    await repo.create(tenant.id, meli_item_id="MLB9", variation_id=42, model_name="X")
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await repo.create(tenant.id, meli_item_id="MLB9", variation_id=42, model_name="Y")
        await db_session.commit()


async def test_same_item_variation_allowed_across_different_tenants(db_session: AsyncSession):
    tenant_a = await _create_tenant(db_session, "loja-var-e")
    tenant_b = await _create_tenant(db_session, "loja-var-f")
    repo = VariantRepository(db_session)

    await repo.create(tenant_a.id, meli_item_id="MLB7", variation_id=1, model_name="A")
    await repo.create(tenant_b.id, meli_item_id="MLB7", variation_id=1, model_name="B")
    await db_session.commit()  # no IntegrityError: unique key includes tenant_id


async def test_variation_id_defaults_to_zero(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-var-g")
    variant = await VariantRepository(db_session).create(
        tenant.id, meli_item_id="MLB-no-variation", model_name="Sem variação"
    )
    await db_session.commit()

    assert variant.variation_id == 0


# --- tenant isolation ----------------------------------------------------------------


async def test_variant_of_tenant_a_is_invisible_via_tenant_b_repository_call(
    db_session: AsyncSession,
):
    tenant_a = await _create_tenant(db_session, "loja-var-h")
    tenant_b = await _create_tenant(db_session, "loja-var-i")
    repo = VariantRepository(db_session)
    variant = await repo.create(tenant_a.id, meli_item_id="MLB5", model_name="Iso")
    await db_session.commit()

    assert await repo.get(tenant_b.id, variant.id) is None
    assert await repo.get(tenant_a.id, variant.id) is not None
    assert await repo.get_by_meli_item_variation(tenant_b.id, "MLB5", 0) is None
    assert await repo.get_by_meli_item_variation(tenant_a.id, "MLB5", 0) is not None


# --- variant_barcodes ----------------------------------------------------------------


async def test_duplicate_barcode_per_tenant_is_rejected(db_session: AsyncSession):
    tenant = await _create_tenant(db_session, "loja-bc-a")
    variant = await VariantRepository(db_session).create(
        tenant.id, meli_item_id="MLB6", model_name="Com código"
    )
    await db_session.commit()

    repo = VariantBarcodeRepository(db_session)
    await repo.create(tenant.id, variant_id=variant.id, barcode="7891234567890", source="ean")
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await repo.create(
            tenant.id, variant_id=variant.id, barcode="7891234567890", source="manual"
        )
        await db_session.commit()


async def test_same_barcode_allowed_across_different_tenants(db_session: AsyncSession):
    tenant_a = await _create_tenant(db_session, "loja-bc-b")
    tenant_b = await _create_tenant(db_session, "loja-bc-c")
    variant_a = await VariantRepository(db_session).create(
        tenant_a.id, meli_item_id="MLB8", model_name="A"
    )
    variant_b = await VariantRepository(db_session).create(
        tenant_b.id, meli_item_id="MLB8", model_name="B"
    )
    await db_session.commit()

    repo = VariantBarcodeRepository(db_session)
    await repo.create(tenant_a.id, variant_id=variant_a.id, barcode="123", source="ean")
    await repo.create(tenant_b.id, variant_id=variant_b.id, barcode="123", source="ean")
    await db_session.commit()  # no IntegrityError: unique key includes tenant_id


async def test_variant_barcode_is_isolated_by_tenant(db_session: AsyncSession):
    tenant_a = await _create_tenant(db_session, "loja-bc-d")
    tenant_b = await _create_tenant(db_session, "loja-bc-e")
    variant = await VariantRepository(db_session).create(
        tenant_a.id, meli_item_id="MLB10", model_name="Iso barcode"
    )
    await db_session.commit()

    repo = VariantBarcodeRepository(db_session)
    await repo.create(tenant_a.id, variant_id=variant.id, barcode="999", source="internal")
    await db_session.commit()

    assert await repo.get_by_barcode(tenant_b.id, "999") is None
    assert await repo.get_by_barcode(tenant_a.id, "999") is not None
