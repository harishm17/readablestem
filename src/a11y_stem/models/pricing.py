"""Per-model prices and cost arithmetic.

Prices are USD per 1M tokens, copied from https://developers.openai.com/api/docs/pricing
on 2026-10-07 (standard tier, short context). Reasoning tokens are billed as output tokens
and are already included in `output_tokens`. Update `PRICES_CHECKED` when you re-check.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICES_CHECKED = "2026-10-07"


@dataclass(frozen=True)
class Price:
    input: float  # per 1M uncached input tokens
    cached_input: float  # per 1M cached input tokens
    output: float  # per 1M output tokens (reasoning included)


PRICES: dict[str, Price] = {
    "gpt-6.1-sol": Price(input=2.00, cached_input=0.10, output=10.00),
    "gpt-6-luna": Price(input=0.10, cached_input=0.01, output=0.50),
}

# Upper bound used when reserving spend before a call. OpenAI bills a high-detail image by
# 512 px tiles; a crop the pipeline sends is at most 2048 px on its long side, which stays
# well under this many input tokens. Instructions add ~1-2k tokens.
MAX_IMAGE_TOKENS = 3000
MAX_TEXT_TOKENS_PER_CHAR = 0.5  # generous: real English is ~0.25 tokens per character


class UnknownModelError(KeyError):
    pass


def price_for(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelError(
            f"no price for model {model!r}; add it to a11y_stem/models/pricing.py"
        ) from None


def cost_usd(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
    """Actual cost of one call from its usage numbers."""
    price = price_for(model)
    uncached = max(0, input_tokens - cached_input_tokens)
    return (
        uncached * price.input
        + cached_input_tokens * price.cached_input
        + output_tokens * price.output
    ) / 1_000_000


def estimate_max_cost_usd(
    model: str, *, n_images: int, text_chars: int, max_output_tokens: int
) -> float:
    """Worst-case cost of one call, for the spend reservation made before sending it."""
    price = price_for(model)
    input_tokens = 2000 + n_images * MAX_IMAGE_TOKENS + int(text_chars * MAX_TEXT_TOKENS_PER_CHAR)
    return (input_tokens * price.input + max_output_tokens * price.output) / 1_000_000
