from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
    "claude-3-haiku-20240307": ModelPricing(
        input_price_per_1m_tokens=0.25, output_price_per_1m_tokens=1.25
    ),
    "claude-3-opus-latest": ModelPricing(
        input_price_per_1m_tokens=15.00, output_price_per_1m_tokens=75.00
    ),
}


def get_pricing(model: str) -> ModelPricing | None:
    """Return the pricing for a known model, or ``None`` if unknown.

    An unknown model has no reliable price. Callers (``estimate_cost_usd``)
    treat ``None`` as a zero-cost estimate rather than fabricating a default
    price (testing-strategy U-19): the cost is always labelled "estimate" in
    the UI, and 0.0 is the honest sentinel for "price unknowable".
    """
    return PRICING_TABLE.get(model)


def estimate_cost_usd(
    *, prompt_tokens: int, completion_tokens: int, model: str
) -> float:
    pricing = get_pricing(model)
    if pricing is None:
        if prompt_tokens > 0 or completion_tokens > 0:
            logger.warning(
                "Unknown model %r with %s prompt / %s completion tokens: cost estimate set to 0.0 (no pricing entry)",
                model,
                prompt_tokens,
                completion_tokens,
            )
        else:
            logger.debug(
                "Unknown model %r: cost estimate set to 0.0 (no pricing entry)", model
            )
        return 0.0
    input_cost = (prompt_tokens * pricing.input_price_per_1m_tokens) / 1_000_000
    output_cost = (completion_tokens * pricing.output_price_per_1m_tokens) / 1_000_000
    return round(input_cost + output_cost, 6)
