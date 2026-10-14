"""Exam mode for figure alt text (T1-12): describe what is shown, never the result.

The default prompt's examples give the answer ("3 km east then 4 km north gives a 5 km
displacement"), which is right in a textbook and wrong in an exam. `ConversionOptions
.exam_mode` (a per-job choice the upload form can set) sends `figure_exam.md` instead.
The default prompt is untouched, so its version, the recorded proof runs and every job
without the flag are as before.
"""

from __future__ import annotations

import pytest

from readablestem.config import CHEAP_MODEL, MAIN_MODEL
from readablestem.core.types import ConversionOptions, Status
from readablestem.eval.gate import code_versions
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


def test_exam_mode_is_off_by_default_and_not_a_deployment_flag():
    assert ConversionOptions().exam_mode is False
    assert ConversionOptions.from_settings(SETTINGS).exam_mode is False
    assert options.current().exam_mode is False


def test_without_the_flag_the_default_prompt_is_sent_as_before():
    fake = client()
    fix = run(fake, exam=False)
    (request,) = fake.requests
    assert request.prompt_version == PROMPT_VERSION == load_prompt("figure").version
    assert request.instructions == load_prompt("figure").text
    assert [c.prompt_version for c in fix.calls] == [PROMPT_VERSION]


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


def test_a_tiny_figure_in_exam_mode_still_starts_cheap_and_uses_the_exam_prompt():
    fake = FakeVisionClient(
        responders={
            "figure": lambda req: (
                answer() | {"confidence": 0.5 if req.model == CHEAP_MODEL else 0.95}
            )
        }
    )
    element = figure_element().model_copy(update={"bbox": (72.0, 72.0, 110.0, 100.0)})
    with options.using(ConversionOptions(exam_mode=True)):
        describe(element, PNG, client=fake, settings=SETTINGS)
    assert [r.model for r in fake.requests] == [CHEAP_MODEL, MAIN_MODEL]
    assert {r.prompt_version for r in fake.requests} == {EXAM_PROMPT_VERSION}


# --------------------------------------------------------------------- the prompt text


def test_the_exam_prompt_says_to_describe_what_is_shown_and_never_the_result():
    text = load_prompt("figure_exam").text
    assert "never the result" in text
    assert "Describe what is shown" in text
    for rule in ("trend", "slope", "total", "net force or\n   displacement", "answer"):
        assert rule in text, rule
    assert "Never calculate" in text


def test_the_exam_prompt_has_none_of_the_default_answers():
    default, exam = load_prompt("figure").text, load_prompt("figure_exam").text
    for example in ("gives a 5 km displacement", "then stays at 5 km", "main trend"):
        assert example in default  # what the review found
        assert example not in exam


def test_the_exam_prompt_keeps_the_default_output_fields():
    def fields(text: str) -> str:
        return text[text.index("## Output fields") : text.index("## Alt text rules")]

    default, exam = load_prompt("figure").text, load_prompt("figure_exam").text
    assert fields(exam).replace("following the rules below", "X") == fields(default).replace(
        "following the rules below", "X"
    )
    assert exam[exam.index("## Context text") :] == default[default.index("## Context text") :]


def test_the_default_prompt_and_the_recorded_identity_are_unchanged():
    """The recorded runs name `figure-ac51f42d`; the exam prompt is not one of the versions
    the proof gate or the eval gate compare."""
    assert PROMPT_VERSION == "figure-ac51f42d"
    assert "figure_exam" not in code_versions().values()
    assert set(code_versions()) == {"equation", "figure", "judge", "table"}
