from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_outbox import EventOutbox


class EventOutboxRepository:
    """tenant_id is the first argument of every method here (CLAUDE.md rule)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_outbox_event(self, tenant_id: int, event_type: str, payload: dict) -> EventOutbox:
        """Transactional outbox write (docs/ARCHITECTURE.md §9): call this in the
        SAME transaction as the state change it records, never inline to EventHub.
        """
        row = EventOutbox(tenant_id=tenant_id, event_type=event_type, payload=payload)
        self.session.add(row)
        await self.session.flush()
        return row
