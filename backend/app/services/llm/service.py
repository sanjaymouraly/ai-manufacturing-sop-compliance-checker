"""Provider resolution, response caching and connection testing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_setup import get_logger
from app.models.entities import ProviderConfig
from app.security import decrypt_secret, encrypt_secret, mask_secret
from app.services.llm.base import (
    LLMAdapter,
    LLMError,
    LLMResult,
    ProviderSettings,
)
from app.services.llm.prompts import CONNECTION_TEST_PROMPT
from app.services.llm.providers import REGISTRY, build_adapter
from app.utils.text import sha256_text

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Response cache (file-backed, deterministic key)
# --------------------------------------------------------------------------- #
class ResponseCache:
    def __init__(self) -> None:
        self.dir = get_settings().cache_path / "llm"
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(provider: str, model: str, system: str, user: str) -> str:
        return sha256_text("|".join([provider, model, system, user]))

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:  # pragma: no cover - corrupt cache entry
            return None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        (self.dir / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")

    def clear(self) -> int:
        count = 0
        for path in self.dir.glob("*.json"):
            path.unlink()
            count += 1
        return count


# --------------------------------------------------------------------------- #
# Provider configuration
# --------------------------------------------------------------------------- #
def settings_from_config(config: ProviderConfig) -> ProviderSettings:
    app_settings = get_settings()
    return ProviderSettings(
        provider_type=config.provider_type,
        model=config.model or "",
        base_url=config.base_url or "",
        api_key=decrypt_secret(config.api_key_encrypted or ""),
        timeout_seconds=config.timeout_seconds,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        max_retries=app_settings.llm_max_retries,
        allow_paid_requests=config.allow_paid_requests,
        request_budget_usd=config.request_budget_usd,
        spent_usd=config.spent_usd,
        token_budget=config.token_budget or 0,
        spent_tokens=config.spent_tokens or 0,
        app_referer=app_settings.llm_app_referer,
        app_title=app_settings.llm_app_title,
    )


def settings_from_env() -> ProviderSettings:
    s = get_settings()
    return ProviderSettings(
        provider_type=s.llm_provider or "offline",
        model=s.llm_model,
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        timeout_seconds=s.llm_timeout_seconds,
        max_tokens=s.llm_max_tokens,
        temperature=s.llm_temperature,
        max_retries=s.llm_max_retries,
        allow_paid_requests=s.llm_allow_paid_requests,
        request_budget_usd=s.llm_request_budget_usd,
        token_budget=s.llm_token_budget,
        app_referer=s.llm_app_referer,
        app_title=s.llm_app_title,
    )


def get_active_config(db: Session) -> ProviderConfig | None:
    return db.scalar(select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1))


def get_active_settings(db: Session | None) -> ProviderSettings:
    """DB configuration wins; otherwise fall back to environment; otherwise offline."""
    if db is not None:
        config = get_active_config(db)
        if config is not None:
            return settings_from_config(config)
    return settings_from_env()


def get_adapter(db: Session | None = None) -> LLMAdapter:
    return build_adapter(get_active_settings(db))


def available_providers() -> list[dict[str, Any]]:
    descriptions = {
        "offline": "Deterministic rule engine only. No network, no cost, always available.",
        "openai": "OpenAI platform API (requires an API key with billing; a ChatGPT subscription is not sufficient).",
        "anthropic": "Anthropic Claude Messages API.",
        "openrouter": "OpenRouter aggregator; publishes live per-model pricing.",
        "custom": "Any OpenAI-compatible endpoint (vLLM, LM Studio, Together, Groq, Azure-compatible gateways…).",
        "ollama": "Local Ollama server. Free, runs on your machine.",
    }
    out = []
    for key, cls in REGISTRY.items():
        out.append(
            {
                "provider_type": key,
                "requires_api_key": cls.requires_api_key,
                "is_paid": cls.is_paid,
                "default_base_url": getattr(cls, "default_base_url", ""),
                "description": descriptions.get(key, ""),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
async def test_connection(settings: ProviderSettings, *, run_inference: bool = False) -> dict[str, Any]:
    """Validate reachability/credentials. Inference is opt-in and budget-guarded."""
    adapter = build_adapter(settings)
    result: dict[str, Any] = {}
    try:
        result = await adapter.test_connection()
    except LLMError as exc:
        return {
            "ok": False,
            "provider": settings.provider_type,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "hint": exc.user_message,
            "paid_request_made": False,
        }

    if settings.model and result.get("models_sample") is not None:
        discovered = result.get("models_sample") or []
        if discovered and settings.model not in discovered:
            try:
                all_models = [m.id for m in await adapter.list_models()]
                result["model_available"] = settings.model in all_models
                if not result["model_available"]:
                    result["message"] = (
                        f"Connected, but model '{settings.model}' was not found in the provider catalogue."
                    )
            except LLMError:
                result["model_available"] = None
        else:
            result["model_available"] = settings.model in discovered if discovered else None

    if run_inference:
        try:
            completion = await adapter.complete(
                "You are a connection test. Reply with JSON only.", CONNECTION_TEST_PROMPT, json_mode=True
            )
            result["inference_ok"] = True
            result["inference_text"] = completion.text[:200]
            result["usage"] = completion.usage.to_dict()
            result["latency_ms"] = completion.latency_ms
            result["paid_request_made"] = adapter.is_paid
            result["message"] = "Connection verified with a live inference request."
        except LLMError as exc:
            result["ok"] = False
            result["inference_ok"] = False
            result["error_type"] = exc.__class__.__name__
            result["message"] = str(exc)
            result["hint"] = exc.user_message
    return result


async def complete_with_cache(
    adapter: LLMAdapter, system: str, user: str, *, use_cache: bool = True
) -> LLMResult:
    cache = ResponseCache() if use_cache and get_settings().llm_cache_enabled else None
    key = ResponseCache.key(adapter.provider_type, adapter.settings.model, system, user)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            logger.info("LLM cache hit for %s/%s", adapter.provider_type, adapter.settings.model)
            from app.services.llm.base import LLMUsage

            return LLMResult(
                text=cached["text"],
                provider=cached["provider"],
                model=cached["model"],
                usage=LLMUsage(**cached.get("usage", {})),
                latency_ms=0,
                cached=True,
            )
    result = await adapter.complete(system, user, json_mode=True)
    if cache is not None:
        cache.put(
            key,
            {
                "text": result.text,
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage.to_dict(),
                "cached_at": datetime.now(UTC).isoformat(),
            },
        )
    return result


def record_spend(db: Session, config: ProviderConfig | None, result: LLMResult) -> None:
    if config is None:
        return
    config.spent_usd = round((config.spent_usd or 0.0) + (result.usage.cost_usd or 0.0), 6)
    # Tokens are recorded whether or not the provider publishes prices, so the
    # ceiling keeps working where the dollar budget cannot.
    config.spent_tokens = (config.spent_tokens or 0) + (result.usage.total_tokens or 0)
    db.flush()


def upsert_provider_config(
    db: Session,
    *,
    name: str,
    provider_type: str,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    timeout_seconds: int,
    max_tokens: int,
    temperature: float,
    allow_paid_requests: bool,
    request_budget_usd: float,
    token_budget: int = 0,
    make_active: bool,
) -> ProviderConfig:
    config = db.scalar(select(ProviderConfig).where(ProviderConfig.name == name))
    if config is None:
        config = ProviderConfig(name=name, provider_type=provider_type)
        db.add(config)
    config.provider_type = provider_type
    config.base_url = base_url
    config.model = model
    config.timeout_seconds = timeout_seconds
    config.max_tokens = max_tokens
    config.temperature = temperature
    config.allow_paid_requests = allow_paid_requests
    config.request_budget_usd = request_budget_usd
    config.token_budget = token_budget
    if api_key:
        config.api_key_encrypted = encrypt_secret(api_key)
        config.api_key_last4 = api_key[-4:]
    if make_active:
        for other in db.scalars(select(ProviderConfig)):
            other.is_active = False
        config.is_active = True
    db.flush()
    logger.info(
        "Provider config '%s' saved (type=%s, model=%s, key=%s, active=%s)",
        name,
        provider_type,
        model,
        mask_secret(api_key or ""),
        config.is_active,
    )
    return config
