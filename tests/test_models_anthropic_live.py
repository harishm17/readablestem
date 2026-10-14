"""Live smoke test against the real Anthropic API (costs money; skipped without a key).

Run with: uv run pytest -m live_model tests/test_models_anthropic_live.py

One tiny rendered equation goes to claude-haiku-5-5 through AnthropicVisionClient, with the
equation step's real output schema and prompt, so the request the app sends (images,
cached instructions, structured output, effort) is accepted by the API. The job budget is
capped at $0.05, so the spend guard itself refuses to go over it; the expected spend is a
fraction of a cent. ANTHROPIC_API_KEY comes from the environment or ./.env;
ANTHROPIC_WORKSPACE_ID too, when the key is not scoped to a workspace.
"""

from __future__ import annotations

import pymupdf
import pytest

from a11y_stem.config import Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.models.anthropic_client import AnthropicVisionClient
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.prompts import load_prompt
from a11y_stem.pipeline.equation_step import EquationOutput

pytestmark = pytest.mark.live_model

MAX_USD = 0.05
MODEL = "claude-haiku-5-5"


def render_equation_png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=170, height=44)
    page.insert_text((10, 30), "v = d / t", fontsize=22)
    return page.get_pixmap(dpi=144).tobytes("png")


def live_settings() -> Settings:
    settings = Settings(model_provider="anthropic")  # type: ignore[call-arg]
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():
        pytest.skip("ANTHROPIC_API_KEY is not set")
    return settings


def test_haiku_transcribes_a_rendered_equation():
    settings = live_settings()
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=MAX_USD), max_usd=MAX_USD, max_calls=2)
    client = AnthropicVisionClient(settings, budget)
    prompt = load_prompt("equation")
    request = VisionRequest(
        step="equation",
        model=MODEL,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[render_equation_png()],
        max_output_tokens=400,
    )
    parsed, call = client.run(request, EquationOutput)
    print(
        f"outcome={call.outcome} model={call.model} input={call.input_tokens} "
        f"cache_read={call.cached_input_tokens} cache_write={call.cache_creation_input_tokens} "
        f"output={call.output_tokens} thinking={call.reasoning_tokens} "
        f"cost_usd={call.cost_usd:.6f} latency_ms={call.latency_ms}"
    )
    assert call.outcome == "ok", call.outcome
    # A smoke check only: the model may write the "v" in another case or style.
    assert parsed is not None and parsed.is_equation and "v" in parsed.latex.lower()
    assert call.cost_usd > 0 and call.input_tokens > 0 and call.output_tokens > 0
    assert 0 < budget.spent_usd < MAX_USD
