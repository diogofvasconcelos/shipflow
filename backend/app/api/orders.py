"""Orders surface (API.md §4): the JSON list/detail endpoints, the manual sync
trigger (from T7), and the HTMX-driven /orders screen. Routers stay thin — parse
inputs, call OrderService, return the result. All assembly lives in the service.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

from app.api.deps import ArqPoolDep, CurrentUser, DbSession
from app.core.config import get_settings
from app.repositories.meli_account import MeliAccountRepository
from app.services.order import OrderService

router = APIRouter(tags=["orders"])
templates = Jinja2Templates(directory=get_settings().templates_dir)

_DISPLAY_TZ = ZoneInfo(get_settings().display_tz)


def _brdate(dt: datetime | None) -> str:
    """Jinja filter: UTC datetime -> 'dd/mm/aaaa hh:mm' in America/Sao_Paulo.
    Timezone conversion happens only in the presentation layer (CLAUDE.md)."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")


templates.env.filters["brdate"] = _brdate


@router.post("/api/orders/sync", status_code=202)
async def sync_orders(user: CurrentUser, pool: ArqPoolDep) -> dict:
    await pool.enqueue_job("poll_orders")
    return {"detail": "sync enfileirado"}


@router.get("/api/orders")
async def list_orders(
    user: CurrentUser,
    session: DbSession,
    status: str | None = None,
    account_id: int | None = None,
    q: str | None = None,
    page: int = 1,
) -> dict:
    return await OrderService(session).list(
        user.tenant_id, status=status, account_id=account_id, q=q, page=page
    )


@router.get("/api/orders/{order_id}")
async def get_order(user: CurrentUser, session: DbSession, order_id: int) -> dict:
    detail = await OrderService(session).get_detail(user.tenant_id, order_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return detail


@router.get("/orders")
async def orders_page(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    status: str | None = None,
    account_id: int | None = None,
    q: str | None = None,
    page: int = 1,
) -> Response:
    result = await OrderService(session).list(
        user.tenant_id, status=status, account_id=account_id, q=q, page=page
    )
    accounts = await MeliAccountRepository(session).list_all(user.tenant_id)
    context = {
        "result": result,
        "accounts": accounts,
        "filters": {"status": status, "account_id": account_id, "q": q},
    }
    # HTMX filter changes ask for just the table body; a normal navigation gets
    # the whole page. Same URL, one route (API.md lists only GET /orders).
    template = "partials/orders_table.html" if request.headers.get("HX-Request") else "orders.html"
    return templates.TemplateResponse(request, template, context)
