"""Provider-independent LLM adapter interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    """Base class for provider errors."""

    user_message = "The AI provider request failed."

    def __init__(self, message: str, *, status_code: int | None = None, provider: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider


class LLMNotConfiguredError(LLMError):
    user_message = "No AI provider is configured. The deterministic offline engine remains available."


class LLMAuthError(LLMError):
    user_message = "The AI provider rejected the credentials (check the API key and base URL)."


class LLMRateLimitError(LLMError):
    user_message = "The AI provider rate-limited the request. Retry later or reduce concurrency."


class LLMTimeoutError(LLMError):
    user_message = "The AI provider did not respond within the configured timeout."


class LLMBudgetError(LLMError):
    user_message = "The request was blocked by the configured spending guard."


class LLMResponseError(LLMError):
    user_message = "The AI provider returned a response that failed schema validation."


@dataclass
class ProviderSettings:
    provider_type: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout_seconds: int = 60
    max_tokens: int = 1200
    temperature: float = 0.0
    max_retries: int = 2
    allow_paid_requests: bool = False
    request_budget_usd: float = 1.0
    spent_usd: float = 0.0
    # A dollar budget is unenforceable against a provider that publishes no
    # prices: the pre-flight estimate is 0.0, so the projection never exceeds
    # the cap. A token ceiling needs no price list, so it still bounds a run
    # on a gateway or self-hosted endpoint. 0 disables the check.
    token_budget: int = 0
    spent_tokens: int = 0
    app_referer: str = ""
    app_title: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    pricing_known: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "pricing_known": self.pricing_known,
        }


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: int = 0
    cached: bool = False
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    context_length: int | None = None
    prompt_price_per_1k: float | None = None
    completion_price_per_1k: float | None = None
    supports_json: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "context_length": self.context_length,
            "prompt_price_per_1k": self.prompt_price_per_1k,
            "completion_price_per_1k": self.completion_price_per_1k,
            "supports_json": self.supports_json,
        }


class LLMAdapter(abc.ABC):
    """All providers implement this small surface."""

    provider_type: str = "abstract"
    requires_api_key: bool = True
    is_paid: bool = True

    def __init__(self, settings: ProviderSettings):
        self.settings = settings

    @abc.abstractmethod
    async def complete(self, system: str, user: str, *, json_mode: bool = True) -> LLMResult:
        """Run one completion request."""

    async def list_models(self) -> list[ModelInfo]:
        """Discover available models (never invents identifiers)."""
        return []

    async def test_connection(self) -> dict[str, Any]:
        """Lightweight reachability/credential check."""
        models = await self.list_models()
        return {
            "ok": True,
            "provider": self.provider_type,
            "models_discovered": len(models),
            "models_sample": [m.id for m in models[:10]],
            "paid_request_made": False,
            "message": "Connection and credentials verified via the model catalogue endpoint.",
        }

    def guard_paid_request(self, estimated_cost: float = 0.0) -> None:
        """Block chargeable traffic unless the operator explicitly allowed it."""
        if not self.is_paid:
            return
        if not self.settings.allow_paid_requests:
            raise LLMBudgetError(
                "Paid AI requests are disabled for this provider configuration. "
                "Enable 'allow paid requests' in AI Provider Settings to proceed.",
                provider=self.provider_type,
            )
        projected = self.settings.spent_usd + estimated_cost
        if self.settings.request_budget_usd and projected > self.settings.request_budget_usd:
            raise LLMBudgetError(
                f"Estimated spend ${projected:.4f} would exceed the configured budget of "
                f"${self.settings.request_budget_usd:.2f}.",
                provider=self.provider_type,
            )
        # Independent of price: bounds the run even when the provider publishes
        # no rates, which is the case for most gateways and local servers.
        if self.settings.token_budget:
            projected_tokens = self.settings.spent_tokens + self.settings.max_tokens
            if projected_tokens > self.settings.token_budget:
                raise LLMBudgetError(
                    f"This request could take cumulative usage to ~{projected_tokens:,} tokens, "
                    f"beyond the configured ceiling of {self.settings.token_budget:,}. "
                    "Raise the token budget in AI Provider Settings to continue.",
                    provider=self.provider_type,
                )
