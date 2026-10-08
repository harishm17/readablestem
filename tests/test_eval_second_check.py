"""Eval metrics of the second automated check: figure flag rate and agreement with the
judge, equation shadow AUROC against exact_lenient, and the added cost (offline, with
the real figure and equation steps, FakeVisionClient and FakeVerifier)."""

from __future__ import annotations

import pytest

from a11y_stem.core.types import Fix, Kind, Status, Verification
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.eval import verifier_metrics as vm
from a11y_stem.models.fake import FakeVisionClient, image_key
from a11y_stem.models.verifier import FakeVerifier
from test_eval_support import (
    EQ_A,
    EQ_F,
    EQ_V,
    EQ_X,
    FakeMedia,
    equation,
    fake_png,
    figure,
    settings,
    write_gold,
)
from test_steps_support import use_fallback_router

DATE = "2026-10-08"


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def config(gold, task, client) -> pe.EvalConfig:
    return pe.EvalConfig(
        task=task,
        settings=settings(),
        client=client,
        media=FakeMedia(),
        date=DATE,
        gold_dir=gold,
        client_label="fake",
        gold_label="gold",
    )


# --------------------------------------------------------------------------- helpers


def test_auroc_and_kappa():
    assert vm.auroc([(0.9, True), (0.1, False)]) == 1.0
    assert vm.auroc([(0.1, True), (0.9, False)]) == 0.0
    assert vm.auroc([(0.5, True), (0.5, False)]) == 0.5
    assert vm.auroc([(0.5, True)]) is None
    assert vm.kappa([(True, True), (False, False)]) == 1.0
    assert vm.kappa([(True, True), (True, True)]) is None
    assert vm.kappa([(True, False), (False, True)]) == -1.0


# --------------------------------------------------------------------------- figures


def judge_responder(request):
    """Fake judge: passes a candidate that mentions 'ramp', fails anything else."""
    candidate = request.context_text.split("<candidate>", 1)[1].split("</candidate>", 1)[0]
    ok = "ramp" in candidate
    return {
        "conveys_key_information": ok,
        "factual_error": not ok,
        "score": 5 if ok else 2,
        "confidence": 0.8,
    }


def test_figure_eval_reports_flag_rate_agreement_and_cost(tmp_path):
    gold = write_gold(
        tmp_path,
        [
            figure("p-1", 0, "A ball on a ramp.", "Figure 1.1 A ball rolls."),
            figure("p-1", 1, "A graph of speed.", "Figure 1.2 Speed."),
            figure("p-1", 2, "A cart.", "Figure 1.3 Cart."),
        ],
    )
    urls = [
        f"https://openstax.org/apps/archive/20990101.000000/resources/p-1-{i}" for i in range(3)
    ]
    alts = ["A ball on a ramp, rolling down.", "A pie chart of fruit.", "A cart on a ramp."]

    def answer(alt):
        return {"figure_type": "diagram", "decorative": False, "alt": alt, "confidence": 0.95}

    by_image = {
        ("figure", image_key(fake_png(url))): answer(alt)
        for url, alt in zip(urls, alts, strict=True)
    }
    # The check flags the pie chart (the judge fails it too) and the cart (the judge
    # passes it: a disagreement).
    flagged = {"A pie chart of fruit.", "A cart on a ramp."}
    verifier = FakeVerifier(
        responder=lambda check, alt: {"ok": 0.2, "factual_error": 0.9} if alt in flagged else None
    )
    client = FakeVisionClient(
        by_image=by_image, responders={"judge": judge_responder}, verifier=verifier
    )
    run = pe.run_pipeline_eval(config(gold, "figures", client))
    rows = run.items
    assert [r["status"] for r in rows] == ["accepted"] * 3
    assert [r["verifier_flagged"] for r in rows] == [False, True, True]
    assert rows[1]["verifier_score"] == pytest.approx(0.1)
    check = run.report["metrics"]["second_check"]
    assert (check["answered"], check["flagged"], check["flag_rate"]) == (
        3,
        2,
        pytest.approx(0.6667),
    )
    agreement = check["agreement_with_judge"]
    assert agreement["n"] == 3 and agreement["raw_agreement"] == pytest.approx(0.6667)
    assert agreement["confusion"] == {
        "flag_and_judge_fail": 1,
        "flag_and_judge_pass": 1,
        "no_flag_and_judge_fail": 0,
        "no_flag_and_judge_pass": 1,
    }
    assert agreement["auroc_score_vs_judge_fail"] == 0.75
    cost = run.report["cost"]
    assert cost["verifier_requests"] == 3
    assert cost["verifier_usd"] == pytest.approx(check["cost_usd"])
    markdown = pe.format_markdown(run.report)
    assert "Second automated check (Decisions API)" in markdown and "kappa_with_judge" in markdown


# ------------------------------------------------------------------------- equations


def test_equation_eval_reports_the_shadow_auroc_against_exact_lenient(tmp_path):
    gold = write_gold(
        tmp_path,
        [
            equation("p-1", 0, EQ_V, "block"),
            equation("p-1", 1, EQ_A, "block"),
            equation("p-2", 0, EQ_F, "block"),
            equation("p-2", 1, EQ_X, "inline"),
        ],
    )
    answers = {
        EQ_V: r"v = \frac{d}{t}",  # right
        EQ_A: r"a = b - c",  # wrong sign
        EQ_F: r"F = m a",  # right
        EQ_X: "x = 2",  # right, inline: starts cheap
    }
    by_image = {
        ("equation", image_key(fake_png(m))): {
            "is_equation": True,
            "latex": latex,
            "confidence": 0.95,
        }
        for m, latex in answers.items()
    }
    alarm = {"match": 0.05, "cosmetic": 0.05, "meaning_changing": 0.9}
    verifier = FakeVerifier(responder=lambda check, latex: alarm if "-" in latex else None)
    client = FakeVisionClient(by_image=by_image, verifier=verifier)
    run = pe.run_pipeline_eval(config(gold, "equations", client))
    rows = {r["id"].rsplit("/", 2)[-2] + "/" + r["id"].rsplit("/", 1)[-1]: r for r in run.items}
    wrong = rows["p-1/eq-001"]
    assert not wrong["exact_lenient"] and wrong["shadow_p_meaning"] == 0.9
    assert wrong["shadow_flagged"] is True
    check = run.report["metrics"]["second_check"]
    assert check["mode"] == "shadow" and check["ran"] == 4
    assert check["final"]["auroc_vs_wrong"] == 1.0
    assert (check["final"]["would_flag"], check["final"]["would_flag_precision"]) == (1, 1.0)
    # Shadow: the statuses are what they would be without the check.
    assert {r["status"] for r in run.items} <= {"accepted", "needs_review"}
    assert run.report["cost"]["verifier_requests"] == 4
    assert "auroc_vs_wrong" in pe.format_markdown(run.report)


def test_cheap_stage_answers_are_counted_apart():
    items = [
        {
            "id": "a",
            "exact_lenient": True,
            "shadow_outcome": "ok",
            "shadow_p_meaning": 0.01,
            "shadow_flagged": False,
            "shadow_cheap_outcome": "ok",
            "shadow_cheap_p_meaning": 0.0,
            "shadow_cheap_flagged": False,
        },
        {
            "id": "b",
            "exact_lenient": False,
            "shadow_outcome": "ok",
            "shadow_p_meaning": 0.3,
            "shadow_flagged": True,
            "shadow_cheap_outcome": "ok",
            "shadow_cheap_p_meaning": 0.8,
            "shadow_cheap_flagged": True,
            "scorer_gap": True,
        },
        {
            "id": "c",
            "exact_lenient": True,
            "shadow_outcome": "timeout",
            "shadow_p_meaning": None,
            "shadow_flagged": None,
        },
    ]
    check = vm.equation_metrics(items)
    assert check["outcomes"] == {"ok": 2, "timeout": 1}
    assert check["final"]["answered"] == 2 and check["final"]["auroc_vs_wrong"] == 1.0
    assert check["final"]["auroc_vs_wrong_excluding_scorer_gaps"] is None  # one class left
    assert check["final"]["would_flag_wrong_excluding_scorer_gaps"] == 0
    assert check["cheap_stage"] == {"ran": 2, "answered": 2, "would_flag": 1, "mean_p_meaning": 0.4}


# -------------------------------------------------------------------------- document


def test_document_pairs_carry_the_final_shadow_answer():
    record = Verification(
        check="equation_match", mode="shadow", probabilities={"meaning_changing": 0.7}, flagged=True
    )
    fix = Fix(
        element_id="p1-e1", kind=Kind.EQUATION, status=Status.ACCEPTED, verifications=[record]
    )
    assert pe._shadow_pair(fix) == {"shadow_p_meaning": 0.7, "shadow_flagged": True}
    assert pe._shadow_pair(None) == {"shadow_p_meaning": None, "shadow_flagged": None}
    figure_record = Verification(
        check="figure_alt",
        probabilities={"ok": 0.1, "factual_error": 0.1},
        flagged=True,
        cost_usd=0.0001,
    )
    figure_fix = Fix(
        element_id="p1-e2", kind=Kind.FIGURE, status=Status.ACCEPTED, verifications=[figure_record]
    )
    page = vm.page_summary([fix, figure_fix])
    assert (page["figure_checked"], page["figure_flagged"], page["equation_would_flag"]) == (
        1,
        1,
        1,
    )
    items = [
        {
            "pairs": [
                {"exact_lenient": False, "shadow_p_meaning": 0.7, "shadow_flagged": True},
                {"exact_lenient": True, "shadow_p_meaning": 0.1, "shadow_flagged": False},
            ],
            "verifier": page,
            "verifier_cost_usd": 0.0002,
            "pdf_pages": 4,
        },
    ]
    check = vm.document_metrics(items)
    assert check["equations_matched"]["auroc_vs_wrong"] == 1.0
    assert check["per_pdf_page_usd"] == pytest.approx(0.00005)
    assert check["figure_flagged"] == 1
