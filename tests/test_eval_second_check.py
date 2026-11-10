"""Eval metrics of the second automated check: figure flag rate and agreement with the
judge, equation shadow AUROC against exact_lenient, and the added cost (offline, with
the real figure and equation steps, FakeVisionClient and FakeVerifier)."""

from __future__ import annotations

import pytest

from readablestem.eval import pipeline_eval as pe
from readablestem.eval import verifier_metrics as vm
from readablestem.models.fake import FakeVisionClient, image_key
from readablestem.models.verifier import FIGURE_VERSION, FakeVerifier
from test_eval_support import (
    FakeMedia,
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
    assert check["versions"] == [FIGURE_VERSION]
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


# -------------------------------------------------------------------------- document
