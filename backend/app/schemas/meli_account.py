from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MeliAccountRead(BaseModel):
    """See docs/API.md §2. Token columns are deliberately absent — Pydantic
    only serializes fields declared here, so tokens can never leak by accident
    even though the ORM object carries them.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    meli_user_id: int
    nickname: str
    site_id: str
    status: str
    access_token_expires_at: datetime
    last_refresh_at: datetime | None
    created_at: datetime
