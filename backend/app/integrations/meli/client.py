"""The ONLY module allowed to talk to the Mercado Libre API (see CLAUDE.md).

Owns auth-header injection, lazy token refresh, the single-flight refresh lock,
the 401/429/5xx retry policy, and per-account concurrency (ARCHITECTURE §6.1).
Token plaintext exists only here and in app/repositories/meli_account.py.

Refresh persistence runs in this module's OWN database session (session_factory):
ML refresh tokens are single-use and rotate, so the new pair MUST be committed
before the Redis lock is released — never inside a caller's open transaction.
"""

import asyncio
import logging
import random
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_token
from app.core.db import SessionLocal
from app.integrations.meli.errors import MeliError, MeliReauthRequired
from app.models.meli_account import MeliAccount
from app.repositories.meli_account import MeliAccountRepository

logger = logging.getLogger("app.integrations.meli")

API_BASE_URL = "https://api.mercadolibre.com"
AUTH_BASE_URL = "https://auth.mercadolivre.com.br/authorization"

REFRESH_MARGIN = timedelta(minutes=5)
LOCK_TTL_SECONDS = 30
LOCK_POLL_SECONDS = 0.1
RATE_LIMIT_ATTEMPTS = 5
SERVER_ERROR_ATTEMPTS = 3

# Patchable in tests so backoff tests don't actually sleep.
_sleep = asyncio.sleep

# Per-account concurrency cap, shared across client instances in this process.
_semaphores: dict[int, asyncio.Semaphore] = {}


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; Postgres timestamptz returns aware ones."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _backoff_delay(attempt: int) -> float:
    return min(2**attempt, 30) + random.uniform(0, 1)


def build_authorization_url(state: str) -> str:
    settings = get_settings()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.meli_client_id,
            "redirect_uri": settings.meli_redirect_uri,
            "state": state,
        }
    )
    return f"{AUTH_BASE_URL}?{query}"


class MeliClient:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] = SessionLocal,
        redis: Redis | None = None,
    ) -> None:
        self.settings = get_settings()
        self._http = httpx.AsyncClient(base_url=API_BASE_URL, timeout=15)
        self._session_factory = session_factory
        self._redis = redis

    # --- OAuth grants ---------------------------------------------------------

    async def exchange_code(self, code: str) -> dict:
        response = await self._http.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self.settings.meli_client_id,
                "client_secret": self.settings.meli_client_secret,
                "code": code,
                "redirect_uri": self.settings.meli_redirect_uri,
            },
        )
        logger.info("POST /oauth/token (authorization_code) -> %s", response.status_code)
        if response.status_code != 200:
            raise MeliError(f"code exchange failed with status {response.status_code}")
        return response.json()

    async def get_me(self, access_token: str) -> dict:
        response = await self._http.get(
            "/users/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        logger.info("GET /users/me -> %s", response.status_code)
        if response.status_code != 200:
            raise MeliError(f"/users/me failed with status {response.status_code}")
        return response.json()

    # --- Token refresh (single-flight) ----------------------------------------

    async def refresh_token(
        self,
        account: MeliAccount,
        *,
        force: bool = False,
        failed_access_token: str | None = None,
    ) -> str:
        """Refresh under the per-account Redis lock and return a valid access token.

        Inside the lock the account row is re-read: if another worker already
        refreshed it, reuse its tokens and skip the HTTP call. When the caller
        just got a 401 (failed_access_token), a "fresh" expiry is not enough —
        the stored token must actually DIFFER from the one that failed,
        otherwise ML revoked it server-side and a real refresh is needed.
        """
        lock_key = f"meli:refresh:{account.id}"
        await self._acquire_lock(lock_key)
        try:
            async with self._session_factory() as session:
                repo = MeliAccountRepository(session)
                fresh = await repo.get(account.tenant_id, account.id)
                if fresh is None:
                    raise MeliError(f"account {account.id} not found during refresh")

                already_fresh = _aware(fresh.access_token_expires_at) > (
                    datetime.now(UTC) + REFRESH_MARGIN
                )
                if already_fresh and not force:
                    current = decrypt_token(fresh.access_token_enc)
                    if failed_access_token is None or current != failed_access_token:
                        self._copy_account_state(fresh, account)
                        return current

                refresh_plain = decrypt_token(fresh.refresh_token_enc)
                response = await self._http.post(
                    "/oauth/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self.settings.meli_client_id,
                        "client_secret": self.settings.meli_client_secret,
                        "refresh_token": refresh_plain,
                    },
                )
                logger.info("POST /oauth/token (refresh_token) -> %s", response.status_code)

                if response.status_code != 200:
                    if self._is_invalid_grant(response):
                        await repo.set_status(account.tenant_id, account.id, "reauth_required")
                        await session.commit()
                        account.status = "reauth_required"
                        raise MeliReauthRequired(f"account {account.id} needs manual reconnection")
                    raise MeliError(f"token refresh failed with status {response.status_code}")

                payload = response.json()
                updated = await repo.set_tokens(
                    account.tenant_id,
                    account.id,
                    access_token=payload["access_token"],
                    refresh_token=payload["refresh_token"],
                    access_token_expires_at=datetime.now(UTC)
                    + timedelta(seconds=payload["expires_in"]),
                )
                await session.commit()
                self._copy_account_state(updated, account)
                return payload["access_token"]
        finally:
            await self._release_lock(lock_key)

    @staticmethod
    def _is_invalid_grant(response: httpx.Response) -> bool:
        try:
            return response.json().get("error") == "invalid_grant"
        except ValueError:
            return False

    @staticmethod
    def _copy_account_state(source: MeliAccount, target: MeliAccount) -> None:
        target.access_token_enc = source.access_token_enc
        target.refresh_token_enc = source.refresh_token_enc
        target.access_token_expires_at = source.access_token_expires_at
        target.last_refresh_at = source.last_refresh_at
        target.status = source.status

    async def _ensure_token(self, account: MeliAccount) -> str:
        if _aware(account.access_token_expires_at) < datetime.now(UTC) + REFRESH_MARGIN:
            return await self.refresh_token(account)
        return decrypt_token(account.access_token_enc)

    # --- Redis lock ------------------------------------------------------------

    def _get_redis(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(self.settings.redis_url)
        return self._redis

    async def _acquire_lock(self, key: str) -> None:
        redis = self._get_redis()
        token = secrets.token_hex(8)
        deadline = LOCK_TTL_SECONDS + 5
        waited = 0.0
        while not await redis.set(key, token, nx=True, ex=LOCK_TTL_SECONDS):
            await _sleep(LOCK_POLL_SECONDS)
            waited += LOCK_POLL_SECONDS
            if waited >= deadline:
                raise MeliError(f"timed out waiting for refresh lock {key}")

    async def _release_lock(self, key: str) -> None:
        await self._get_redis().delete(key)

    # --- Authenticated API calls ------------------------------------------------

    async def _request(
        self, account: MeliAccount, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        semaphore = _semaphores.setdefault(account.id, asyncio.Semaphore(5))
        async with semaphore:
            token = await self._ensure_token(account)
            rate_limit_tries = 0
            server_error_tries = 0
            refreshed_after_401 = False

            while True:
                try:
                    response = await self._http.request(
                        method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
                    )
                except httpx.TransportError as exc:
                    server_error_tries += 1
                    logger.warning(
                        "%s %s -> network error (try %s)", method, path, server_error_tries
                    )
                    if server_error_tries >= SERVER_ERROR_ATTEMPTS:
                        raise MeliError(f"{method} {path} failed: {exc!r}") from exc
                    await _sleep(_backoff_delay(server_error_tries - 1))
                    continue

                logger.info("%s %s -> %s", method, path, response.status_code)

                if response.status_code == 401 and not refreshed_after_401:
                    refreshed_after_401 = True
                    token = await self.refresh_token(account, failed_access_token=token)
                    continue

                if response.status_code == 429:
                    rate_limit_tries += 1
                    if rate_limit_tries >= RATE_LIMIT_ATTEMPTS:
                        raise MeliError(
                            f"{method} {path} rate-limited after {rate_limit_tries} tries"
                        )
                    retry_after = response.headers.get("Retry-After")
                    delay = (
                        float(retry_after) if retry_after else _backoff_delay(rate_limit_tries - 1)
                    )
                    await _sleep(delay)
                    continue

                if response.status_code >= 500:
                    server_error_tries += 1
                    if server_error_tries >= SERVER_ERROR_ATTEMPTS:
                        raise MeliError(
                            f"{method} {path} failed with status {response.status_code}"
                        )
                    await _sleep(_backoff_delay(server_error_tries - 1))
                    continue

                if response.status_code >= 400:
                    raise MeliError(f"{method} {path} failed with status {response.status_code}")

                return response

    # --- Typed API methods -------------------------------------------------------

    async def get_order(self, account: MeliAccount, meli_order_id: int) -> dict:
        response = await self._request(account, "GET", f"/orders/{meli_order_id}")
        return response.json()

    async def get_shipment(self, account: MeliAccount, meli_shipment_id: int) -> dict:
        response = await self._request(account, "GET", f"/shipments/{meli_shipment_id}")
        return response.json()

    async def search_orders(self, account: MeliAccount, from_dt: datetime, offset: int = 0) -> dict:
        response = await self._request(
            account,
            "GET",
            "/orders/search",
            params={
                "seller": account.meli_user_id,
                "order.date_last_updated.from": _aware(from_dt).isoformat(),
                "sort": "date_asc",
                "offset": offset,
            },
        )
        return response.json()

    async def get_label_pdf(self, account: MeliAccount, meli_shipment_id: int) -> bytes:
        response = await self._request(
            account,
            "GET",
            "/shipment_labels",
            params={"shipment_ids": meli_shipment_id, "response_type": "pdf"},
        )
        return response.content

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._redis is not None:
            await self._redis.aclose()
