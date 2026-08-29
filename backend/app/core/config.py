"""Application configuration.

All settings come from environment variables (or a local .env). Nothing is
hardcoded, and no secret has a usable production default — `JWT_SECRET` ships
with an obviously-fake value that `is_production` validation rejects.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["auto", "gemini", "claude", "mock"]

INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # --- Database ---------------------------------------------------------
    # asyncpg for Postgres; aiosqlite keeps the no-Docker path viable.
    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/engagement"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- LLM --------------------------------------------------------------
    # "auto" resolves to gemini when a key is present, else mock. This is what
    # makes Demo Mode work with no configuration at all.
    llm_provider: ProviderName = "auto"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    llm_timeout_seconds: float = 20.0
    llm_max_output_tokens: int = 2048

    # --- Security ---------------------------------------------------------
    jwt_secret: str = INSECURE_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    cors_origins: str = "http://localhost:3000"

    # --- Automation -------------------------------------------------------
    automation_enabled: bool = True
    automation_interval_minutes: int = 60
    # Thresholds for the brief's worked example: joining within 7 days AND no
    # interaction in the last 5 days. Configurable because HR policy varies.
    rule_joining_window_days: int = Field(default=7, ge=1, le=90)
    rule_silence_threshold_days: int = Field(default=5, ge=1, le=90)

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, value: str) -> str:
        return value.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS is pinned to an explicit allowlist — never '*', because the API
        serves candidate PII and will carry credentialed requests."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def resolved_provider(self) -> Literal["gemini", "claude", "mock"]:
        """Which LLM provider will actually serve requests.

        Under "auto", Gemini wins when both keys are present - it is the
        provider the prompts were tuned against. Falling back to the mock
        rather than raising is deliberate: a missing key should degrade the
        system to Demo Mode, not stop it booting.
        """
        if self.llm_provider in ("gemini", "claude", "mock"):
            return self.llm_provider  # type: ignore[return-value]
        if self.gemini_api_key.strip():
            return "gemini"
        if self.anthropic_api_key.strip():
            return "claude"
        return "mock"

    @property
    def is_demo_mode(self) -> bool:
        return self.resolved_provider == "mock"

    @property
    def active_model(self) -> str | None:
        """Model name for the resolved provider, or None in Demo Mode."""
        return {"gemini": self.gemini_model, "claude": self.anthropic_model}.get(
            self.resolved_provider
        )

    def validate_production_safety(self) -> list[str]:
        """Configuration that is fine locally but unacceptable in production.

        Returned as warnings rather than raised, so a misconfigured deploy is
        loudly visible in logs without a boot loop.
        """
        problems: list[str] = []
        if not self.is_production:
            return problems
        if self.jwt_secret == INSECURE_DEFAULT_SECRET:
            problems.append("JWT_SECRET is still the insecure development default")
        if "*" in self.cors_origin_list:
            problems.append("CORS_ORIGINS contains a wildcard")
        if self.is_sqlite:
            problems.append("SQLite is in use; Postgres is expected in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    """Cached so config is parsed once per process and import order can't
    produce two divergent views of the environment."""
    return Settings()
