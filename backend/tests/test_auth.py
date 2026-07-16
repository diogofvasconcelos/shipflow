import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from app.api.deps import (
    AdminRequired,
    AuthenticationRequired,
    CurrentUser,
    require_admin,
    require_user,
)
from app.core.db import get_db
from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository


async def _create_user(
    db_session: AsyncSession,
    *,
    tenant_slug: str,
    email: str,
    password: str,
    role: str = "admin",
) -> tuple[Tenant, User]:
    tenant = await TenantRepository(db_session).create(name=tenant_slug, slug=tenant_slug)
    user = await UserRepository(db_session).create(
        tenant.id, email=email, password_hash=hash_password(password), role=role
    )
    await db_session.commit()
    return tenant, user


def _request(path: str, session: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "path": path,
        "headers": [],
        "query_string": b"",
        "session": session if session is not None else {},
    }
    return Request(scope)


# --- /login, /logout (HTTP, via the real app) ---------------------------------


async def test_login_success_sets_session_and_redirects(
    client: AsyncClient, db_session: AsyncSession
):
    await _create_user(
        db_session, tenant_slug="loja-a", email="maria@example.com", password="s3nha-forte"
    )

    response = await client.post(
        "/login",
        data={"email": "maria@example.com", "password": "s3nha-forte"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"
    assert "session" in response.cookies


async def test_login_wrong_password_rerenders_with_error(
    client: AsyncClient, db_session: AsyncSession
):
    await _create_user(
        db_session, tenant_slug="loja-b", email="joao@example.com", password="correta"
    )

    response = await client.post(
        "/login",
        data={"email": "joao@example.com", "password": "errada"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "E-mail ou senha inv" in response.text


async def test_login_unknown_email_fails_same_as_wrong_password(client: AsyncClient):
    response = await client.post(
        "/login",
        data={"email": "ninguem@example.com", "password": "x"},
        follow_redirects=False,
    )

    assert response.status_code == 401


async def test_logout_clears_session_and_redirects(client: AsyncClient, db_session: AsyncSession):
    await _create_user(
        db_session, tenant_slug="loja-c", email="ana@example.com", password="segredo123"
    )
    await client.post("/login", data={"email": "ana@example.com", "password": "segredo123"})

    response = await client.post("/logout", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


# --- tenant isolation -----------------------------------------------------------


async def test_user_of_tenant_a_is_invisible_via_tenant_b_repository_call(
    db_session: AsyncSession,
):
    tenant_a, user_a = await _create_user(
        db_session, tenant_slug="loja-d", email="carlos@example.com", password="x"
    )
    tenant_b = await TenantRepository(db_session).create(name="loja-e", slug="loja-e")
    await db_session.commit()

    assert await UserRepository(db_session).get(tenant_b.id, user_a.id) is None
    assert await UserRepository(db_session).get(tenant_a.id, user_a.id) is not None


# --- require_user / require_admin (direct dependency calls) ---------------------


async def test_require_user_raises_redirect_for_page_paths_without_session(
    db_session: AsyncSession,
):
    request = _request("/dashboard")

    with pytest.raises(AuthenticationRequired) as exc_info:
        await require_user(request, db_session)

    assert exc_info.value.is_api is False


async def test_require_user_raises_401_for_api_paths_without_session(db_session: AsyncSession):
    request = _request("/api/orders")

    with pytest.raises(AuthenticationRequired) as exc_info:
        await require_user(request, db_session)

    assert exc_info.value.is_api is True


async def test_require_user_returns_user_for_valid_session(db_session: AsyncSession):
    _, user = await _create_user(
        db_session, tenant_slug="loja-f", email="paula@example.com", password="x"
    )
    request = _request("/dashboard", session={"user_id": user.id})

    result = await require_user(request, db_session)

    assert result.id == user.id


async def test_require_user_rejects_inactive_user(db_session: AsyncSession):
    _, user = await _create_user(
        db_session, tenant_slug="loja-g", email="pedro@example.com", password="x"
    )
    user.is_active = False
    await db_session.commit()
    request = _request("/dashboard", session={"user_id": user.id})

    with pytest.raises(AuthenticationRequired):
        await require_user(request, db_session)


async def test_require_admin_allows_admin(db_session: AsyncSession):
    _, admin = await _create_user(
        db_session, tenant_slug="loja-h", email="admin@example.com", password="x", role="admin"
    )

    assert await require_admin(admin) is admin


async def test_require_admin_blocks_operator(db_session: AsyncSession):
    _, operator = await _create_user(
        db_session, tenant_slug="loja-i", email="op@example.com", password="x", role="operator"
    )

    with pytest.raises(AdminRequired):
        await require_admin(operator)


# --- full HTTP wiring: dependency -> exception -> handler -> response -----------


async def test_protected_route_redirects_over_http_when_unauthenticated(db_session: AsyncSession):
    probe_app = FastAPI()
    probe_app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @probe_app.exception_handler(AuthenticationRequired)
    async def _handler(request: Request, exc: AuthenticationRequired):
        if exc.is_api:
            return JSONResponse(status_code=401, content={"detail": "Autenticação necessária"})
        return RedirectResponse(url="/login", status_code=302)

    @probe_app.get("/protected")
    async def protected(user: CurrentUser) -> dict:
        return {"id": user.id}

    async def override_get_db():
        yield db_session

    probe_app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=probe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/protected", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
