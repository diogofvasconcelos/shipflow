"""Safety-net token refresh cron (ARCHITECTURE §6.1): keeps tokens warm on
quiet days so the lazy-refresh path never meets an expired refresh window.
"""

import logging
from datetime import UTC, datetime, timedelta

from app.core.db import SessionLocal
from app.integrations.meli.client import MeliClient
from app.integrations.meli.errors import MeliError, MeliReauthRequired
from app.repositories.meli_account import list_accounts_expiring_before

logger = logging.getLogger("app.workers.tokens")


async def refresh_stale_tokens(ctx: dict) -> None:
    cutoff = datetime.now(UTC) + timedelta(hours=1)
    async with SessionLocal() as session:
        accounts = await list_accounts_expiring_before(session, cutoff)

    if not accounts:
        return

    client = MeliClient()
    try:
        for account in accounts:
            try:
                await client.refresh_token(account, force=True)
                logger.info("refreshed tokens for account %s", account.id)
            except MeliReauthRequired:
                logger.warning("account %s flipped to reauth_required", account.id)
            except MeliError:
                logger.error("token refresh failed for account %s", account.id)
    finally:
        await client.aclose()
