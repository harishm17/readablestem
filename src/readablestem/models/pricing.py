"""Per-model prices and cost arithmetic.

Prices are USD per 1M tokens. The OpenAI rows are copied from
https://developers.openai.com/api/docs/pricing on 2026-10-07 (standard tier, short
context). Reasoning tokens are billed as output tokens and are already included in
`output_tokens`. Update `PRICES_CHECKED` when you re-check.

The Anthropic rows (`claude-*`) are from the Claude API model and pricing table
(`ANTHROPIC_PRICES_SOURCE`; first-party API, standard tier, no batch discount), read again
on `ANTHROPIC_PRICES_CHECKED`; see the block above them for the cache rules. Thinking tokens
are billed as output tokens and are included in `output_tokens`. A copy of the published
figures as read that day is `tests/fixtures/models/anthropic_prices.json`, and
`tests/test_pricing.py` holds the rows to it: change both from a new reading, never one.

The Decisions API (the second automated check, `models.decisions_client`) has a price
list of its own, `DECISIONS_PRICES`, at the end of this module.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass

PRICES_CHECKED = "2026-10-07"
ANTHROPIC_PRICES_CHECKED = "2026-11-11"
ANTHROPIC_PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"

# Upper bound used when reserving spend before a call. OpenAI bills a high-detail image by
# 512 px tiles; a crop the pipeline sends is at most 2048 px on its long side, which stays
# well under this many input tokens. Instructions add ~1-2k tokens.
MAX_IMAGE_TOKENS = 3000
MAX_TEXT_TOKENS_PER_CHAR = 0.5  # generous: real English is ~0.25 tokens per character
# The instructions, as an input-token allowance in the reservation.
BASE_INPUT_TOKENS = 2000


@dataclass(frozen=True)
class Price:
    input: float  # per 1M uncached input tokens
    cached_input: float  # per 1M cached input tokens (a cache read)
    output: float  # per 1M output tokens (reasoning or thinking included)
    # Per 1M tokens written to the prompt cache. None: the provider has no separate charge
    # (OpenAI), so a write is an ordinary input token.
    cache_write: float | None = None
    # A second rate card for long prompts: `long` applies when the prompt (cached or not)
    # has more than `long_prompt_tokens` tokens. None: one rate card.
    long_prompt_tokens: int | None = None
    long: Price | None = None
    # Rates of the dearest model a refusal fallback can route to (the server-side
    # `fallbacks`): a reservation is priced at the larger of the two, so it stays an upper
    # bound when the fallback model serves the request.
    ceiling: Price | None = None
    # What one image and the instructions can add to the prompt, and the thinking allowance
    # added to the output limit, in the reservation (`estimate_max_cost_usd`).
    max_image_tokens: int = MAX_IMAGE_TOKENS
    base_input_tokens: int = BASE_INPUT_TOKENS
    thinking_tokens: int = 0
    # Pixels one image token covers (Claude: width x height / 750 after the image is scaled
    # to at most `IMAGE_MAX_EDGE_PX` on its long edge). With this set, the reservation
    # prices an image by its real size (`image_tokens_upper_bound`) instead of
    # `max_image_tokens`, which stays the ceiling. None: every image is priced at the maximum.
    pixels_per_token: int | None = None

    def for_prompt(self, prompt_tokens: int) -> Price:
        """The rate card that applies to a prompt of this many tokens."""
        if (
            self.long is not None
            and self.long_prompt_tokens is not None
            and prompt_tokens > self.long_prompt_tokens
        ):
            return self.long
        return self


PRICES: dict[str, Price] = {
    "gpt-6.1-sol": Price(input=2.00, cached_input=0.10, output=10.00),
    "gpt-6-luna": Price(input=0.10, cached_input=0.01, output=0.50),
}

# --- Anthropic Claude API ---------------------------------------------------------------
#
# From the model table of the Claude API reference and its prompt-caching and model notes,
# as read on `ANTHROPIC_PRICES_CHECKED`. Cache reads are 0.1x the input rate on Haiku 5.5,
# 0.05x on Opus 5.5 ($0.20) and on Sonnet 5.5 ($0.10), and 0.025x on Fable 5.2 ($0.20). A
# cache write costs 1.25x the input rate for the 5-minute lifetime (the only one the client
# asks for; 2x for 1 hour). Haiku 5.5 has two rate cards, chosen by prompt length: $0.10 /
# $0.50 up to 100K tokens, $0.50 / $2.50 beyond, with the same multipliers.
#
# Sonnet 5.5's cache reads were $0.20 here until 2026-11-11, twice the published rate, so
# the cached part of a second check was overstated; nothing was understated.
#
# claude-fable-5-2 has a row so that a ladder with it can be costed from a recording. No
# step is set to it, and the client's requests have not been tried against it: by the API
# reference it always thinks, refuses a forced tool choice, and takes no server-side
# fallback in a batch.
#
# The reservation's upper bound for these models (`estimate_max_cost_usd`):
# - images: a high-resolution image is at most about 4784 tokens (2576 px on the long
#   edge), and the newer tokenizers count up to 1.35x as many tokens as older ones, so an
#   image is allowed 6500 here;
# - the instructions and the JSON schema sent with them: 3500 tokens;
# - thinking: it counts toward `max_tokens`, so the client sends the step's output limit
#   plus THINKING_ALLOWANCE_TOKENS and the reservation prices exactly that many output
#   tokens;
# - input is priced at the cache-write rate where that is dearer than the input rate;
# - claude-opus-5-5 can be served by its refusal fallback (claude-opus-5 or
#   claude-opus-4-8, both $5 / $25, cache reads $0.50, writes $6.25): the reservation uses
#   the dearer of the two models' rates.
ANTHROPIC_IMAGE_TOKENS = 6500
ANTHROPIC_BASE_INPUT_TOKENS = 3500
# Thinking is part of `max_tokens`, so the request asks for the step's limit plus this much
# and the reservation prices all of it at the output rate. It is a ceiling on thinking, not
# a typical size: on the 2,405 calls of the held-out run (effort medium on claude-opus-5-5,
# low on claude-haiku-5-5) the largest answer was 707 output tokens, 671 of them thinking,
# and none ran into the limit. It was 8192 at first, which alone made one claude-opus-5-5
# reservation about $0.2 and a one-page job's $0.30 cap unable to hold a single call.
THINKING_ALLOWANCE_TOKENS = 2048
# Image sizing, measured with the free token-counting endpoint on 2026-10-14
# (claude-opus-5-5): a PNG costs about width x height / 750 tokens (+4..7), the long edge is
# scaled down to 2576 px first, and the cost stops at about 4764 tokens. The bound adds the
# 1.35 allowance for the newer tokenizers that `ANTHROPIC_IMAGE_TOKENS` already includes.
IMAGE_MAX_EDGE_PX = 2576
IMAGE_PIXELS_PER_TOKEN = 750
IMAGE_TOKEN_CAP = 4784
IMAGE_TOKEN_MARGIN = 1.35
HAIKU_LONG_PROMPT_TOKENS = 100_000

_HAIKU_LONG = Price(
    input=0.50,
    cached_input=0.05,
    output=2.50,
    cache_write=0.625,
    max_image_tokens=ANTHROPIC_IMAGE_TOKENS,
    base_input_tokens=ANTHROPIC_BASE_INPUT_TOKENS,
    thinking_tokens=THINKING_ALLOWANCE_TOKENS,
    pixels_per_token=IMAGE_PIXELS_PER_TOKEN,
)
_OPUS_FALLBACK_CEILING = Price(input=5.00, cached_input=0.50, output=25.00, cache_write=6.25)

PRICES.update(
    {
        "claude-opus-5-5": Price(
            input=4.00,
            cached_input=0.20,
            output=20.00,
            cache_write=5.00,
            ceiling=_OPUS_FALLBACK_CEILING,
            max_image_tokens=ANTHROPIC_IMAGE_TOKENS,
            base_input_tokens=ANTHROPIC_BASE_INPUT_TOKENS,
            thinking_tokens=THINKING_ALLOWANCE_TOKENS,
            pixels_per_token=IMAGE_PIXELS_PER_TOKEN,
        ),
        "claude-fable-5-2": Price(
            input=8.00,
            cached_input=0.20,
            output=40.00,
            cache_write=10.00,
            max_image_tokens=ANTHROPIC_IMAGE_TOKENS,
            base_input_tokens=ANTHROPIC_BASE_INPUT_TOKENS,
            thinking_tokens=THINKING_ALLOWANCE_TOKENS,
            pixels_per_token=IMAGE_PIXELS_PER_TOKEN,
        ),
        "claude-sonnet-5-5": Price(
            input=2.00,
            cached_input=0.10,
            output=10.00,
            cache_write=2.50,
            max_image_tokens=ANTHROPIC_IMAGE_TOKENS,
            base_input_tokens=ANTHROPIC_BASE_INPUT_TOKENS,
            thinking_tokens=THINKING_ALLOWANCE_TOKENS,
            pixels_per_token=IMAGE_PIXELS_PER_TOKEN,
        ),
        "claude-haiku-5-5": Price(
            input=0.10,
            cached_input=0.01,
            output=0.50,
            cache_write=0.125,
            long_prompt_tokens=HAIKU_LONG_PROMPT_TOKENS,
            long=_HAIKU_LONG,
            max_image_tokens=ANTHROPIC_IMAGE_TOKENS,
            base_input_tokens=ANTHROPIC_BASE_INPUT_TOKENS,
            thinking_tokens=THINKING_ALLOWANCE_TOKENS,
            pixels_per_token=IMAGE_PIXELS_PER_TOKEN,
        ),
    }
)
# The Message Batches API bills every token at half the standard rate (re-read 2026-11-11).
BATCH_DISCOUNT = 0.5
ANTHROPIC_MODELS: frozenset[str] = frozenset(m for m in PRICES if m.startswith("claude-"))
# Priced so that a recording can be costed with them, and not to be set as a role's model:
# the client's requests have not been tried against them (see the block above the rows).
COSTED_ONLY: frozenset[str] = frozenset({"claude-fable-5-2"})


class UnknownModelError(KeyError):
    pass


def price_for(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelError(
            f"no price for model {model!r}; add it to readablestem/models/pricing.py"
        ) from None


def cost_usd(
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
) -> float:
    """Actual cost of one call from its usage numbers.

    `input_tokens` is the whole prompt: the cached part (`cached_input_tokens`, read from
    the cache) and the part written to the cache (`cache_write_tokens`) are inside it.
    """
    price = price_for(model).for_prompt(input_tokens)
    uncached = max(0, input_tokens - cached_input_tokens - cache_write_tokens)
    write_rate = price.input if price.cache_write is None else price.cache_write
    return (
        uncached * price.input
        + cached_input_tokens * price.cached_input
        + output_tokens * price.output
        + cache_write_tokens * write_rate
    ) / 1_000_000


def ceiling_cost_usd(
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
) -> float:
    """Cost of a usage block at the dearer of `model`'s rates and its fallback ceiling."""
    own = cost_usd(model, input_tokens, cached_input_tokens, output_tokens, cache_write_tokens)
    ceiling = price_for(model).ceiling
    if ceiling is None:
        return own
    uncached = max(0, input_tokens - cached_input_tokens - cache_write_tokens)
    dearer = (
        uncached * ceiling.input
        + cached_input_tokens * ceiling.cached_input
        + output_tokens * ceiling.output
        + cache_write_tokens * (ceiling.cache_write or ceiling.input)
    ) / 1_000_000
    return max(own, dearer)


def output_token_limit(model: str, max_output_tokens: int) -> int:
    """The `max_tokens` to send for a step's output limit: for a model that thinks before it
    answers, thinking counts toward the limit, so it gets an allowance on top."""
    return max_output_tokens + price_for(model).thinking_tokens


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's header, or None if it is not a readable PNG."""
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if width > 0 and height > 0 else None


def image_tokens_upper_bound(model: str, image: bytes | None = None) -> int:
    """Most input tokens one image can cost on `model`: its measured size when the model
    prices by pixels and the PNG header is readable, else the model's flat maximum."""
    price = price_for(model)
    size = png_size(image) if image is not None else None
    if price.pixels_per_token is None or size is None:
        return price.max_image_tokens
    width, height = size
    scale = min(1.0, IMAGE_MAX_EDGE_PX / max(width, height))
    pixels = math.ceil(width * scale) * math.ceil(height * scale)
    tokens = min(math.ceil(pixels / price.pixels_per_token) + 16, IMAGE_TOKEN_CAP)
    return min(price.max_image_tokens, math.ceil(tokens * IMAGE_TOKEN_MARGIN))


def estimate_max_cost_usd(
    model: str,
    *,
    n_images: int,
    text_chars: int,
    max_output_tokens: int,
    images: Sequence[bytes] | None = None,
) -> float:
    """Worst-case cost of one call, for the spend reservation made before sending it.

    `images` (the PNG bytes, when the caller has them) lets a model that prices images by
    size reserve for the images actually sent; without them each of `n_images` is priced
    at the model's maximum."""
    price = price_for(model)
    if images is not None:
        image_tokens = sum(image_tokens_upper_bound(model, image) for image in images)
    else:
        image_tokens = n_images * price.max_image_tokens
    input_tokens = (
        price.base_input_tokens + image_tokens + int(text_chars * MAX_TEXT_TOKENS_PER_CHAR)
    )
    output_tokens = output_token_limit(model, max_output_tokens)
    worst = 0.0
    for card in (price.for_prompt(input_tokens), price.ceiling):
        if card is None:
            continue
        # A prompt token costs the cache-write rate at most (dearer than a plain one).
        input_rate = max(card.input, card.cache_write or 0.0)
        worst = max(worst, input_tokens * input_rate + output_tokens * card.output)
    return worst / 1_000_000


# --- Reservation sizes, from measured calls ---------------------------------------------
#
# The worst case above is what a call could cost if it filled its output limit with
# thinking, with the largest image. A call of the pipeline costs a fraction of it: on the
# replay below the dearest call of each step was 0.18 to 0.6 of its worst case, and an Opus
# equation read held about $0.099 and cost $0.0025 at the median. Holds that are many times
# the cost starve a job that is far under its cap: eight reads in flight hold $0.79, more than
# the $0.33 that was left of a $0.60 run cap with $0.27 spent.
# A reservation is therefore the worst case of the request, or the measured size of its
# step on its model when that is less.
#
# The size is twice the dearest call of the step on the model in the replay of the corpus's
# recorded answers (`readablestem eval cost`; every request a conversion sent, 1,376 calls of
# 39 uploads, under the production profile, dollars as recorded), rounded up. Its number of
# calls is in the comment on its row. One equation call of the recordings is left out of
# the dearest: its $0.1039 is a send charged at its reservation after no usage came back
# plus the retry's $0.0038 (`ModelCall.billed_unseen_usd`), not a cost. A step that is not
# in the table has no measured call (the page steps, the judge, the checks) and holds its
# worst case. A call that costs more than its reservation is settled at what it cost and
# logged (`model_call_over_reservation`): widen its row from a new replay.
RESERVATIONS_MEASURED = "2026-11-18"
RESERVATIONS: dict[tuple[str, str], float] = {
    ("equation", "claude-haiku-5-5"): 0.0015,  # dearest of 685: $0.0007
    ("equation", "claude-opus-5-5"): 0.046,  # 440: $0.023
    ("figure", "claude-haiku-5-5"): 0.0015,  # 41: $0.0007
    ("figure", "claude-opus-5-5"): 0.065,  # 156: $0.0325
    ("image_page", "claude-opus-5-5"): 0.142,  # 14: $0.0707
    ("table", "claude-haiku-5-5"): 0.012,  # 27: $0.0059
    ("table", "claude-opus-5-5"): 0.12,  # 12: $0.060
}


def reservation_usd(step: str, model: str, worst_case_usd: float) -> float:
    """What a call of `step` on `model` holds before it is sent: its worst case, or the
    measured size of the step on that model (`RESERVATIONS`) when that is less."""
    measured = RESERVATIONS.get((step, model))
    return worst_case_usd if measured is None else min(worst_case_usd, measured)


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
            f"no Decisions price for model {model!r}; add it to readablestem/models/pricing.py"
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
