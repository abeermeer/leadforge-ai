"""Global infrastructure configuration.

Per-user third-party API keys (OpenAI/Anthropic/SendGrid/Google/Hunter/SocialCrawl)
do NOT live here — they are stored Fernet-encrypted per user in `user_settings`
and decrypted on demand via `deps.get_user_settings`.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "Trax9 Lead Gen"
    DEBUG: bool = False
    APP_BASE_URL: str = "http://localhost:8000"

    # Auth / secrets
    SECRET_KEY: str = "change-me-jwt-secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 72
    # Fernet key for encrypting user_settings.encrypted_keys.
    # Empty -> crypto.py derives a dev-only key from SECRET_KEY (never for production).
    FERNET_KEY: str = ""

    # Infra
    # SQLite default so local dev boots without Postgres; compose overrides with Postgres.
    DATABASE_URL: str = "sqlite:///./trax9_dev.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Scraping
    PROXY_POOL: str = ""  # comma-separated proxy URLs; empty = direct

    # Sending defaults (per-user overrides in user_settings)
    DEFAULT_MAX_EMAILS_PER_DAY: int = 100
    DEFAULT_MAX_EMAILS_PER_HOUR: int = 50
    SEND_START_HOUR: int = 8
    SEND_END_HOUR: int = 18

    # Warmup
    WARMUP_START_CAP: int = 10
    WARMUP_DAILY_INCREMENT: int = 5

    # Discovery
    PLACES_RADIUS_METERS: int = 50000
    MAX_LEADS_PER_DISCOVERY: int = 200

    # Enrichment
    SOCIAL_ENRICH_MIN_SCORE: int = 60

    # Quotas
    DEFAULT_MONTHLY_EMAIL_QUOTA: int = 1000

    # Observability
    SENTRY_DSN: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
