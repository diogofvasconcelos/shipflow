from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import ArqPoolDep, DbSession
from app.services.webhook_intake import EnqueueFailedError, WebhookIntakeService

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/meli")
async def meli_webhook(request: Request, session: DbSession, pool: ArqPoolDep) -> JSONResponse:
    """Public, unauthenticated (ARCHITECTURE §11). Always 200 unless the enqueue
    itself fails — never 4xx, since ML retries any non-2xx forever.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({}, status_code=200)

    try:
        await WebhookIntakeService(session, pool).intake(body)
    except EnqueueFailedError:
        return JSONResponse({}, status_code=500)

    return JSONResponse({}, status_code=200)
