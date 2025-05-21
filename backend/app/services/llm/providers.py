"""Concrete LLM provider adapters.

Implemented
-----------
* ``openai``     – api.openai.com (Chat Completions)
* ``openrouter`` – openrouter.ai (OpenAI-compatible, publishes live pricing)
* ``custom``     – any OpenAI-compatible endpoint (user-supplied base URL)
* ``anthropic``  – api.anthropic.com (Messages API)
* ``ollama``     – local inference, free, no credentials
* ``offline``    – no network at all; deterministic placeholder used so the rest
                   of the pipeline has a uniform interface

Adding a provider = subclass :class:`LLMAdapter` and register it in ``REGISTRY``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.logging_setup import get_logger
from app.services.llm.base import (
    LLMAdapter,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMResult,
    LLMTimeoutError,
    LLMUsage,
    ModelInfo,
    ProviderSettings,
)
from app.services.llm.pricing import build_usage, estimate_cost, estimate_tokens

logger = get_logger(__name__)


def _raise_for_status(response: httpx.Response, provider: str) -> None:
    if response.status_code < 400:
        return
    try:
        detail = response.json()
        message = (
            detail.get("error", {}).get("message")
            if isinstance(detail.get("error"), dict)
            else detail.get("error") or detail.get("message") or response.text[:400]
        )
    except Exception:
        message = response.text[:400]
    if response.status_code in (401, 403):
        raise LLMAuthError(f"{provider}: {message}", status_code=response.status_code, provider=provider)
    if response.status_code == 429:
        raise LLMRateLimitError(f"{provider}: {message}", status_code=response.status_code, provider=provider)
    raise LLMError(
        f"{provider} HTTP {response.status_code}: {message}",
        status_code=response.status_code,
        provider=provider,
    )


class OpenAICompatibleAdapter(LLMAdapter):
    """Chat Completions-compatible provider."""

    provider_type = "openai"
    default_base_url = "https://api.openai.com/v1"

    @property
    def base_url(self) -> str:
        return (self.settings.base_url or self.default_base_url).rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    async def list_models(self) -> list[ModelInfo]:
        url = f"{self.base_url}/models"
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            try:
                response = await client.get(url, headers=self._headers())
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(str(exc), provider=self.provider_type) from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Cannot reach {url}: {exc}", provider=self.provider_type) from exc
        _raise_for_status(response, self.provider_type)
        payload = response.json()
        items = payload.get("data", payload if isinstance(payload, list) else [])
        models: list[ModelInfo] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id") or item.get("name")
            if not model_id:
                continue
            pricing = item.get("pricing") or {}
            try:
                raw_prompt = pricing.get("prompt")
                prompt_price = float(raw_prompt) * 1000 if raw_prompt is not None else None
                raw_completion = pricing.get("completion")
                completion_price = float(raw_completion) * 1000 if raw_completion is not None else None
            except (TypeError, ValueError):
                prompt_price = completion_price = None
            models.append(
                ModelInfo(
                    id=str(model_id),
                    name=str(item.get("name") or model_id),
                    context_length=item.get("context_length")
                    or (item.get("top_provider") or {}).get("context_length"),
                    prompt_price_per_1k=prompt_price,
                    completion_price_per_1k=completion_price,
                    supports_json=None,
                )
            )
        return sorted(models, key=lambda m: m.id)

    async def _price_override(self) -> tuple[float, float] | None:
        """OpenRouter/custom endpoints can publish pricing; use it when present."""
        if self.provider_type not in {"openrouter", "custom"}:
            return None
        try:
            for model in await self.list_models():
                if (
                    model.id == self.settings.model
                    and model.prompt_price_per_1k is not None
                    and model.completion_price_per_1k is not None
                ):
                    return model.prompt_price_per_1k, model.completion_price_per_1k
        except LLMError:
            return None
        return None

    async def complete(self, system: str, user: str, *, json_mode: bool = True) -> LLMResult:
        if not self.settings.model:
            raise LLMError("No model configured for this provider.", provider=self.provider_type)
        # Guard before touching the network. Fetching live pricing is itself an
        # outbound call that ships the API key to the provider, so a deployment
        # that has withheld spending authorisation must be refused first, on a
        # table-based estimate, rather than after contacting a third party.
        provisional, _ = estimate_cost(
            self.provider_type, self.settings.model, system + user, self.settings.max_tokens, None
        )
        self.guard_paid_request(provisional)

        price_override = await self._price_override()
        estimated, known = estimate_cost(
            self.provider_type, self.settings.model, system + user, self.settings.max_tokens, price_override
        )
        # Live pricing can be higher than the built-in table; re-check against
        # the refined figure before committing to the request.
        self.guard_paid_request(estimated)

        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = self._headers()
        if self.provider_type == "openrouter":
            headers["HTTP-Referer"] = self.settings.app_referer or "http://localhost"
            headers["X-Title"] = self.settings.app_title or "SOP Auditor"

        url = f"{self.base_url}/chat/completions"
        start = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 400 and json_mode:
                    # Model may not support response_format: retry once without it.
                    payload.pop("response_format", None)
                    json_mode = False
                    continue
                _raise_for_status(response, self.provider_type)
                data = response.json()
                break
            except (LLMRateLimitError, LLMTimeoutError) as exc:
                last_error = exc
                if attempt >= self.settings.max_retries:
                    raise
                time.sleep(min(2**attempt, 4))
            except httpx.TimeoutException as exc:
                last_error = LLMTimeoutError(str(exc), provider=self.provider_type)
                if attempt >= self.settings.max_retries:
                    raise last_error from exc
            except httpx.HTTPError as exc:
                raise LLMError(
                    f"{self.provider_type} transport error: {exc}", provider=self.provider_type
                ) from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise last_error or LLMError("Unknown provider failure", provider=self.provider_type)

        latency_ms = int((time.perf_counter() - start) * 1000)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{self.provider_type} returned no choices.", provider=self.provider_type)
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        usage_raw = data.get("usage") or {}
        usage = build_usage(
            provider=self.provider_type,
            model=self.settings.model,
            prompt_tokens=int(usage_raw.get("prompt_tokens") or estimate_tokens(system + user)),
            completion_tokens=int(usage_raw.get("completion_tokens") or estimate_tokens(text)),
            price_override=price_override,
        )
        if not known and not usage.pricing_known:
            logger.info(
                "Pricing for %s/%s is unknown; cost reported as unavailable.",
                self.provider_type,
                self.settings.model,
            )
        return LLMResult(
            text=text,
            provider=self.provider_type,
            model=data.get("model") or self.settings.model,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=choices[0].get("finish_reason"),
            raw={"id": data.get("id")},
        )


class OpenRouterAdapter(OpenAICompatibleAdapter):
    provider_type = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"


class CustomOpenAIAdapter(OpenAICompatibleAdapter):
    provider_type = "custom"
    default_base_url = ""

    @property
    def base_url(self) -> str:
        if not self.settings.base_url:
            raise LLMError(
                "A base URL is required for the custom OpenAI-compatible provider.", provider="custom"
            )
        return self.settings.base_url.rstrip("/")


class AnthropicAdapter(LLMAdapter):
    provider_type = "anthropic"
    default_base_url = "https://api.anthropic.com/v1"
    api_version = "2023-06-01"

    @property
    def base_url(self) -> str:
        return (self.settings.base_url or self.default_base_url).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.settings.api_key,
            "anthropic-version": self.api_version,
        }

    async def list_models(self) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            try:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(str(exc), provider=self.provider_type) from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Cannot reach Anthropic: {exc}", provider=self.provider_type) from exc
        _raise_for_status(response, self.provider_type)
        data = response.json().get("data", [])
        return [
            ModelInfo(id=item["id"], name=str(item.get("display_name") or item["id"]))
            for item in data
            if isinstance(item, dict) and item.get("id")
        ]

    async def complete(self, system: str, user: str, *, json_mode: bool = True) -> LLMResult:
        if not self.settings.model:
            raise LLMError("No model configured for Anthropic.", provider=self.provider_type)
        estimated, _ = estimate_cost(
            self.provider_type, self.settings.model, system + user, self.settings.max_tokens
        )
        self.guard_paid_request(estimated)

        payload = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "temperature": self.settings.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/messages", headers=self._headers(), json=payload
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(str(exc), provider=self.provider_type) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Anthropic transport error: {exc}", provider=self.provider_type) from exc
        _raise_for_status(response, self.provider_type)
        data = response.json()
        blocks = data.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        usage_raw = data.get("usage") or {}
        usage = build_usage(
            provider=self.provider_type,
            model=self.settings.model,
            prompt_tokens=int(usage_raw.get("input_tokens") or estimate_tokens(system + user)),
            completion_tokens=int(usage_raw.get("output_tokens") or estimate_tokens(text)),
        )
        return LLMResult(
            text=text,
            provider=self.provider_type,
            model=data.get("model") or self.settings.model,
            usage=usage,
            latency_ms=int((time.perf_counter() - start) * 1000),
            finish_reason=data.get("stop_reason"),
            raw={"id": data.get("id")},
        )


class OllamaAdapter(LLMAdapter):
    provider_type = "ollama"
    requires_api_key = False
    is_paid = False
    default_base_url = "http://localhost:11434"

    @property
    def base_url(self) -> str:
        return (self.settings.base_url or self.default_base_url).rstrip("/")

    async def list_models(self) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            try:
                response = await client.get(f"{self.base_url}/api/tags")
            except httpx.HTTPError as exc:
                raise LLMError(
                    f"Cannot reach Ollama at {self.base_url}. Is `ollama serve` running? ({exc})",
                    provider=self.provider_type,
                ) from exc
        _raise_for_status(response, self.provider_type)
        return [
            ModelInfo(id=m["name"], name=m.get("name", ""), context_length=None)
            for m in response.json().get("models", [])
            if m.get("name")
        ]

    async def complete(self, system: str, user: str, *, json_mode: bool = True) -> LLMResult:
        if not self.settings.model:
            raise LLMError("No model configured for Ollama.", provider=self.provider_type)
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "prompt": user,
            "system": system,
            "stream": False,
            "options": {"temperature": self.settings.temperature, "num_predict": self.settings.max_tokens},
        }
        if json_mode:
            payload["format"] = "json"
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(str(exc), provider=self.provider_type) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama transport error: {exc}", provider=self.provider_type) from exc
        _raise_for_status(response, self.provider_type)
        data = response.json()
        text = data.get("response", "")
        usage = LLMUsage(
            prompt_tokens=int(data.get("prompt_eval_count") or estimate_tokens(system + user)),
            completion_tokens=int(data.get("eval_count") or estimate_tokens(text)),
            cost_usd=0.0,
            pricing_known=True,  # local inference is free
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        return LLMResult(
            text=text,
            provider=self.provider_type,
            model=self.settings.model,
            usage=usage,
            latency_ms=int((time.perf_counter() - start) * 1000),
            finish_reason=data.get("done_reason"),
        )


class OfflineAdapter(LLMAdapter):
    """No-network adapter used by the default deterministic mode.

    It performs **no** inference. It returns a valid, schema-conformant payload
    stating that no LLM was consulted, so the pipeline behaves identically with
    and without a provider and nothing is ever fabricated.
    """

    provider_type = "offline"
    requires_api_key = False
    is_paid = False

    async def complete(self, system: str, user: str, *, json_mode: bool = True) -> LLMResult:
        payload = {
            "observations": [],
            "notes": [
                "Offline deterministic mode: no language model was consulted. "
                "All findings in this audit come from rule-based analysis."
            ],
        }
        return LLMResult(
            text=json.dumps(payload),
            provider=self.provider_type,
            model="offline-deterministic",
            usage=LLMUsage(pricing_known=True),
            latency_ms=0,
        )

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="offline-deterministic", name="Offline deterministic engine (no LLM)")]

    async def test_connection(self) -> dict[str, Any]:
        return {
            "ok": True,
            "provider": "offline",
            "models_discovered": 1,
            "models_sample": ["offline-deterministic"],
            "paid_request_made": False,
            "message": "Offline deterministic engine is always available; no network access required.",
        }


REGISTRY: dict[str, type[LLMAdapter]] = {
    "offline": OfflineAdapter,
    "openai": OpenAICompatibleAdapter,
    "openrouter": OpenRouterAdapter,
    "custom": CustomOpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "ollama": OllamaAdapter,
}


def build_adapter(settings: ProviderSettings) -> LLMAdapter:
    adapter_cls = REGISTRY.get(settings.provider_type)
    if adapter_cls is None:
        raise LLMError(
            f"Unknown provider '{settings.provider_type}'. Available: {', '.join(sorted(REGISTRY))}."
        )
    return adapter_cls(settings)
