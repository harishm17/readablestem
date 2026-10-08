"""Live checks of both steps against the real OpenAI API (costs money; opt-in).

Run with: OPENAI_API_KEY=... uv run pytest -m live_model tests/test_steps_live.py -s

Each test reserves spend through a JobBudget capped at $0.05 (two tests, under $0.10).
Inputs are synthetic: a gold OpenStax equation rendered from its MathML in Chromium, and
a bar chart drawn with PyMuPDF. No private documents.

WP1's `OpenAIVisionClient` and `router.call_with_fallback` are stubs on this branch, so
the test uses `LiveClient`, a minimal Responses API client with the same contract, and
the fallback router from test_steps_support. After WP1 merges, swap in the real ones.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import pydantic
import pytest

from a11y_stem.config import Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.core.types import ModelCall, Status
from a11y_stem.eval.equations import score_mathml
from a11y_stem.eval.gold_scoring import iter_gold_records
from a11y_stem.models.base import T, VisionRequest, fence_untrusted
from a11y_stem.models.pricing import cost_usd, estimate_max_cost_usd
from a11y_stem.pipeline.equation_step import transcribe
from a11y_stem.pipeline.figure_step import describe
from test_steps_support import (
    equation_element,
    figure_element,
    make_settings,
    use_fallback_router,
)

pytestmark = pytest.mark.live_model

REPO = Path(__file__).resolve().parents[1]
GOLD_ID = "openstax/physics/2-2-speed-and-velocity/eq-005"  # 3-row aligned equation
MAX_USD_PER_TEST = 0.05


@pytest.fixture
def settings() -> Settings:
    s = make_settings()  # still reads OPENAI_API_KEY from the environment
    if s.openai_api_key is None or not s.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY is not set")
    return s


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


class LiveClient:
    """Minimal `VisionClient` over the Responses API with spend reservation."""

    def __init__(self, settings: Settings, budget: JobBudget) -> None:
        import openai

        self._openai = openai
        self._sdk = openai.OpenAI(
            api_key=settings.require_openai_key(), timeout=settings.model_timeout_s, max_retries=1
        )
        self.budget = budget

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        estimate = estimate_max_cost_usd(
            request.model,
            n_images=len(request.images),
            text_chars=len(request.context_text or ""),
            max_output_tokens=request.max_output_tokens,
        )
        reservation = self.budget.reserve(estimate)
        content: list[dict] = [
            {
                "type": "input_image",
                "image_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                "detail": request.image_detail,
            }
            for png in request.images
        ]
        if request.context_text:
            content.append({"type": "input_text", "text": fence_untrusted(request.context_text)})
        call = ModelCall(
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            is_fallback=is_fallback,
        )
        start = time.perf_counter()
        try:
            response = self._sdk.responses.parse(
                model=request.model,
                instructions=request.instructions,
                input=[{"role": "user", "content": content}],  # type: ignore[list-item]
                text_format=schema,
                max_output_tokens=request.max_output_tokens,
                store=False,
            )
        except self._openai.APITimeoutError:
            self.budget.settle(reservation, 0.0)
            call.outcome = "timeout"
            return None, call
        except self._openai.APIError:
            self.budget.settle(reservation, 0.0)
            call.outcome = "api_error"
            return None, call
        except (pydantic.ValidationError, self._openai.LengthFinishReasonError):
            self.budget.settle(reservation, estimate)  # billed, but unusable
            call.outcome = "invalid_output"
            return None, call
        call.latency_ms = int((time.perf_counter() - start) * 1000)
        usage = response.usage
        if usage is not None:
            call.input_tokens = usage.input_tokens
            call.cached_input_tokens = usage.input_tokens_details.cached_tokens
            call.output_tokens = usage.output_tokens
            call.reasoning_tokens = usage.output_tokens_details.reasoning_tokens
            call.cost_usd = cost_usd(
                request.model, call.input_tokens, call.cached_input_tokens, call.output_tokens
            )
        self.budget.settle(reservation, call.cost_usd)
        parsed = response.output_parsed
        if parsed is None:
            call.outcome = "refused"
        return parsed, call


def _budget() -> JobBudget:
    guard = InMemorySpendGuard(cap_usd=MAX_USD_PER_TEST)
    return JobBudget(guard=guard, max_usd=MAX_USD_PER_TEST, max_calls=4)


def _render_mathml_png(mathml: str) -> bytes:
    sync_api = pytest.importorskip("playwright.sync_api")
    html = (
        '<!doctype html><html><body style="margin:0;background:#fff">'
        f'<div id="eq" style="display:inline-block;padding:12px;font-size:32px">{mathml}</div>'
        "</body></html>"
    )
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(device_scale_factor=2)
            page.set_content(html)
            png = page.locator("#eq").screenshot()
            browser.close()
    except sync_api.Error as exc:  # Chromium not installed
        pytest.skip(f"Playwright Chromium unavailable ({type(exc).__name__})")
    return png


def _bar_chart_png() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=360, height=260)
    page.insert_text((90, 30), "Average speed by trip", fontsize=14)
    x0, y0 = 60, 220
    page.draw_line((x0, y0), (340, y0))
    page.draw_line((x0, y0), (x0, 50))
    for i, (label, height) in enumerate([("Trip A", 40), ("Trip B", 90), ("Trip C", 150)]):
        left = x0 + 25 + i * 90
        rect = pymupdf.Rect(left, y0 - height, left + 50, y0)
        page.draw_rect(rect, color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
        page.insert_text((left + 5, y0 + 16), label, fontsize=10)
    for value in (0, 50, 100, 150):
        page.insert_text((x0 - 25, y0 - value + 4), str(value), fontsize=9)
    page.insert_text((12, 140), "km/h", fontsize=10)
    return page.get_pixmap(dpi=150).tobytes("png")


def test_live_equation_transcription(settings):
    record = next(
        r for r in iter_gold_records(REPO / "data" / "gold", kind="equation") if r["id"] == GOLD_ID
    )
    png = _render_mathml_png(record["mathml"])
    element = equation_element(
        display=record["display"],
        text="v avg = distance time = 150 km 3.2 h = 47 km/h.",  # what a PDF text layer holds
    )
    client = LiveClient(settings, _budget())
    fix = transcribe(element, png, client=client, settings=settings)
    print(f"\nequation: {fix.status} conf={fix.confidence} cost=${fix.cost_usd:.4f}")
    print([(c.model, c.outcome, c.input_tokens, c.output_tokens) for c in fix.calls])
    assert fix.status in {Status.ACCEPTED, Status.NEEDS_REVIEW}, (fix.reason, fix.failed_checks)
    assert fix.equation is not None
    print(fix.equation.latex)
    print(fix.equation.speech)
    score = score_mathml(fix.equation.mathml, record["mathml"])
    assert score.teds >= 0.8, score
    assert fix.cost_usd < MAX_USD_PER_TEST


def test_live_figure_alt_text(settings):
    element = figure_element(caption="Figure 1 Average speed on three trips.")
    client = LiveClient(settings, _budget())
    fix = describe(element, _bar_chart_png(), client=client, settings=settings)
    print(f"\nfigure: {fix.status} conf={fix.confidence} cost=${fix.cost_usd:.4f}")
    print([(c.model, c.outcome, c.input_tokens, c.output_tokens) for c in fix.calls])
    assert fix.status in {Status.ACCEPTED, Status.NEEDS_REVIEW}, (fix.reason, fix.failed_checks)
    assert fix.figure is not None
    print(fix.figure.alt)
    assert fix.figure.figure_type == "chart" and not fix.figure.decorative
    assert "bar" in fix.figure.alt.lower()
    assert 0 < len(fix.figure.alt) <= 300
    assert fix.cost_usd < MAX_USD_PER_TEST
