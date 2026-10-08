"""Live smoke test against the real OpenAI API (costs money; skipped without a key).

Run with: uv run pytest -m live_model tests/test_models_live.py

One tiny rendered equation goes to both models through OpenAIVisionClient. The job budget
is capped at $0.05, so the spend guard itself refuses to go over it. Expected spend is well
under one cent: low image detail, a few hundred input tokens, short output.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from pydantic import BaseModel

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL, Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.openai_client import OpenAIVisionClient

pytestmark = pytest.mark.live_model

PROMPT = Path(__file__).parent / "fixtures" / "models" / "live_equation.md"
MAX_USD = 0.05


class LiveLatex(BaseModel):
    latex: str
    confidence: float


def render_equation_png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=170, height=44)
    page.insert_text((10, 30), "v = d / t", fontsize=22)
    return page.get_pixmap(dpi=144).tobytes("png")


def live_settings() -> Settings:
    settings = Settings()  # OPENAI_API_KEY from the environment or ./.env
    key = settings.openai_api_key
    if key is None or not key.get_secret_value():
        pytest.skip("OPENAI_API_KEY is not set")
    return settings


def test_both_models_transcribe_a_rendered_equation():
    settings = live_settings()
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=MAX_USD), max_usd=MAX_USD, max_calls=2)
    client = OpenAIVisionClient(settings, budget)
    png = render_equation_png()

    for model in (MAIN_MODEL, CHEAP_MODEL):
        request = VisionRequest(
            step="equation",
            model=model,
            prompt_version="live-equation-test",
            instructions=PROMPT.read_text(encoding="utf-8"),
            images=[png],
            max_output_tokens=400,
            image_detail="low",
        )
        parsed, call = client.run(request, LiveLatex)
        assert call.outcome == "ok", f"{model}: {call.outcome}"
        # A smoke check only: at low detail a model may read the "v" as "V" (seen 2026-10-07).
        assert parsed is not None and "v" in parsed.latex.lower()
        assert call.cost_usd > 0
        assert call.input_tokens > 0 and call.output_tokens > 0

    assert 0 < budget.spent_usd < MAX_USD
