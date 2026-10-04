"""Runtime settings. Environment variables override these defaults."""

from functools import lru_cache
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://corestack:corestack@localhost:5432/corestack_mcp"
    )
    # Set by the deploy Compose file. The password is quoted here so characters
    # such as / or ? cannot change the hostname in DATABASE_URL.
    postgres_host: str = ""
    postgres_user: str = "corestack"
    postgres_password: str = ""
    postgres_db: str = "corestack_mcp"
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

    def resolved_database_url(self) -> str:
        if not self.postgres_host:
            return self.database_url
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        database = quote(self.postgres_db, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:5432/{database}?ssl=disable"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
