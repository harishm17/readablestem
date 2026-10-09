"""The assembled HTML of two stored results and two whole conversions, byte for byte
(golden files).

`demo` is the public demo's stored result (real model output: headings, lists, inline and
display equations, figures); `sample` is the hand-written fixture. Both golden files were
written by the pipeline 0.3.0 assembler, so this test is how the content features keep
their promise that documents without the new kinds are written exactly as before. The
`flags_off_*` cases convert two LaTeX fixtures with every content flag off and the
offline model client, so a flag-off change anywhere in the pipeline shows as a diff.

When the output changes on purpose, regenerate them and review the diff:
`UPDATE_GOLDEN=1 uv run pytest tests/test_assemble_golden.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import a11y_stem
from a11y_stem.core.types import DocumentResult, Element, Fix
from a11y_stem.pipeline.assemble import render_html

HERE = Path(__file__).parent
GOLDEN = HERE / "fixtures" / "assemble" / "golden"
DEMO_REPORT = Path(a11y_stem.__file__).parent / "web" / "static" / "demo" / "report.json"
SAMPLE = HERE / "fixtures" / "assemble" / "sample_document.json"


def demo() -> str:
    result = DocumentResult.model_validate(json.loads(DEMO_REPORT.read_text())["result"])
    return render_html(
        title=result.title, lang=result.lang, elements=result.elements, fixes=result.fixes, crops={}
    )


def sample() -> str:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return render_html(
        title=data["title"],
        lang=data["lang"],
        elements=[Element.model_validate(e) for e in data["elements"]],
        fixes=[Fix.model_validate(f) for f in data["fixes"]],
        crops={},
        source_note="Sample written for tests.",
        notice="An offline test run.",
    )


def converted(name: str) -> str:
    """A whole conversion with every content flag off and the offline model client: the
    pipeline-level promise (layout, the always-on text pass, figure images, assembly)
    that documents without the new things are unchanged. `problem_set` has equations
    and lists; `kinematics_slides` has a figure crop (MF23's smaller image)."""
    from a11y_stem.config import Settings
    from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
    from a11y_stem.core.types import ConversionOptions
    from a11y_stem.models.fake import FakeVisionClient
    from a11y_stem.pipeline.run import convert_pdf

    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    output = convert_pdf(
        (HERE / "fixtures" / "layout" / "latex" / f"{name}.pdf").read_bytes(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=FakeVisionClient(budget=budget),
        options=ConversionOptions(),  # every content flag off, whatever the environment
    )
    return output.result.html


CASES = {
    "demo": demo,
    "sample": sample,
    "flags_off_problem_set": lambda: converted("problem_set"),
    "flags_off_kinematics_slides": lambda: converted("kinematics_slides"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_output_matches_its_golden_file(name):
    html = CASES[name]()
    path = GOLDEN / f"{name}.html"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_text(html, encoding="utf-8")
    assert html == path.read_text(encoding="utf-8"), (
        f"{name}: the HTML changed; if on purpose, regenerate with UPDATE_GOLDEN=1 "
        "and review the diff"
    )
