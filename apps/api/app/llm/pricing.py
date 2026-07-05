from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_price_per_1m_tokens: float
    output_price_per_1m_tokens: float


PRICING_TABLE: dict[str, ModelPricing] = {
    "gpt-4o": ModelPricing(input_price_per_1m_tokens=2.50, output_price_per_1m_tokens=10.00),
    "gpt-4o-mini": ModelPricing(input_price_per_1m_tokens=0.15, output_price_per_1m_tokens=0.60),
    "gpt-4-turbo": ModelPricing(input_price_per_1m_tokens=10.00, output_price_per_1m_tokens=30.00),
    "gpt-3.5-turbo": ModelPricing(input_price_per_1m_tokens=0.50, output_price_per_1m_tokens=1.50),
    "claude-3-5-haiku-latest": ModelPricing(
        input_price_per_1m_tokens=0.80, output_price_per_1m_tokens=4.00
    ),
    "claude-3-5-sonnet-latest": ModelPricing(
        input_price_per_1m_tokens=3.00, output_price_per_1m_tokens=15.00
    ),
    "claude-3-opus-latest": ModelPricing(
        input_price_per_1m_tokens=15.00, output_price_per_1m_tokens=75.00
    ),
}

DEFAULT_PRICING = ModelPricing(
    input_price_per_1m_tokens=1.00, output_price_per_1m_tokens=3.00
)


def get_pricing(model: str) -> ModelPricing:
    return PRICING_TABLE.get(model, DEFAULT_PRICING)


def estimate_cost_usd(
    *, prompt_tokens: int, completion_tokens: int, model: str
) -> float:
    pricing = get_pricing(model)
    input_cost = (prompt_tokens * pricing.input_price_per_1m_tokens) / 1_000_000
    output_cost = (completion_tokens * pricing.output_price_per_1m_tokens) / 1_000_000
    return round(input_cost + output_cost, 6)
