"""Exam mode for figure alt text (T1-12): describe what is shown, never the result.

The default prompt's examples give the answer ("3 km east then 4 km north gives a 5 km
displacement"), which is right in a textbook and wrong in an exam. `ConversionOptions
.exam_mode` (a per-job choice the upload form can set) sends `figure_exam.md` instead.
The default prompt is untouched, so its version, the recorded proof runs and every job
without the flag are as before.
"""

from __future__ import annotations

import pytest

from readablestem.core.types import ConversionOptions, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.models.prompts import load_prompt
from readablestem.pipeline import figure_step, options
from readablestem.pipeline.figure_step import (
    EXAM_PROMPT_NAME,
    EXAM_PROMPT_VERSION,
    LONG_PROMPT_VERSION,
    PROMPT_VERSION,
    describe,
)
from test_steps_support import figure_element, make_settings, use_fallback_router

PNG = b"\x89PNG figure crop"
SETTINGS = make_settings()
EXAM_ALT = "Line graph of position in metres versus time in seconds; one straight line segment."


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def answer(alt: str = EXAM_ALT) -> dict:
    return {"figure_type": "chart", "decorative": False, "alt": alt, "confidence": 0.93}


def client() -> FakeVisionClient:
    return FakeVisionClient(responders={"figure": lambda req: answer()})


def run(fake: FakeVisionClient, *, exam: bool, long: bool = False):
    with options.using(ConversionOptions(exam_mode=exam, long_descriptions=long)):
        return describe(figure_element(), PNG, client=fake, settings=SETTINGS)


def test_exam_mode_sends_the_exam_prompt_and_records_its_version():
    fake = client()
    fix = run(fake, exam=True)
    (request,) = fake.requests
    assert request.prompt_version == EXAM_PROMPT_VERSION
    assert EXAM_PROMPT_VERSION.startswith(f"{EXAM_PROMPT_NAME}-")
    assert EXAM_PROMPT_VERSION != PROMPT_VERSION
    assert request.instructions == load_prompt("figure_exam").text
    assert fix.status is Status.ACCEPTED and fix.figure is not None
    assert fix.figure.alt == EXAM_ALT and fix.figure.long_description is None
    assert [c.prompt_version for c in fix.calls] == [EXAM_PROMPT_VERSION]


def test_exam_mode_wins_over_long_descriptions():
    """A trend or a table of values is a result: no long description, no data table."""
    fake = client()
    fix = run(fake, exam=True, long=True)
    (request,) = fake.requests
    assert request.prompt_version == EXAM_PROMPT_VERSION != LONG_PROMPT_VERSION
    assert fix.figure is not None
    assert fix.figure.long_description is None and fix.figure.data_table is None
    assert not figure_step.described_with_long_prompt(fix)  # so no "description missing" notice
    assert not figure_step.long_description_missing(fix)


# --------------------------------------------------------------------- the prompt text
