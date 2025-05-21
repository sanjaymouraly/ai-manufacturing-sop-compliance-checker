"""Token accounting and cost estimation.

Prices are USD per 1,000 tokens. The table is intentionally small and is only
used when the provider does not publish pricing (OpenRouter does, via its model
catalogue). Unknown pricing is reported as unknown — never guessed — and the UI
shows "pricing unavailable" rather than a fabricated number.
"""

from __future__ import annotations

from app.services.llm.base import LLMUsage

#: provider -> model prefix -> (prompt, completion) USD per 1K tokens
STATIC_PRICING: dict[str, dict[str, tuple[float, float]]] = {
    "openai": {
        "gpt-4o-mini": (0.00015, 0.0006),
        "gpt-4o": (0.0025, 0.01),
        "gpt-4.1-mini": (0.0004, 0.0016),
        "gpt-4.1-nano": (0.0001, 0.0004),
        "gpt-4.1": (0.002, 0.008),
    },
    "anthropic": {
        "claude-3-5-haiku": (0.0008, 0.004),
        "claude-3-haiku": (0.00025, 0.00125),
        "claude-3-5-sonnet": (0.003, 0.015),
        "claude-sonnet-4": (0.003, 0.015),
    },
    "ollama": {},
    "offline": {},
}


def lookup_price(provider: str, model: str) -> tuple[float, float] | None:
    table = STATIC_PRICING.get(provider, {})
    model_l = (model or "").lower()
    best: tuple[int, tuple[float, float]] | None = None
    for prefix, price in table.items():
        if prefix in model_l and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else None


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token) for budgeting only."""
    return max(1, len(text or "") // 4)


def build_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    price_override: tuple[float, float] | None = None,
) -> LLMUsage:
    price = price_override or lookup_price(provider, model)
    usage = LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    if price is not None:
        prompt_price, completion_price = price
        usage.cost_usd = (prompt_tokens / 1000.0) * prompt_price + (
            completion_tokens / 1000.0
        ) * completion_price
        usage.pricing_known = True
    return usage


def estimate_cost(
    provider: str,
    model: str,
    prompt_text: str,
    max_output_tokens: int,
    price_override: tuple[float, float] | None = None,
) -> tuple[float, bool]:
    """Pre-flight cost estimate used by the spending guard."""
    price = price_override or lookup_price(provider, model)
    if price is None:
        return 0.0, False
    prompt_tokens = estimate_tokens(prompt_text)
    prompt_price, completion_price = price
    cost = (prompt_tokens / 1000.0) * prompt_price + (max_output_tokens / 1000.0) * completion_price
    return round(cost, 6), True
