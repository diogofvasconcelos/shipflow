from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """See docs/ARCHITECTURE.md §12 for the meaning of every field."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://shipflow:shipflow@localhost:5432/shipflow"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "changeme"
    token_encryption_key: str = "changeme"

    meli_client_id: str = ""
    meli_client_secret: str = ""
    meli_redirect_uri: str = ""

    base_url: str = "http://localhost:8000"

    eventhub_enabled: bool = False
    eventhub_url: str = ""
    eventhub_token: str = ""

    display_tz: str = "America/Sao_Paulo"
    # Relative to the backend/ working directory (local dev and Docker both run from there).
    label_storage_dir: str = "./data/labels"
    templates_dir: str = "../frontend/templates"
    static_dir: str = "../frontend/static"

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
