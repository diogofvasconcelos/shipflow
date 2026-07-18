import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every model on Base.metadata
from app.core.config import get_settings
from app.core.db import Base, get_db
from app.main import create_app


@pytest.fixture(autouse=True)
def _valid_token_encryption_key():
    """The default TOKEN_ENCRYPTION_KEY ("changeme") isn't a valid Fernet key.
    get_settings() is an lru_cache singleton, so mutating the one instance
    here fixes every module that calls get_settings() (crypto.py included)
    for the duration of the test.
    """
    settings = get_settings()
    original = settings.token_encryption_key
    settings.token_encryption_key = Fernet.generate_key().decode()
    yield
    settings.token_encryption_key = original


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
def db_session_factory(db_engine):
    """Session factory bound to the test engine — inject into components that
    open their own sessions (e.g. MeliClient token refresh)."""
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def db_session(db_session_factory):
    async with db_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
