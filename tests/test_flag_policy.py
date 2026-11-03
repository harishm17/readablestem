"""The flag policy (`pipeline/flag_policy.py`) and its fit (`eval/flag_features.py`).

The policy only ever adds required decisions; the fit is deterministic: the same recorded items
give the same weights, threshold and numbers, with no model call and no files outside `tmp_path`.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from readablestem.core.types import Check, Element, EquationFix, Fix, Kind, Status
from readablestem.eval import flag_features as ff
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import flag_policy as fp
from readablestem.pipeline.equation_step import transcribe
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_steps_support import make_settings, use_fallback_router


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def feats(latex: str, text: str | None = "", **kw):
    base = {"display": "inline", "escalation": None, "codes": []}
    return fp.features(
        **(base | kw), text=text, latex=latex, mathml=safe_latex_to_mathml(latex, "inline")
    )


# ------------------------------------------------------------------ features


def test_features_are_content_free_numbers_in_a_fixed_order():
    f = feats(r"\mathrm{d}x_1^2", text="dx 1 2", escalation="changed")
    assert tuple(f) == fp.FEATURES
    assert all(isinstance(v, float) for v in f.values())
    assert f["esc_changed"] == 1.0 and f["esc_same"] == 0.0
    assert f["upright"] == 1.0 and f["script"] == 1.0 and f["frac"] == 0.0
    assert f["no_text"] == 0.0 and f["single"] == 0.0


def test_the_notation_classes_are_read_from_the_latex():
    f = feats(r"\boldsymbol{\alpha}", text="\u03b1")
    assert f["bold"] == 1.0 and f["greek"] == 1.0 and f["single"] == 1.0
    assert feats(r"\mathcal{L}", text="L")["calligraphic"] == 1.0
    assert feats(r"\sqrt{\frac{a}{b}}", text="")["frac"] == 1.0
    assert feats(r"\sum_i x_i", text="")["bigop"] == 1.0


def test_extension_and_missing_symbols_compare_the_reading_with_the_spans_text():
    longer = feats(r"T = 2\pi\sqrt{m/k}", text="T = 2π")  # reaches past the box
    assert longer["ext_extra"] == 1.0 and longer["ext_missing"] == 0.0
    shorter = feats(r"T = 2", text="T = 2π")
    assert shorter["ext_missing"] == 1.0 and shorter["ext_extra"] == 0.0
    same = feats(r"T = 2\pi", text="T = 2π")
    assert same["ext_extra"] == 0.0 and same["ext_missing"] == 0.0
    # no text layer: nothing to extend past
    assert feats("x", text="")["no_text"] == 1.0 and feats("x", text="")["ext_extra"] == 0.0


def test_a_text_layer_without_unicode_letters_is_noticed():
    assert feats("x", text="")["no_unicode"] == 1.0
    assert feats("x", text="(cid:12)")["no_unicode"] == 1.0
    assert feats("x", text="x")["no_unicode"] == 0.0


def test_the_skipped_second_reading_is_the_escalation_or_its_note():
    assert feats("x", escalation="skipped")["esc_skipped"] == 1.0
    assert feats("x", codes=["single_reading"])["esc_skipped"] == 1.0
    assert feats("x", codes=["style_cosmetic"])["note_style"] == 1.0


def test_probability_is_a_logistic_of_the_weights():
    f = {"a": 1.0, "b": 0.0}
    assert fp.probability(f, {"a": 2.0}, -2.0) == pytest.approx(0.5)
    assert fp.probability(f, {"a": 50.0}, 0.0) == pytest.approx(1.0)
    assert fp.probability(f, {"a": -50.0}, 0.0) == pytest.approx(0.0, abs=1e-9)


def test_shipped_constants_name_known_features_only():
    assert set(fp.WEIGHTS) <= set(fp.FEATURES)
    assert fp.VERSION.startswith("flag-policy-")
    assert 0.0 < fp.THRESHOLD < 1.0


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


@pytest.fixture
def flag_nothing(monkeypatch):
    monkeypatch.setattr(fp, "THRESHOLD", 1.01)


def test_an_accepted_fix_nobody_is_asked_about_can_gain_a_decision(flag_everything):
    for review in ("none", "optional"):
        fix = fix_of(Status.ACCEPTED, review)
        out = fp.apply(fix, text="dx")
        assert out.status is Status.NEEDS_REVIEW and out.review == "required"
        assert out.equation == fix.equation and out.confidence == fix.confidence
        added = out.checks[-1]
        assert (added.name, added.code, added.passed) == (
            "equation.flag_policy",
            "flag_policy",
            False,
        )
        assert out.checks[:-1] == fix.checks  # nothing taken off


def test_a_fix_below_the_threshold_is_returned_as_it_is(flag_nothing):
    fix = fix_of(Status.ACCEPTED, "none")
    assert fp.apply(fix, text="dx") is fix


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


def test_a_required_fix_keeps_its_own_checks_and_reasons(flag_everything):
    fix = fix_of(
        Status.NEEDS_REVIEW,
        "required",
        checks=[Check(name="equation.text_layer", passed=False, code="symbol_mismatch")],
    )
    assert fp.apply(fix, text="dx") is fix


def test_apply_without_a_proposal_does_nothing(flag_everything):
    fix = Fix(element_id="e", kind=Kind.EQUATION, status=Status.ACCEPTED, review="none")
    assert fp.apply(fix, text="") is fix


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


def test_the_setting_is_off_by_default_and_changes_nothing(flag_everything):
    assert make_settings().flag_policy is False
    fix = run_step()
    assert fix.status is Status.ACCEPTED and fix.review != "required"
    assert all(c.code != "flag_policy" for c in fix.checks)


def test_the_setting_adds_the_decision_in_the_step(flag_everything):
    fix = run_step(flag_policy=True)
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert fix.equation is not None and fix.equation.latex == "dx"
    assert [c.code for c in fix.checks if not c.passed] == ["flag_policy"]


def test_the_setting_on_with_a_high_threshold_changes_nothing(flag_nothing):
    off, on = run_step(), run_step(flag_policy=True)
    assert on.model_dump() == off.model_dump()


def test_the_added_decision_has_a_sentence_on_the_review_screen():
    from readablestem.web.messages import CHECK_MESSAGES

    assert ("equation.flag_policy", "flag_policy") in CHECK_MESSAGES


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


def test_rows_carry_the_features_the_step_computes_and_no_text(spec):
    rows, items = ff.rows_of(spec)
    assert len(rows) == 12 * 21 and len(items) == 12
    row = rows[0]
    assert tuple(row["features"]) == fp.FEATURES
    assert row["features"]["upright"] == 1.0 and row["line_context"] is True
    assert "pred_text" not in row and "text" not in row
    assert sum(ff.candidate(r) for r in rows) == 12 * 20  # the required ones are not candidates


def test_the_fit_is_deterministic_and_finds_the_upright_signal(spec):
    rows, _ = ff.rows_of(spec)
    cand = [r for r in rows if ff.candidate(r)]
    x = ff.design(cand)
    y = np.array([r["fit_wrong"] for r in cand], dtype=float)
    w1, b1 = ff.fit_logistic(x, y, 1.0)
    w2, b2 = ff.fit_logistic(x, y, 1.0)
    assert (w1 == w2).all() and b1 == b2
    weights = dict(zip(fp.FEATURES, w1, strict=True))
    assert weights["upright"] == max(weights.values()) and weights["upright"] > 2.0
    assert b1 < 0  # most readings are right
    again = ff.cross_fit(cand, 1.0)
    assert (again == ff.cross_fit(cand, 1.0)).all()
    assert ff.auroc(y, again) is not None and ff.auroc(y, again) > 0.8  # type: ignore[operator]
    assert ff.choose_l2(cand)[0] in ff.L2_GRID


def test_folds_never_split_a_paper(spec):
    rows, _ = ff.rows_of(spec)
    fold_of = {}
    for r, f in zip(rows, ff.folds(rows), strict=True):
        assert fold_of.setdefault(r["paper"], f) == f
    assert set(fold_of.values()) == set(range(5))


def test_before_and_after_count_the_added_decisions(spec):
    rows, items = ff.rows_of(spec)
    before = ff.summarise(ff.paper_vectors(rows, items, set()))
    assert before["gold"] == 12 * 24 and before["pages"] == 24 and before["papers"] == 12
    assert before["decisions per page"][0] == pytest.approx(36 / 24)
    wrong_upright = {
        (r["paper"], r["gold_id"])
        for r in rows
        if ff.candidate(r) and r["wrong"] and r["features"]["upright"]
    }
    after = ff.summarise(ff.paper_vectors(rows, items, wrong_upright))
    assert after["added"] == len(wrong_upright) == after["added_wrong"]
    assert after["silent"] == before["silent"] - len(wrong_upright)
    assert after["decisions per page"][0] == pytest.approx((36 + len(wrong_upright)) / 24)
    assert after["flag recall"][0] > before["flag recall"][0]
    low, high = after["flag recall"][1:]
    assert low is not None and high is not None and low <= after["flag recall"][0] <= high


def test_the_threshold_spends_the_budget_or_reaches_the_goal(spec):
    rows, items = ff.rows_of(spec)
    cand = [r for r in rows if ff.candidate(r)]
    probs = np.array([0.9 if r["features"]["upright"] else 0.05 for r in cand])
    choice = ff.choose_threshold(cand, probs, items)
    # 36 decisions on 24 pages are asked; 4 a page allows 60 more; 48 upright readings, 36 wrong
    assert choice["budget_rows"] == 60 and choice["rows"] == 48
    assert choice["threshold"] == pytest.approx(0.9)
    # every wrong upright is caught; the plain wrong ones remain
    silent_left = sum(r["wrong"] and not r["features"]["upright"] for r in cand)
    assert choice["silent_per_100"] == pytest.approx(100 * silent_left / (12 * 24))
    assert choice["met"] is (choice["silent_per_100"] <= ff.TARGET_SILENT_PER_100)


def test_a_budget_too_small_for_the_goal_is_reported_not_met(spec, monkeypatch):
    monkeypatch.setattr(ff, "MAX_DECISIONS_PER_PAGE", 1.6)
    rows, items = ff.rows_of(spec)
    cand = [r for r in rows if ff.candidate(r)]
    probs = np.array([0.9 if r["features"]["upright"] else 0.05 for r in cand])
    choice = ff.choose_threshold(cand, probs, items)
    assert choice["budget_rows"] == 2  # 1.6 * 24 - 36, rounded down
    # the 48 upright readings share one probability: all or none, and 48 do not fit
    assert choice["met"] is False and choice["rows"] == 0 and choice["threshold"] == 1.0
    assert choice["unbounded_rows"] == 48


def test_the_threshold_is_chosen_on_the_development_split_only():
    assert ff.THRESHOLD_SPLIT in ff.FIT_SPLITS
    assert not set(ff.FIT_SPLITS) & set(ff.VALIDATE_SPLITS)
    assert set(ff.VALIDATE_SPLITS) == {"fresh2", "fresh3", "fresh4"}
    assert {ff.SPLITS[n].role for n in ff.FIT_SPLITS} == {"fit"}


def test_the_dump_is_one_json_line_per_row(spec, tmp_path, monkeypatch):
    monkeypatch.setitem(ff.SPLITS, "fixture", spec)
    out = tmp_path / "rows.jsonl"
    assert ff.dump(out, ["fixture"]) == 12 * 21
    first = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert first["f_upright"] == 1.0 and "features" in first and first["split"] == "fixture"
