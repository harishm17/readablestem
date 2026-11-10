"""The flag policy (`pipeline/flag_policy.py`) and its fit (`eval/flag_features.py`).

The policy only ever adds required decisions; the fit is deterministic: the same recorded items
give the same weights, threshold and numbers, with no model call and no files outside `tmp_path`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from readablestem.core.types import Element, EquationFix, Fix, Kind, Status
from readablestem.eval import flag_features as ff
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import flag_policy as fp
from readablestem.pipeline.equation_step import transcribe
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_steps_support import make_settings, use_fallback_router


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


# ------------------------------------------------------------------ features


# ------------------------------------------------------------------ apply: add only


def fix_of(status: Status, review: str, latex: str = r"\mathrm{d}x", **kw) -> Fix:
    return Fix(
        element_id="e",
        kind=Kind.EQUATION,
        status=status,
        confidence=0.95,
        equation=EquationFix(
            latex=latex,
            mathml=safe_latex_to_mathml(latex, "inline"),
            speech="d x",
            display="inline",
        ),
        review=review,  # type: ignore[arg-type]
        **kw,
    )


@pytest.fixture
def flag_everything(monkeypatch):
    monkeypatch.setattr(fp, "THRESHOLD", 0.0)


@pytest.mark.parametrize(
    "status,review",
    [
        (Status.NEEDS_REVIEW, "required"),
        (Status.FAILED, "required"),
        (Status.ABSTAINED, "required"),
        (Status.EDITED, "none"),
        (Status.REJECTED, "none"),
    ],
)
def test_nothing_else_is_touched_even_if_the_policy_would_flag_everything(
    flag_everything, status, review
):
    fix = fix_of(status, review)
    assert fp.apply(fix, text="dx") is fix


# ------------------------------------------------------------------ the step


def element() -> Element:
    return Element(
        id="e",
        kind=Kind.EQUATION,
        page=0,
        bbox=(0, 0, 10, 10),
        order=0,
        text="dx",
        display="inline",
    )


def run_step(**settings):
    client = FakeVisionClient(
        responders={
            "equation": lambda req: {"is_equation": True, "latex": "dx", "confidence": 0.95}
        }
    )
    return transcribe(element(), b"\x89PNG", client=client, settings=make_settings(**settings))


def test_the_setting_adds_the_decision_in_the_step(flag_everything):
    fix = run_step(flag_policy=True)
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert fix.equation is not None and fix.equation.latex == "dx"
    assert [c.code for c in fix.checks if not c.passed] == ["flag_policy"]


# ------------------------------------------------------------------ the fit on a fixture


def pair(gold_id: str, latex: str, *, wrong: bool, review: str = "none", **kw) -> dict:
    return {
        "gold_id": gold_id,
        "pred_latex": latex,
        "pred_mathml": safe_latex_to_mathml(latex, "inline"),
        "pred_text": kw.pop("text", "ab"),
        "pred_display": "inline",
        "gold_display": "inline",
        "exact_lenient": not wrong,
        "exact_symbols": not wrong,
        "review": review,
        "status": "needs_review" if review == "required" else "accepted",
        "escalation": kw.pop("escalation", None),
        "tier_reasons": [],
    }


def fixture_items() -> list[dict]:
    """12 papers; a reading with `\\mathrm` is wrong (silent) 3 times in 4, a plain one 1 in 20."""
    items = []
    for p in range(12):
        pairs = []
        for i in range(20):
            upright = i % 5 == 0
            latex = r"\mathrm{ab}" if upright else "ab"
            wrong = (i % 4 != 0) if upright else (i == 7 and p % 2 == 0)
            pairs.append(pair(f"p{p}/{i}", latex, wrong=wrong))
        pairs.append(pair(f"p{p}/r", "ab", wrong=True, review="required"))
        items.append(
            {
                "id": f"p{p}",
                "n_gold": 24,
                "pdf_pages": 2,
                "review": {"required_decisions": 3},
                "pairs": pairs,
                "prompt_versions": {"equation": "equation-x+equation_line-y"},
            }
        )
    return items


@pytest.fixture
def spec(tmp_path: Path):
    path = tmp_path / "items.jsonl"
    path.write_text("\n".join(json.dumps(i) for i in fixture_items()), encoding="utf-8")
    return ff.SplitSpec("fixture", path, "", "fit")
