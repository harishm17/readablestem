"""Metric arithmetic for the pipeline evals, checked against hand-computed values."""

from __future__ import annotations

import pytest

from a11y_stem.core.types import Check, Element, EquationFix, Fix, Kind, ModelCall, Status
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.eval.equations import latex_to_mathml
from test_eval_support import EQ_A, EQ_V, EQ_X, base_record, eq_fix, equation

MAIN, CHEAP = "gpt-6.1-sol", "gpt-6-luna"
V_LATEX = r"v=\frac{d}{t}"


# --------------------------------------------------------------------------- stats


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [
        ([1, 2, 3, 4], 50, 2.5),
        ([1, 2, 3, 4], 95, 3.85),  # position 3 * 0.95 = 2.85 -> 3 + 0.85 * (4 - 3)
        ([10, 20], 95, 19.5),
        ([4, 1, 3, 2], 50, 2.5),  # order does not matter
        ([7], 95, 7.0),
        ([5, 1, 9], 0, 1.0),
        ([5, 1, 9], 100, 9.0),
    ],
)
def test_percentile_interpolates_between_ranks(values, q, expected):
    assert pe.percentile(values, q) == pytest.approx(expected)


def test_percentile_edge_cases():
    assert pe.percentile([], 50) is None
    with pytest.raises(ValueError):
        pe.percentile([1], 101)


def test_wilson_interval_matches_published_values():
    # 8 of 10: Wilson 95% interval is 0.490 to 0.943 (e.g. Newcombe 1998, Table I style).
    low, high = pe.wilson_interval(8, 10)
    assert (low, high) == (pytest.approx(0.4902, abs=1e-4), pytest.approx(0.9433, abs=1e-4))
    low, high = pe.wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-4)
    assert pe.wilson_interval(0, 0) is None


def test_latency_summary():
    assert pe.latency_summary([100, 200, 300, 400]) == {"p50": 250.0, "p95": 385.0, "n": 4}


# ------------------------------------------------------------------- equations


def item(conf, exact, teds, *, proposal=True, status="accepted", route="main", display="block"):
    return {
        "has_proposal": proposal,
        "confidence": conf,
        "exact_lenient": exact,
        "exact_strict": exact,
        "teds": teds,
        "status": status,
        "route": route,
        "display": display,
    }


ITEMS = [
    item(0.95, True, 1.0),
    item(0.60, False, 0.5, status="needs_review", route="escalated", display="inline"),
    item(None, False, 0.0, proposal=False, status="failed", route="fallback"),
    item(0.70, True, 1.0, status="needs_review", route="cheap", display="inline"),
]


def test_selective_table_thresholds_coverage_and_accuracy():
    rows = {r["threshold"]: r for r in pe.selective_table(ITEMS)}
    assert list(rows) == [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    assert rows[0.5] == {
        "threshold": 0.5,
        "kept": 3,
        "coverage": 0.75,
        "accuracy": pytest.approx(2 / 3),
        "mean_teds": pytest.approx(2.5 / 3),
    }
    assert rows[0.6]["kept"] == 3  # threshold is inclusive
    assert (rows[0.65]["coverage"], rows[0.65]["accuracy"]) == (0.5, 1.0)
    assert rows[0.7]["kept"] == 2
    assert (rows[0.95]["coverage"], rows[0.95]["accuracy"]) == (0.25, 1.0)


def test_selective_table_with_nothing_kept_reports_no_accuracy():
    (row,) = pe.selective_table([item(0.3, True, 1.0)], thresholds=[0.5])
    assert (row["kept"], row["coverage"], row["accuracy"]) == (0, 0.0, None)


def test_equation_metrics_count_missing_proposals_as_wrong():
    metrics = pe.equation_metrics(ITEMS)
    assert metrics["exact_lenient"] == 0.5
    assert metrics["mean_teds"] == pytest.approx(2.5 / 4)
    assert metrics["proposal_rate"] == 0.75
    assert metrics["by_status"] == {
        "accepted": {"n": 1, "share": 0.25, "exact_lenient": 1.0, "mean_teds": 1.0},
        "needs_review": {"n": 2, "share": 0.5, "exact_lenient": 0.5, "mean_teds": 0.75},
        "failed": {"n": 1, "share": 0.25, "exact_lenient": 0.0, "mean_teds": 0.0},
    }
    assert list(metrics["by_route"]) == ["cheap", "main", "escalated", "fallback"]
    assert metrics["by_display"]["inline"]["exact_lenient"] == 0.5
    low, high = metrics["exact_lenient_ci95"]
    assert low < 0.5 < high


def call(model, *, fallback=False, outcome="ok"):
    return ModelCall(
        step="equation", model=model, prompt_version="v", is_fallback=fallback, outcome=outcome
    )


@pytest.mark.parametrize(
    ("calls", "route"),
    [
        ([], "none"),
        ([call(CHEAP)], "cheap"),
        ([call(MAIN)], "main"),
        ([call(CHEAP), call(MAIN)], "escalated"),
        ([call(MAIN, outcome="timeout"), call(CHEAP, fallback=True)], "fallback"),
        ([call("other-model")], "other"),
    ],
)
def test_route_of(calls, route):
    assert pe.route_of(calls, main_model=MAIN, cheap_model=CHEAP) == route


def test_budget_reason_spots_fixes_that_were_never_tried():
    failed = Fix(element_id="e", kind=Kind.EQUATION, status=Status.FAILED, reason="budget_daily")
    assert pe.budget_reason(failed) == "budget_daily"
    refused = Fix(
        element_id="e",
        kind=Kind.EQUATION,
        status=Status.FAILED,
        reason="model_error",
        calls=[call(MAIN, outcome="budget_exceeded")],
    )
    assert pe.budget_reason(refused) == "budget_exceeded"
    other = Fix(element_id="e", kind=Kind.EQUATION, status=Status.FAILED, reason="model_error")
    assert pe.budget_reason(other) is None


def test_equation_item_scores_against_gold():
    record = equation("p", 0, EQ_V, "block")
    good = Fix(
        element_id="p1-e0",
        kind=Kind.EQUATION,
        status=Status.ACCEPTED,
        confidence=0.9,
        equation=EquationFix(latex=V_LATEX, mathml=latex_to_mathml(V_LATEX), speech=""),
        calls=[call(MAIN)],
    )
    row = pe.equation_item(record, good, _settings())
    assert (row["exact_lenient"], row["teds"], row["route"]) == (True, 1.0, "main")
    assert row["prompt_versions"] == {"equation": "v"}
    none = Fix(element_id="p1-e0", kind=Kind.EQUATION, status=Status.ABSTAINED)
    row = pe.equation_item(record, none, _settings())
    assert (row["exact_lenient"], row["teds"], row["has_proposal"]) == (False, 0.0, False)


def _settings():
    from test_eval_support import settings

    return settings()


# -------------------------------------------------------------------- alignment


def same(a, b):
    return 1.0 if a == b else 0.0


def test_align_keeps_order_and_skips_extras():
    assert pe.align(["a", "x", "b"], ["a", "b"], same, min_similarity=0.5) == [
        (0, 0, 1.0),
        (2, 1, 1.0),
    ]


def test_align_never_crosses():
    assert len(pe.align(["a", "b"], ["b", "a"], same, min_similarity=0.5)) == 1


def test_align_pairs_repeated_texts_in_order():
    pairs = pe.align(["ts", "x", "ts"], ["ts", "ts"], same, min_similarity=0.5)
    assert [(g, p) for g, p, _ in pairs] == [(0, 0), (2, 1)]


def test_align_respects_the_threshold():
    assert pe.align(["a"], ["b"], lambda a, b: 0.49, min_similarity=0.5) == []


def test_signatures():
    assert pe.text_signature(" a − B ") == "a-b"
    assert pe.math_signature(EQ_V) == "v=dt"
    assert pe.math_signature("<math><mi>") == ""  # unparseable: empty, never a crash
    assert pe.heading_signature("2.1  Speed & Velocity!") == "2 1 speed velocity"
    assert pe.similarity("", "x") == 0.0
    assert pe.similarity("speed", "sped") == pytest.approx(8 / 9)


# --------------------------------------------------------------------- headings


def h(level, text, context=None):
    return {"level": level, "text": text, "context": context}


def p(level, text, status="accepted"):
    return {"level": level, "text": text, "status": status}


def test_heading_page_item_matches_fuzzy_text_and_relative_levels():
    gold = [
        h(2, "2.1 Motion"),
        h(3, "Teacher Support", "os-teacher"),
        h(3, "Speed"),
        h(4, "Worked Example"),
    ]
    predicted = [
        p(1, "2.1 Motion"),
        p(3, "Teacher support", "needs_review"),  # relative level 2, gold relative 1
        p(2, "Sped"),  # ratio 8/9 >= 0.85
        p(2, "Running footer"),
    ]
    row = pe.heading_page_item("physics/p", gold, predicted)
    assert (row["n_gold"], row["n_pred"], row["matched"], row["level_correct"]) == (4, 4, 3, 2)
    assert [m["text"] for m in row["missed"]] == ["Worked Example"]
    assert [e["text"] for e in row["extra"]] == ["Running footer"]
    assert [m["level_correct"] for m in row["pairs"]] == [True, False, True]


def test_heading_metrics_are_micro_averaged():
    page_a = pe.heading_page_item(
        "a",
        [h(2, "One"), h(3, "Two", "note"), h(3, "Three"), h(3, "Four")],
        [p(1, "One"), p(2, "Two"), p(2, "Three"), p(2, "Extra", "needs_review")],
    )
    page_b = pe.heading_page_item("b", [h(2, "Five")], [p(1, "Five"), p(2, "Noise")])
    metrics = pe.heading_metrics([page_a, page_b])
    assert (metrics["matched"], metrics["gold_headings"], metrics["predicted_headings"]) == (
        4,
        5,
        6,
    )
    assert metrics["precision"] == pytest.approx(4 / 6)
    assert metrics["recall"] == pytest.approx(4 / 5)
    assert metrics["f1"] == pytest.approx(16 / 22)
    assert metrics["level_accuracy"] == 1.0
    assert metrics["precision_by_status"]["needs_review"] == {"n": 1, "precision": 0.0}
    assert metrics["recall_by_context"] == {
        "body": {"n": 4, "recall": 0.75},
        "note": {"n": 1, "recall": 1.0},
    }


def test_heading_metrics_with_no_predictions():
    row = pe.heading_page_item("a", [h(2, "One")], [])
    metrics = pe.heading_metrics([row])
    assert (metrics["precision"], metrics["recall"], metrics["f1"]) == (None, 0.0, None)


def test_predicted_headings_skip_abstained_and_failed():
    from a11y_stem.core.types import HeadingFix

    def fix(status, text="T"):
        return Fix(
            element_id="e",
            kind=Kind.HEADING,
            status=status,
            heading=HeadingFix(level=2, text=text),
        )

    kept = pe.predicted_headings(
        [fix(Status.ACCEPTED), fix(Status.NEEDS_REVIEW), fix(Status.ABSTAINED), fix(Status.FAILED)]
    )
    assert [k["status"] for k in kept] == ["accepted", "needs_review"]


# ---------------------------------------------------------------------- figures


def fig(outcome, *, passed=None, score=None, checks=True, proposal=True, factual=False):
    return {
        "judge_outcome": outcome,
        "judge_pass": passed,
        "judge_score": score,
        "judge_factual_error": factual if outcome == "judged" else None,
        "checks_passed": checks and proposal,
        "failed_checks": [] if checks else ["alt.too_long"],
        "has_proposal": proposal,
        "status": "accepted" if proposal else "abstained",
        "figure_type": "diagram" if proposal else None,
        "decorative": False if proposal else None,
    }


def test_figure_metrics_denominators():
    items = [
        fig("judged", passed=True, score=5),
        fig("judged", passed=False, score=2, factual=True),
        fig("no_proposal", proposal=False),
        fig("no_reference"),
        fig("judge_refused", checks=False),
    ]
    metrics = pe.figure_metrics(items)
    assert metrics["check_pass_rate"] == pytest.approx(3 / 5)
    assert metrics["with_reference"] == 4
    assert metrics["judge_measured"] == 3  # the refused judge call is not a measurement
    assert metrics["judge_errors"] == 1
    assert metrics["judge_pass_rate"] == pytest.approx(1 / 3)  # abstention counts as fail
    assert metrics["judge_pass_rate_answered"] == 0.5
    assert metrics["judge_mean_score"] == 3.5
    assert metrics["judge_factual_error_rate"] == 0.5
    assert metrics["failed_check_codes"] == {"alt.too_long": 1}
    assert metrics["by_status"] == {"accepted": 4, "abstained": 1}


# --------------------------------------------------------------------- document


def element(eid, kind, order, text=None, display=None):
    return Element(
        id=eid, kind=kind, page=0, bbox=(0, 0, 1, 1), order=order, text=text, display=display
    )


def test_document_page_item_aligns_equations_and_counts_violations():
    gold = [equation("p", 0, EQ_V, "block"), equation("p", 1, EQ_X, "inline")]
    gold.append(equation("p", 2, EQ_A, "block"))
    elements = [
        element("p1-e0", Kind.PARAGRAPH, 0, text="Speed is"),
        element("p1-e1", Kind.EQUATION, 1, text="v = d/t", display="block"),
        element("p1-e2", Kind.EQUATION, 2, text="F = ma", display="block"),  # not in gold
        element("p1-e3", Kind.EQUATION, 3, text="a = b + c", display="block"),
    ]
    fixes = {"p1-e1": eq_fix("p1-e1", r"v=\frac{d}{t}"), "p1-e3": eq_fix("p1-e3", "a=b-c")}
    static = [
        Check(name="html.lang", passed=True),
        Check(name="headings.order", passed=False, code="skipped_level"),
        Check(name="img.alt", passed=False, severity="warning", code="long_alt"),
    ]
    axe = [Check(name="axe", passed=False, severity="warning", code="skipped")]
    row = pe.document_page_item("physics/p", gold, elements, fixes, static, axe)
    assert (row["n_gold"], row["n_pred"], row["matched"]) == (3, 3, 2)
    assert [(m["gold_id"], m["element_id"]) for m in row["pairs"]] == [
        (gold[0]["id"], "p1-e1"),
        (gold[2]["id"], "p1-e3"),  # matched by its PDF text although the MathML is wrong
    ]
    assert [m["exact_lenient"] for m in row["pairs"]] == [True, False]
    assert row["missed_gold_ids"] == [gold[1]["id"]]
    assert row["static_violations"] == ["skipped_level"]  # warnings are not violations
    assert (row["axe_violations"], row["axe_status"]) == ([], "skipped")

    metrics = pe.document_metrics([row])
    assert metrics["detection_precision"] == pytest.approx(2 / 3)
    assert metrics["detection_recall"] == pytest.approx(2 / 3)
    assert metrics["matched_exact_lenient"] == 0.5
    assert metrics["end_to_end_exact_lenient"] == pytest.approx(1 / 3)
    assert metrics["detection_recall_by_display"] == {
        "block": {"n_gold": 2, "recall": 1.0},
        "inline": {"n_gold": 1, "recall": 0.0},
    }
    assert metrics["static_violation_codes"] == {"skipped_level": 1}
    assert (metrics["axe_skipped_pages"], metrics["axe_checked_pages"]) == (1, 0)


def test_axe_skip_link_failure_is_a_violation_not_a_skipped_run():
    skip_link = [Check(name="axe.skip-link", passed=False, code="skip-link")]
    assert pe.axe_status(skip_link) == "ran"
    assert pe.axe_status([Check(name="axe", passed=False, severity="warning", code="skipped")]) == (
        "skipped"
    )
    row = pe.document_page_item("p", [], [], {}, [], skip_link)
    assert (row["axe_violations"], row["axe_status"]) == (["skip-link"], "ran")


# WP4's html_checks results, verbatim: what axe_checks returns when it did not run.
AXE_SKIPPED = Check(name="axe", passed=True, severity="warning", code="skipped")
AXE_ERROR = Check(name="axe", passed=False, severity="warning", code="axe_error")


def test_axe_error_is_a_run_that_did_not_happen_not_a_clean_page():
    """Regression: a crashed axe (e.g. Playwright refusing a second sync session in the
    thread) was a warning, so it counted as zero violations and not as skipped."""
    clean = pe.document_page_item("p/clean", [], [], {}, [], [Check(name="axe", passed=True)])
    crashed = pe.document_page_item("p/crashed", [], [], {}, [], [AXE_ERROR])
    missing = pe.document_page_item("p/missing", [], [], {}, [], [AXE_SKIPPED])
    image_alt = Check(name="axe.image-alt", passed=False, code="image-alt")
    found = pe.document_page_item("p/found", [], [], {}, [], [image_alt])
    assert [r["axe_status"] for r in (clean, crashed, missing, found)] == [
        "ran",
        "error",
        "skipped",
        "ran",
    ]
    metrics = pe.document_metrics([clean, crashed, missing, found])
    assert metrics["axe_violations"] == 1 and metrics["axe_violation_codes"] == {"image-alt": 1}
    assert (
        metrics["axe_checked_pages"],
        metrics["axe_skipped_pages"],
        metrics["axe_error_pages"],
    ) == (2, 1, 1)
    # Any lone failed "axe" check is a broken run, whatever its code.
    broken = [Check(name="axe", passed=False, code="browser_crash")]
    assert pe.axe_status(broken) == "error"
    assert pe.document_page_item("p", [], [], {}, [], broken)["axe_violations"] == []
    assert pe.axe_status([]) is None  # no HTML to check (the page failed earlier)


# -------------------------------------------------------------------- selection


def test_select_units_and_limits():
    records = [
        equation("a", 0, EQ_V, "block"),
        base_record("a", "heading", 0, level=2, text="A"),
        equation("b", 0, EQ_X, "inline"),
        base_record("b", "figure", 0, caption="c", images=[{"url": None, "alt": "x"}]),
        base_record("c", "figure", 0, caption="c", images=[{"url": "https://openstax.org/r"}]),
    ]
    assert [u["id"] for u in pe.select_units("equations", records, None)] == [
        records[0]["id"],
        records[2]["id"],
    ]
    assert len(pe.select_units("equations", records, 1)) == 1
    assert [u["id"] for u in pe.select_units("figures", records, None)] == [records[4]["id"]]
    headings = pe.select_units("headings", records, None)
    assert pe.unit_ids("headings", headings) == ["physics/a"]
    assert pe.unit_ids("document", pe.select_units("document", records, 2)) == [
        "physics/a",
        "physics/b",
    ]
    with pytest.raises(ValueError, match="unknown task"):
        pe.select_units("tables", records, None)


def test_manifest_hash_tracks_content(tmp_path):
    from test_eval_support import write_gold

    gold = write_gold(tmp_path, [equation("a", 0, EQ_V, "block")])
    first = pe.manifest_sha256(gold)
    (gold / "manifest.json").unlink()
    no_manifest = pe.manifest_sha256(gold)
    assert len(first) == len(no_manifest) == 64
    path = next(gold.rglob("*.jsonl"))
    path.write_text(path.read_text().replace("block", "inline"))
    assert pe.manifest_sha256(gold) != no_manifest
