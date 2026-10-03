"""Runtime settings. Environment variables override these defaults."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://corestack:corestack@localhost:5432/corestack_mcp"
    )
    core_stack_base_url: str = "https://geoserver.core-stack.org"
    core_stack_api_key: str = ""
    mcp_allowed_hosts: str = ""
    admin_token: str = ""
    upstream_timeout_seconds: float = 120
    max_response_chars: int = 180_000
    trust_proxy: bool = False
    log_level: str = "INFO"

    @property
    def allowed_hosts(self) -> list[str]:
        return [item.strip() for item in self.mcp_allowed_hosts.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
