import secrets
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.api.deps import DbSession, MeliClientDep, require_admin
from app.core.errors import AppError
from app.core.security import InvalidOAuthState, sign_oauth_state, verify_oauth_state
from app.integrations.meli.client import build_authorization_url
from app.integrations.meli.errors import MeliError
from app.models.user import User
from app.services.oauth import OAuthService

router = APIRouter(prefix="/api/meli/oauth", tags=["oauth"])

AdminUser = Annotated[User, Depends(require_admin)]


@router.get("/start")
async def oauth_start(user: AdminUser) -> RedirectResponse:
    state = sign_oauth_state({"tenant_id": user.tenant_id, "nonce": secrets.token_urlsafe(16)})
    return RedirectResponse(url=build_authorization_url(state), status_code=302)


@router.get("/callback")
async def oauth_callback(
    session: DbSession,
    client: MeliClientDep,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """Unauthenticated by design (ARCHITECTURE §11): the redirect comes from ML.
    Tenant identity comes exclusively from the signed state.
    """
    try:
        data = verify_oauth_state(state or "")
    except InvalidOAuthState:
        return RedirectResponse(url="/accounts?error=estado_invalido", status_code=302)

    if not code:
        return RedirectResponse(url="/accounts?error=autorizacao_negada", status_code=302)

    try:
        await OAuthService(session, client).complete_connection(data["tenant_id"], code)
    except AppError as exc:
        return RedirectResponse(url=f"/accounts?error={exc.code}", status_code=302)
    except MeliError:
        return RedirectResponse(url="/accounts?error=falha_ml", status_code=302)

    return RedirectResponse(url="/accounts?connected=1", status_code=302)
