"""Settings, read from the environment (and `.env` in the project root).

Every cost limit is a setting, not a constant, so the numbers live in one place. The web
app refuses to start in production unless the spend cap, the page quota, the session
secret and the Google sign-in credentials are all set (`Settings.check_production`).
CLI runs (convert, eval) take explicit `--max-usd` limits instead of the daily cap.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    # --- second automated check: the OpenAI Decisions API (ADR 0005, docs/steps.md)
    # Only gpt-6-luna serves /v1/decisions. Answers come back in ~0.15 s, so a short
    # timeout: a slow check is skipped (fail-open), it never holds up a conversion.
    decisions_model: str = CHEAP_MODEL
    decisions_timeout_s: float = Field(default=10.0, gt=0)
    # Figures: ask whether an accepted alt text matches the image. Default on: the result
    # is only an optional note on an otherwise accepted figure (never a required decision,
    # never a status change), any failure leaves the figure as it is today, it costs about
    # $0.0001 per figure (2-3% of describing it), and the model's own confidence cannot
    # tell a wrong alt text from a right one (every real figure: 0.95-0.99), so without
    # it nothing ever points a reviewer at a factual error. Set false to turn it off.
    decisions_figure_verify: bool = True
    # Pre-registered rule (2026-10-08): a note when min(P(ok), 1 - P(factual error)) is
    # below this. Not calibrated on Harish's labels yet (docs/eval.md).
    decisions_figure_flag_below: float = Field(default=0.5, ge=0, le=1)
    # Equations: shadow mode only. P(meaning_changing) is recorded on the fix and counted
    # in the aggregates; it never changes a status, a confidence, a tier or escalation.
    decisions_equation_shadow: bool = True
    # Shadow only: P(meaning_changing) at or above this counts as "would flag" in the
    # aggregates and the eval (research value, tuned in-sample; not a gate).
    decisions_equation_flag_at: float = Field(default=0.2, ge=0, le=1)
    # A daily drift canary in the hourly cron (ops/canary.py): ~20 fixed requests, at
    # most once per UTC day; an answer that moves by more than 0.02 logs an ERROR.
    decisions_canary: bool = True

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
    # Microsoft (Entra ID) sign-in, next to Google. Only accounts of the listed tenants
    # (directory ids, comma-separated in the environment) may use it; with no tenant
    # listed it is off, even when a client id is set. The allowlist still applies.
    ms_client_id: str | None = None
    ms_client_secret: SecretStr | None = None
    ms_allowed_tenants: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Shared secret that Cloud Scheduler sends to /internal/cron/* (header X-Cron-Token).
    cron_token: SecretStr | None = None
    # Run the background worker thread inside the web process (one Cloud Run service).
    run_worker: bool = True
    worker_poll_s: float = Field(default=2.0, gt=0)

    # --- public pages
    source_url: str = "https://github.com/harishm17/a11y-stem"
    contact_email: str | None = None
    # Where the footer's "Feedback" link goes (http or https). Unset: an email to
    # CONTACT_EMAIL. A form service here is a new data processor: name it in the privacy
    # notice before setting it.
    feedback_url: str | None = None
    # The two optional questions after a download ("Will you post this for students?",
    # "About how many minutes did this save?"), stored as usage events: they need
    # EVENTS_ENABLED too.
    feedback_questions_enabled: bool = True
    # Content-free usage events (`events` table: a name, a code, a number; never text).
    events_enabled: bool = True

    # --- content features (docs/design/content-features.md). Each is off until its eval
    # says it helps; ConversionOptions.from_settings reads them for every conversion.
    # Rebuild data tables as <table> (off: a detected table is kept as an image of the
    # table with a required decision, "table not rebuilt").
    feature_tables: bool = False
    # Long descriptions and data tables for complex figures, and the colour-only flag.
    feature_long_descriptions: bool = False
    # Keep coloured text (colours adjusted to 4.5:1 contrast) and note colour references.
    feature_colour: bool = False
    # Per-course notation memory: hints for the equation step, spoken readings, bold
    # letters in running text as maths, "remember for this course" on the review screen.
    feature_notation_memory: bool = False

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

    @field_validator("feedback_url")
    @classmethod
    def _web_url(cls, value: str | None) -> str | None:
        """A link in every page footer: only a web address, never javascript: or data:."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.match(r"https?://[^\s\"'<>]+$", value):
            raise ValueError("FEEDBACK_URL must be an http or https address")
        return value

    @field_validator("ms_allowed_tenants", mode="before")
    @classmethod
    def _tenant_list(cls, value: object) -> object:
        """ "id1, id2" (the environment) or a list -> lower-case ids, blanks dropped."""
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            return [str(v).strip().lower() for v in value if str(v).strip()]
        return value

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip() for e in self.admin_emails.split(",") if e.strip()]

    @property
    def ms_sign_in_enabled(self) -> bool:
        """Microsoft sign-in is offered: client id, secret and at least one tenant set."""
        return not (_blank(self.ms_client_id) or _blank(self.ms_client_secret)) and bool(
            self.ms_allowed_tenants
        )

    @property
    def fallback_for(self) -> dict[str, str]:
        """Fallback model per primary model: each of the two models covers the other."""
        return {self.main_model: self.cheap_model, self.cheap_model: self.main_model}

    def require_openai_key(self) -> str:
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value():
            raise ConfigError("OPENAI_API_KEY is not set (put it in .env; never commit it)")
        return self.openai_api_key.get_secret_value()

    def check_production(self) -> None:
        """Fail fast on a production start that would run without its safety limits.

        The Microsoft sign-in settings are checked in every environment: half of them set
        is always a mistake (`_check_microsoft`).
        """
        self._check_microsoft()
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

    def _check_microsoft(self) -> None:
        """A client id needs its secret; a secret needs the tenants it may sign in from
        (an empty list would quietly turn the sign-in off); tenants are directory ids."""
        has_id, has_secret = not _blank(self.ms_client_id), not _blank(self.ms_client_secret)
        if has_id and not has_secret:
            raise ConfigError("MS_CLIENT_ID is set but MS_CLIENT_SECRET is not")
        if has_secret and not has_id:
            raise ConfigError("MS_CLIENT_SECRET is set but MS_CLIENT_ID is not")
        if has_secret and not self.ms_allowed_tenants:
            raise ConfigError("MS_CLIENT_SECRET is set but MS_ALLOWED_TENANTS lists no tenant")
        bad = [t for t in self.ms_allowed_tenants if not _TENANT_ID.fullmatch(t)]
        if bad:
            raise ConfigError(
                "MS_ALLOWED_TENANTS must list directory (tenant) ids, like "
                "00000000-0000-0000-0000-000000000000"
            )


# An Entra ID directory (tenant) id: a GUID. "common" and "organizations" are not tenants.
_TENANT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


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
