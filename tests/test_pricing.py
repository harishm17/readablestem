"""The price table against the published figures, and against the models production names.

Every spend limit and every figure of `readablestem eval cost` is these rows times token
counts, so a wrong row is wrong everywhere and nothing else would show it: cache reads of
claude-sonnet-5-5 stood at twice the published rate for a month.
"""

from __future__ import annotations

import json
from pathlib import Path

from readablestem.models import pricing
from readablestem.ops import profile

PUBLISHED = json.loads(
    (Path(__file__).parent / "fixtures" / "models" / "anthropic_prices.json").read_text()
)


def _rates(price: pricing.Price) -> dict[str, float | None]:
    return {
        "input": price.input,
        "output": price.output,
        "cache_read": price.cached_input,
        "cache_write": price.cache_write,
    }


def test_the_claude_rows_are_the_published_prices_as_last_read():
    assert PUBLISHED["read"] == pricing.ANTHROPIC_PRICES_CHECKED
    assert set(PUBLISHED["models"]) == pricing.ANTHROPIC_MODELS
    for model, published in PUBLISHED["models"].items():
        above = published.pop("above", None)
        limit = published.pop("above_prompt_tokens", None)
        price = pricing.price_for(model)
        assert _rates(price) == published, model
        assert price.long_prompt_tokens == limit, model
        if above is not None:
            assert _rates(price.for_prompt(limit + 1)) == above, model


def test_every_model_production_names_has_a_price():
    named = {
        setting: model
        for setting, model in profile.load().settings.items()
        # "unset": the step takes the main or the cheap model, which are settings here too.
        if setting.endswith("_model") and isinstance(model, str) and model != profile.UNSET
    }
    assert {"main_model", "cheap_model", "claude_check_model"} <= set(named)
    for setting, model in named.items():
        table = pricing.DECISIONS_PRICES if setting == "decisions_model" else pricing.PRICES
        assert model in table, f"{setting.upper()}={model} has no row in models/pricing.py"
