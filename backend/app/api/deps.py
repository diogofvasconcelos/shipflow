from collections.abc import AsyncIterator
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.integrations.meli.client import MeliClient
from app.models.user import User
from app.repositories.user import get_user_by_id

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_arq_pool(request: Request) -> ArqRedis:
    """The shared Arq/Redis pool created in the app lifespan (see app.main).
    Overridden in tests with a fake pool.
    """
    return request.app.state.arq_pool


ArqPoolDep = Annotated[ArqRedis, Depends(get_arq_pool)]


async def get_meli_client() -> AsyncIterator[MeliClient]:
    client = MeliClient()
    try:
        yield client
    finally:
        await client.aclose()


MeliClientDep = Annotated[MeliClient, Depends(get_meli_client)]


class AuthenticationRequired(Exception):
    """No user_id in session. is_api decides 401 JSON vs 302 to /login (see app.main)."""

    def __init__(self, path: str) -> None:
        self.is_api = path.startswith("/api")


class AdminRequired(Exception):
    """Authenticated but role != admin — always a 403, on both page and API surfaces."""


async def require_user(request: Request, session: DbSession) -> User:
    user_id = request.session.get("user_id")
    user = await get_user_by_id(session, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise AuthenticationRequired(request.url.path)
    return user


CurrentUser = Annotated[User, Depends(require_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise AdminRequired()
    return user
