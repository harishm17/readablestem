"""`convert_pdf` with the page witness on (`ConversionOptions.page_witness`): a real one-page
PDF, a stand-in client. Off, no witness call is made; on, the witness is asked once per maths
page, its cost and prompt version are recorded, and the equation reads its witness."""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.core.types import ConversionOptions, Kind
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline.run import convert_pdf
from test_steps_support import use_fallback_router


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Solve the system for the unknowns.", fontsize=11)
    page.insert_text((100, 190), "x + y = 5", fontsize=12)
    page.insert_text((100, 204), "y = 2 x", fontsize=12)
    return doc.tobytes()


def witness_answer(request) -> dict:
    rows = [line.split(" | ", 1) for line in (request.context_text or "").splitlines()]
    found = [{"line": i, "start": 0, "quote": t} for i, t in rows if t.startswith(("x +", "y ="))]
    return {
        "formulas": [
            {"anchors": found, "display": "block", "where": "text", "latex": "x+y=5, y=2x"}
        ]
    }


def client() -> FakeVisionClient:
    return FakeVisionClient(
        responders={"page_witness": witness_answer},
        defaults={"equation": {"is_equation": True, "latex": "x+y=5", "confidence": 0.95}},
    )


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_with_the_option_on_a_maths_page_is_asked_once_and_the_equation_reads_its_witness():
    fake = client()
    out = convert_pdf(
        pdf(), settings=settings(), client=fake, options=ConversionOptions(page_witness=True)
    )
    asked = [r for r in fake.requests if r.step == "page_witness"]
    assert len(asked) == 1 and asked[0].model == "claude-opus-5-5"
    summary = out.result.page_witness
    assert summary is not None and summary.pages == 1 and summary.calls == 1
    assert summary.verified == 1 and summary.cost_usd > 0
    assert "page_witness" in out.result.prompt_versions
    equations = [e for e in out.result.elements if e.kind is Kind.EQUATION]
    assert equations and any(e.witness_status is not None for e in equations)
    assert out.result.stats.model_calls >= summary.calls
