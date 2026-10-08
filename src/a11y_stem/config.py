"""Settings, read from the environment (and `.env` in the project root).

Every cost limit is a setting, not a constant, so the numbers live in one place. The web
app refuses to start in production unless the spend cap, the page quota, the session
secret and the Google sign-in credentials are all set (`Settings.check_production`).
CLI runs (convert, eval) take explicit `--max-usd` limits instead of the daily cap.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The two models Harish chose on 2026-10-07 (ADR 0002). Each is the other's fallback.
MAIN_MODEL = "gpt-6.1-sol"
CHEAP_MODEL = "gpt-6-luna"


class ConfigError(RuntimeError):
    """A required setting is missing or inconsistent."""


class Settings(BaseSettings):
    # env_ignore_empty: `NAME=` (as in .env.example) means "not set", so an optional number
    # left blank falls back to its default instead of failing validation.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    env: Literal["dev", "test", "prod"] = "dev"

    # --- models
    openai_api_key: SecretStr | None = None
    main_model: str = MAIN_MODEL
    cheap_model: str = CHEAP_MODEL
    model_timeout_s: float = Field(default=60.0, gt=0)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    # Confidence at or above which a validated fix is accepted without review.
    auto_accept_confidence: float = Field(default=0.85, ge=0, le=1)
    # Below this the step abstains instead of proposing a fix.
    abstain_confidence: float = Field(default=0.4, ge=0, le=1)
    # Answer every model request with the offline FakeVisionClient (placeholder output),
    # so the web app can be tried end to end without an OpenAI key. Dev and test only.
    fake_models: bool = False

    # --- cost and abuse limits (no defaults for the money ones on purpose)
    daily_spend_cap_usd: float | None = Field(default=None, gt=0)
    per_user_daily_page_quota: int | None = Field(default=None, gt=0)
    max_usd_per_job: float = Field(default=2.0, gt=0)
    max_model_calls_per_job: int = Field(default=400, gt=0)
    # A job's caps are also at most this much per page of the upload (web.worker.job_caps),
    # so a short document cannot use a long one's allowance. Measured average: about
    # $0.004 and a few calls per PDF page; these leave room for equation-dense pages.
    max_usd_per_page: float = Field(default=0.30, gt=0)
    max_model_calls_per_page: int = Field(default=80, gt=0)
    max_pages_per_job: int = Field(default=30, gt=0)
    max_upload_mb: int = Field(default=20, gt=0)
    # Uploads taken in at once per instance (one per user); see web/uploads.py.
    max_concurrent_uploads: int = Field(default=3, gt=0)

    # --- storage and retention
    database_url: str = "sqlite:///.local/a11y-stem.db"
    retention_hours: int = Field(default=24, gt=0)
    opt_in_retention_days: int = Field(default=30, gt=0)

    # --- web, sign-in and cron
    base_url: str = "http://localhost:8000"
    session_secret: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Comma-separated emails created as allowed admins at startup (the allowlist itself
    # lives in the database; manage it with `a11y-stem users allow EMAIL`).
    admin_emails: str = ""
    # Local-only sign-in shortcut (any email, no password). Refused outside env=dev.
    dev_login_enabled: bool = False
    # Shared secret that Cloud Scheduler sends to /internal/cron/* (header X-Cron-Token).
    cron_token: SecretStr | None = None
    # Run the background worker thread inside the web process (one Cloud Run service).
    run_worker: bool = True
    worker_poll_s: float = Field(default=2.0, gt=0)

    # --- public pages
    source_url: str = "https://github.com/harishm17/a11y-stem"
    contact_email: str | None = None

    # --- tracing (off by default; never on for private-inputs/ runs)
    tracing_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None

    log_level: str = "INFO"

    @field_validator("admin_emails")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip() for e in self.admin_emails.split(",") if e.strip()]

    @property
    def fallback_for(self) -> dict[str, str]:
        """Fallback model per primary model: each of the two models covers the other."""
        return {self.main_model: self.cheap_model, self.cheap_model: self.main_model}

    def require_openai_key(self) -> str:
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value():
            raise ConfigError("OPENAI_API_KEY is not set (put it in .env; never commit it)")
        return self.openai_api_key.get_secret_value()

    def check_production(self) -> None:
        """Fail fast on a production start that would run without its safety limits."""
        if self.env != "prod":
            if self.dev_login_enabled and self.env not in {"dev", "test"}:
                raise ConfigError("DEV_LOGIN_ENABLED is only allowed with ENV=dev or test")
            return
        missing = [
            name
            for name, value in [
                ("DAILY_SPEND_CAP_USD", self.daily_spend_cap_usd),
                ("PER_USER_DAILY_PAGE_QUOTA", self.per_user_daily_page_quota),
                ("SESSION_SECRET", self.session_secret),
                ("GOOGLE_CLIENT_ID", self.google_client_id),
                ("GOOGLE_CLIENT_SECRET", self.google_client_secret),
                ("CRON_TOKEN", self.cron_token),
                ("OPENAI_API_KEY", self.openai_api_key),
                ("CONTACT_EMAIL", self.contact_email),
            ]
            if _blank(value)
        ]
        if missing:
            raise ConfigError("production start refused; missing: " + ", ".join(missing))
        if self.dev_login_enabled:
            raise ConfigError("DEV_LOGIN_ENABLED must be false in production")
        if self.fake_models:
            raise ConfigError("FAKE_MODELS must be false in production")
        if self.database_url.startswith("sqlite"):
            raise ConfigError("production needs Postgres (DATABASE_URL=postgresql+psycopg://...)")
        if not self.base_url.startswith("https://"):
            raise ConfigError("BASE_URL must be https in production")


def _blank(value: object) -> bool:
    """None, "", whitespace, or a SecretStr holding one of those."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value is None or (isinstance(value, str) and not value.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def local_dir() -> Path:
    """Git-ignored working directory for local databases and scratch output."""
    path = Path(".local")
    path.mkdir(exist_ok=True)
    return path
