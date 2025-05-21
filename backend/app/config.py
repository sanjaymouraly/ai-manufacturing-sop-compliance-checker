"""Application configuration.

All settings are environment-driven (12-factor) with safe local defaults so the
application runs fully offline on a fresh clone with zero configuration.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    app_name: str = "Manufacturing SOP AI Auditor"
    app_version: str = "1.0.0"
    environment: str = Field(default="development")
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False

    # --- Security ---
    secret_key: str = Field(default="dev-insecure-change-me-in-production")
    access_token_expire_minutes: int = 720
    jwt_algorithm: str = "HS256"
    # Comma separated list; "*" allowed only in development.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    rate_limit_per_minute: int = 240
    # Brute-force guard on sign-in, counted per client IP. Deliberately much tighter
    # than the global limit. Raise it only for automated test runs (E2E logs in many
    # times a minute); never weaken it for a real deployment.
    login_rate_limit_per_minute: int = 10
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "Admin#12345"

    # --- Storage / DB ---
    database_url: str = Field(default="")
    var_dir: str = Field(default="")
    max_upload_mb: int = 25
    allowed_upload_extensions: str = ".pdf,.docx,.txt,.md,.csv,.json"
    retention_days: int = 0  # 0 = keep forever

    # --- Analysis engine ---
    analysis_mode: str = "deterministic"  # deterministic | llm_assisted
    evidence_match_threshold: float = 0.52
    strict_citation_verification: bool = True
    enable_ocr: bool = False

    # --- LLM provider (backend-managed; never shipped to the frontend) ---
    llm_provider: str = "offline"  # offline|openai|anthropic|openrouter|custom|ollama
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 60
    llm_max_tokens: int = 1200
    llm_temperature: float = 0.0
    llm_max_retries: int = 2
    llm_allow_paid_requests: bool = False
    llm_request_budget_usd: float = 1.0
    # Cumulative token ceiling for a provider configuration. Unlike the dollar
    # budget this needs no price list, so it still bounds spending against a
    # gateway or self-hosted endpoint that publishes no rates. 0 disables it.
    llm_token_budget: int = 0
    llm_cache_enabled: bool = True
    llm_app_referer: str = "https://github.com/local/manufacturing-sop-ai-auditor"
    llm_app_title: str = "Manufacturing SOP AI Auditor"

    # --- Serving ---
    # Serve the built single-page app from this process, so a deployment is one
    # port and one container instead of an API plus a separate static host.
    serve_frontend: bool = False
    frontend_dist: str = Field(default="")

    @field_validator("analysis_mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in {"deterministic", "llm_assisted"}:
            raise ValueError("analysis_mode must be 'deterministic' or 'llm_assisted'")
        return v

    # --- Derived helpers ---
    @property
    def var_path(self) -> Path:
        p = Path(self.var_dir) if self.var_dir else (REPO_ROOT / "var")
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def storage_path(self) -> Path:
        p = self.var_path / "storage"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def export_path(self) -> Path:
        p = self.var_path / "exports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_path(self) -> Path:
        p = self.var_path / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.var_path / 'app.db').as_posix()}"

    @property
    def frontend_dist_path(self) -> Path | None:
        """Directory holding the built SPA, or None when it is not available."""
        if not self.serve_frontend:
            return None
        p = Path(self.frontend_dist) if self.frontend_dist else (REPO_ROOT / "frontend" / "dist")
        return p if (p / "index.html").is_file() else None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upload_extensions(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_upload_extensions.split(",") if e.strip()}

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Clear the settings cache (used by tests and by runtime config updates)."""
    get_settings.cache_clear()
    return get_settings()


# Convenience for modules that only need a value at import time.
IS_TESTING = bool(os.environ.get("PYTEST_CURRENT_TEST"))
