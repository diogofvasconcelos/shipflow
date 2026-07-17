from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.schemas.meli_account import MeliAccountRead
from app.services.meli_account import MeliAccountService

router = APIRouter(tags=["accounts"])
templates = Jinja2Templates(directory=get_settings().templates_dir)


@router.get("/accounts")
async def accounts_page(request: Request, user: CurrentUser, session: DbSession) -> Response:
    accounts = await MeliAccountService(session).list_accounts(user.tenant_id)
    return templates.TemplateResponse(request, "accounts.html", {"accounts": accounts})


@router.get("/api/accounts")
async def list_accounts(user: CurrentUser, session: DbSession) -> dict:
    accounts = await MeliAccountService(session).list_accounts(user.tenant_id)
    return {"items": [MeliAccountRead.model_validate(a) for a in accounts]}


@router.delete("/api/accounts/{account_id}", response_model=MeliAccountRead)
async def disable_account(
    account_id: int, user: CurrentUser, session: DbSession
) -> MeliAccountRead:
    account = await MeliAccountService(session).disable_account(user.tenant_id, account_id)
    return MeliAccountRead.model_validate(account)
