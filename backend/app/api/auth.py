from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import DbSession
from app.core.config import get_settings
from app.services.auth import AuthError, AuthService

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=get_settings().templates_dir)


@router.get("/login")
async def login_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request, session: DbSession, email: str = Form(...), password: str = Form(...)
) -> Response:
    try:
        user = await AuthService(session).authenticate(email, password)
    except AuthError as exc:
        return templates.TemplateResponse(
            request, "login.html", {"error": str(exc)}, status_code=401
        )
    request.session["user_id"] = user.id
    return RedirectResponse(url="/dashboard", status_code=302)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
