from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.health import router as health_router
from app.api.tenants import router as tenants_router
from app.core.config import get_settings
from app.core.errors import AppError

settings = get_settings()


def create_app() -> FastAPI:
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

    app.include_router(health_router)
    app.include_router(tenants_router)

    # Vendored CSS/JS only — label PDFs are never served from here (ARCHITECTURE §11).
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    return app


app = create_app()
