"""GET /orders and GET /api/orders (list/detail) are T8 (user task, see
docs/ORCHESTRATION.md) — this file starts with only the manual sync trigger T7
needs (API.md §4), and T8 extends it.
"""

from fastapi import APIRouter

from app.api.deps import ArqPoolDep, CurrentUser

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("/sync", status_code=202)
async def sync_orders(user: CurrentUser, pool: ArqPoolDep) -> dict:
    await pool.enqueue_job("poll_orders")
    return {"detail": "sync enfileirado"}
