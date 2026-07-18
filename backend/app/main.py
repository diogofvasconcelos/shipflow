from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.accounts import router as accounts_router
from app.api.auth import router as auth_router
from app.api.deps import AdminRequired, AuthenticationRequired
from app.api.health import router as health_router
from app.api.oauth import router as oauth_router
from app.api.tenants import router as tenants_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging

settings = get_settings()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    app = FastAPI(title="ShipFlow")

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.is_prod,
    )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code}
        )

    @app.exception_handler(AuthenticationRequired)
    async def authentication_required_handler(
        request: Request, exc: AuthenticationRequired
    ) -> JSONResponse | RedirectResponse:
        if exc.is_api:
            return JSONResponse(
                status_code=401,
                content={"detail": "Autenticação necessária", "code": "unauthenticated"},
            )
        return RedirectResponse(url="/login", status_code=302)

    @app.exception_handler(AdminRequired)
    async def admin_required_handler(request: Request, exc: AdminRequired) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"detail": "Acesso restrito a administradores", "code": "forbidden"},
        )

    app.include_router(health_router)
    app.include_router(tenants_router)
    app.include_router(auth_router)
    app.include_router(accounts_router)
    app.include_router(oauth_router)

    # Vendored CSS/JS only — label PDFs are never served from here (ARCHITECTURE §11).
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    return app


app = create_app()
