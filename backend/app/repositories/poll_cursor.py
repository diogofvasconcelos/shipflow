from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.poll_cursor import PollCursor


class PollCursorRepository:
    """No tenant_id (docs/ARCHITECTURE.md §4.6): poll_cursors is keyed 1:1 by
    meli_account_id, and the poller is a cross-tenant background job by nature
    (§5, last paragraph) — same reasoning as WebhookEventRepository.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, meli_account_id: int) -> datetime | None:
        cursor = await self.session.get(PollCursor, meli_account_id)
        return cursor.orders_last_polled_at if cursor else None

    async def set_cursor(self, meli_account_id: int, when: datetime) -> None:
        cursor = await self.session.get(PollCursor, meli_account_id)
        if cursor is None:
            self.session.add(
                PollCursor(meli_account_id=meli_account_id, orders_last_polled_at=when)
            )
        else:
            cursor.orders_last_polled_at = when
        await self.session.flush()
