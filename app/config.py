from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://policies:local-only-change-me@localhost:5432/policies"
    rabbitmq_url: str = "amqp://policies:local-only-change-me@localhost:5672//"
    core_base_url: str = "http://localhost:8081"
    core_timeout_seconds: float = 2.0
    api_read_token: str = "demo-read-token"
    api_operator_token: str = "demo-operator-token"
    api_reviewer_token: str = "demo-reviewer-token"
    demo_controls_enabled: bool = False
    log_level: str = "INFO"
    renewal_days_ahead: int = 30
    max_core_concurrency: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
