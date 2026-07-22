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
    JWT_EXPIRY_HOURS: int = 12  # shorter session (audit 1.5); was 72
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

    # Webhook security (audit 1.2). When set, SendGrid event-webhook signatures
    # are verified (ECDSA); when empty, verification is skipped with a warning
    # (dev only). The inbound-parse endpoint requires this secret in its path.
    SENDGRID_WEBHOOK_VERIFICATION_KEY: str = ""
    INBOUND_WEBHOOK_SECRET: str = ""

    # Rate limiting (audit 1.4). Redis-backed; fails OPEN if Redis is down.
    RATE_LIMIT_AUTH_MAX: int = 5          # attempts per window
    RATE_LIMIT_AUTH_WINDOW_SEC: int = 900  # 15 min
    RATE_LIMIT_ANALYZE_MAX: int = 10      # profile-analyze per window per user
    RATE_LIMIT_ANALYZE_WINDOW_SEC: int = 3600
    RATE_LIMIT_WEBHOOK_MAX: int = 240     # per IP per minute
    RATE_LIMIT_WEBHOOK_WINDOW_SEC: int = 60

    # CORS — comma-separated allowed origins for the browser frontend.
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def using_default_secret(self) -> bool:
        return self.SECRET_KEY == "change-me-jwt-secret"


# Placeholder values that must never run in production.
INSECURE_SECRET_DEFAULT = "change-me-jwt-secret"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
