from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_event import WebhookEvent


class WebhookEventRepository:
    """webhook_events has NO tenant_id (docs/ARCHITECTURE.md §4.6, §6.2): intake
    happens before tenant resolution, so this repository does NOT follow the
    tenant-first rule — there is nothing to scope by yet (same reasoning as
    app/repositories/tenant.py for the Tenant model itself).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, event_id: int) -> WebhookEvent | None:
        return await self.session.get(WebhookEvent, event_id)

    async def create(
        self,
        *,
        topic: str,
        resource: str,
        meli_user_id: int,
        payload: dict,
        status: str = "received",
        provider: str = "meli",
    ) -> WebhookEvent:
        event = WebhookEvent(
            provider=provider,
            topic=topic,
            resource=resource,
            meli_user_id=meli_user_id,
            payload=payload,
            status=status,
        )
        self.session.add(event)
        await self.session.flush()
        return event
