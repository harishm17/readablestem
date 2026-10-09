"""Per-model prices and cost arithmetic.

Prices are USD per 1M tokens, copied from https://developers.openai.com/api/docs/pricing
on 2026-10-07 (standard tier, short context). Reasoning tokens are billed as output tokens
and are already included in `output_tokens`. Update `PRICES_CHECKED` when you re-check.

The Decisions API (the second automated check, `models.decisions_client`) has a price
list of its own, `DECISIONS_PRICES`, at the end of this module.
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


# --- Decisions API (POST /v1/decisions; ADR 0005) --------------------------------------
#
# Its own price list: the endpoint bills input tokens only (no output, cache-read or
# cache-write charge), at a rate specific to /v1/decisions, and serves gpt-6-luna only.
# Checked 2026-10-08 (https://developers.openai.com/api/docs/guides/decisions and the
# pricing page). Input above DECISIONS_LONG_CONTEXT_TOKENS is billed at twice the rate.
DECISIONS_PRICES_CHECKED = "2026-10-08"
DECISIONS_PRICES: dict[str, Price] = {
    "gpt-6-luna": Price(input=0.10, cached_input=0.10, output=0.0),
}
DECISIONS_LONG_CONTEXT_TOKENS = 272_000
# The shared input is billed once per request; each answered question adds a hidden
# prompt of about 140-150 tokens plus its own text (measured 2026-10-08: +149 to +151
# tokens per one-line question, +184 per question of the figure check's length). So the
# reservation is per question: this overhead plus the question's own characters at the
# generous MAX_TEXT_TOKENS_PER_CHAR, never a flat number.
DECISIONS_QUESTION_OVERHEAD_TOKENS = 160
# Message framing around the shared input (role, part separators).
DECISIONS_INPUT_OVERHEAD_TOKENS = 50


def decisions_price_for(model: str) -> Price:
    try:
        return DECISIONS_PRICES[model]
    except KeyError:
        raise UnknownModelError(
            f"no Decisions price for model {model!r}; add it to a11y_stem/models/pricing.py"
        ) from None


def _decisions_usd(model: str, input_tokens: int) -> float:
    price = decisions_price_for(model)
    rate = price.input * (2 if input_tokens > DECISIONS_LONG_CONTEXT_TOKENS else 1)
    return max(0, input_tokens) * rate / 1_000_000


def decisions_cost_usd(model: str, input_tokens: int) -> float:
    """Actual cost of one Decisions request from its usage (input tokens only)."""
    return _decisions_usd(model, input_tokens)


def decisions_question_tokens(question_chars: int) -> int:
    """Upper bound of the tokens one answered question adds: the hidden per-question
    prompt plus its own text (instructions, name, choice values and descriptions)."""
    return DECISIONS_QUESTION_OVERHEAD_TOKENS + int(question_chars * MAX_TEXT_TOKENS_PER_CHAR)


def estimate_decisions_max_cost_usd(
    model: str, *, n_images: int, text_chars: int, question_chars: list[int]
) -> float:
    """Worst-case cost of one Decisions request, for the reservation made before sending.

    The shared input (images at detail "high", which `MAX_IMAGE_TOKENS` bounds, and the
    text parts) is counted once; every question adds `decisions_question_tokens` of its
    own length.
    """
    input_tokens = (
        DECISIONS_INPUT_OVERHEAD_TOKENS
        + n_images * MAX_IMAGE_TOKENS
        + int(text_chars * MAX_TEXT_TOKENS_PER_CHAR)
        + sum(decisions_question_tokens(chars) for chars in question_chars)
    )
    return _decisions_usd(model, input_tokens)
