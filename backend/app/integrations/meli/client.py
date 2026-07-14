"""The ONLY module allowed to talk to the Mercado Libre API (see CLAUDE.md).

Full implementation — OAuth exchange/refresh, single-flight token refresh lock,
401/429/5xx retry policy — is task T3 in docs/ORCHESTRATION.md (see
docs/ARCHITECTURE.md §6.1 for the binding spec). This module exists now so every
other module has a single, stable import target and never reaches for httpx
directly.
"""

from typing import TYPE_CHECKING

import httpx

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.models.meli_account import MeliAccount  # added in T2


class MeliClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._http = httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=15)

    async def exchange_code(self, code: str) -> dict:
        raise NotImplementedError("Implemented in T3 — see docs/ORCHESTRATION.md")

    async def refresh_token(self, account: "MeliAccount") -> dict:
        raise NotImplementedError("Implemented in T3 — see docs/ORCHESTRATION.md")

    async def get_me(self, access_token: str) -> dict:
        raise NotImplementedError("Implemented in T3 — see docs/ORCHESTRATION.md")

    async def get_order(self, account: "MeliAccount", meli_order_id: int) -> dict:
        raise NotImplementedError("Implemented in T3 — see docs/ORCHESTRATION.md")

    async def get_shipment(self, account: "MeliAccount", meli_shipment_id: int) -> dict:
        raise NotImplementedError("Implemented in T3 — see docs/ORCHESTRATION.md")

    async def search_orders(self, account: "MeliAccount", from_dt, offset: int = 0) -> dict:
        raise NotImplementedError("Implemented in T3 — see docs/ORCHESTRATION.md")

    async def get_label_pdf(self, account: "MeliAccount", meli_shipment_id: int) -> bytes:
        raise NotImplementedError("Implemented in T3 — see docs/ORCHESTRATION.md")

    async def aclose(self) -> None:
        await self._http.aclose()
