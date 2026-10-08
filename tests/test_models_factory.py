"""make_vision_client: offline defaults, real client without a key, light package import."""

from __future__ import annotations

import subprocess
import sys
from typing import Literal

from pydantic import BaseModel

from a11y_stem.config import MAIN_MODEL, Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.models import (
    OFFLINE_DEFAULTS,
    FakeVisionClient,
    VisionRequest,
    call_with_fallback,
    make_vision_client,
)
from a11y_stem.models.openai_client import OpenAIVisionClient

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]  (no API key)


class EquationOut(BaseModel):
    is_equation: bool
    latex: str
    confidence: float


class FigureOut(BaseModel):
    figure_type: Literal["chart", "diagram", "photo", "illustration", "table_image", "other"]
    decorative: bool
    alt: str
    confidence: float


def budget() -> JobBudget:
    return JobBudget(guard=InMemorySpendGuard(cap_usd=1), max_usd=1, max_calls=10)


def request(step: Literal["equation", "figure"]) -> VisionRequest:
    return VisionRequest(
        step=step,
        model=MAIN_MODEL,
        prompt_version=f"{step}-test",
        instructions="i",
        images=[b"\x89PNG\r\n\x1a\nimg"],
    )


def test_fake_client_answers_equation_and_figure_offline_and_charges_the_budget():
    job = budget()
    client = make_vision_client(SETTINGS, job, fake=True)
    assert isinstance(client, FakeVisionClient)

    equation, call = client.run(request("equation"), EquationOut)
    assert equation == EquationOut(is_equation=True, latex="x", confidence=0.5)
    assert call.outcome == "ok"

    figure, _ = client.run(request("figure"), FigureOut)
    assert figure is not None
    assert (figure.figure_type, figure.decorative) == ("other", False)
    assert figure.alt == "Figure (offline placeholder)"

    assert job.calls == 2 and job.spent_usd > 0  # spend limits apply offline too


def test_offline_answers_always_need_review():
    s = SETTINGS
    for payload in OFFLINE_DEFAULTS.values():
        assert payload is not None
        assert s.abstain_confidence <= payload["confidence"] < s.auto_accept_confidence


def test_each_fake_client_gets_its_own_copy_of_the_defaults():
    first = make_vision_client(SETTINGS, budget(), fake=True)
    assert isinstance(first, FakeVisionClient)
    first.defaults["equation"] = None  # type: ignore[index]
    assert OFFLINE_DEFAULTS["equation"] is not None
    second = make_vision_client(SETTINGS, budget(), fake=True)
    parsed, _ = second.run(request("equation"), EquationOut)
    assert parsed is not None


def test_fake_client_works_through_the_router():
    client = make_vision_client(SETTINGS, budget(), fake=True)
    parsed, calls = call_with_fallback(client, request("equation"), EquationOut, settings=SETTINGS)
    assert parsed is not None and [c.outcome for c in calls] == ["ok"]


def test_real_client_is_built_without_needing_a_key_yet():
    client = make_vision_client(SETTINGS, budget())
    assert isinstance(client, OpenAIVisionClient)


def test_importing_the_package_does_not_import_the_openai_sdk():
    code = "import sys, a11y_stem.models; print('openai' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
