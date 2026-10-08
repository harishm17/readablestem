"""run_pipeline_eval end to end, offline: fake inputs, FakeVisionClient keyed to the
rendered PNG hashes, and the other work packages' steps replaced by contract-shaped fakes."""

from __future__ import annotations

import json
import math

import pytest

from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.models.fake import FakeVisionClient, image_key
from a11y_stem.pipeline import equation_step, figure_step, heading_step, html_checks, layout, run
from a11y_stem.pipeline.layout import Layout
from test_eval_support import (
    EQ_A,
    EQ_F,
    EQ_V,
    EQ_X,
    FakeMedia,
    eq_fix,
    equation,
    fake_describe,
    fake_png,
    fake_transcribe,
    figure,
    heading,
    settings,
    write_gold,
)

DATE = "2026-10-07"


def budget(cap: float = math.inf, max_calls: int = 1000) -> JobBudget:
    return JobBudget(InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=max_calls)


def config(gold, task, client, **kw) -> pe.EvalConfig:
    return pe.EvalConfig(
        task=task,
        settings=settings(),
        client=client,
        media=kw.pop("media", FakeMedia()),
        date=DATE,
        gold_dir=gold,
        client_label="fake",
        gold_label="gold",
        **kw,
    )


# ------------------------------------------------------------------------ equations


@pytest.fixture
def equation_gold(tmp_path):
    return write_gold(
        tmp_path,
        [
            equation("p-1", 0, EQ_V, "block"),
            equation("p-1", 1, EQ_X, "inline"),
            equation("p-2", 0, EQ_A, "block"),
            equation("p-2", 1, EQ_F, "block"),
        ],
    )


def equation_client(**kw) -> FakeVisionClient:
    """Answers keyed by (step, sha256 of the rendered PNG), like a recorded run."""
    answers = {
        EQ_V: {"latex": r"v = \frac{d}{t}", "confidence": 0.95},  # right, accepted
        EQ_X: {"latex": "x = 3", "confidence": 0.6},  # wrong; cheap -> main escalation
        EQ_A: None,  # refusal -> FAILED, no proposal
        EQ_F: {"latex": "F = m a", "confidence": 0.7},  # right, needs review
    }
    by_image = {("equation", image_key(fake_png(m))): a for m, a in answers.items()}
    return FakeVisionClient(by_image=by_image, **kw)


def test_equation_eval_scores_rendered_inputs(monkeypatch, equation_gold):
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    media = FakeMedia()
    client = equation_client(budget=budget())
    run_ = pe.run_pipeline_eval(config(equation_gold, "equations", client, media=media))
    report, items = run_.report, run_.items

    # Every request carried the PNG the renderer produced for that gold record.
    assert [r.images[0] for r in client.requests[:1]] == [fake_png(EQ_V)]
    assert [c[0] for c in media.calls] == ["equation"] * 4

    assert (report["n"], report["n_planned"], report["complete"]) == (4, 4, True)
    m = report["metrics"]
    assert m["exact_lenient"] == 0.5
    assert m["proposal_rate"] == 0.75
    assert m["by_status"]["accepted"]["n"] == 1
    assert m["by_status"]["needs_review"] == {
        "n": 2,
        "share": 0.5,
        "exact_lenient": 0.5,
        "mean_teds": pytest.approx(items[1]["teds"] / 2 + 0.5),
    }
    assert m["by_status"]["failed"]["exact_lenient"] == 0.0
    assert {k: v["n"] for k, v in m["by_route"].items()} == {"main": 3, "escalated": 1}
    selective = {r["threshold"]: r for r in m["selective"]}
    assert (selective[0.5]["kept"], selective[0.5]["accuracy"]) == (3, pytest.approx(2 / 3))
    assert (selective[0.9]["coverage"], selective[0.9]["accuracy"]) == (0.25, 1.0)

    calls = 5  # one per equation, plus the escalation
    assert report["cost"]["model_calls"] == calls
    assert report["cost"]["total_usd"] == pytest.approx(sum(i["cost_usd"] for i in items))
    assert report["cost"]["per_item_usd"] == pytest.approx(report["cost"]["total_usd"] / 4)
    assert report["latency_ms"]["p50"] == 1.0  # FakeVisionClient reports 1 ms per call
    assert report["prompt_versions"] == {"equation": "equation-test1"}
    assert report["models_used"] == ["gpt-6-luna", "gpt-6.1-sol"]
    assert report["date"] == DATE and report["client"] == "fake"
    assert report["dataset"]["manifest_sha256"] == pe.manifest_sha256(equation_gold)
    assert report["dataset"]["gold_dir"] == "gold"
    json.dumps(report)  # serialisable as is


def test_equation_items_hold_predictions_for_inspection(monkeypatch, equation_gold):
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    items = pe.run_pipeline_eval(
        config(equation_gold, "equations", equation_client(budget=budget()))
    ).items
    by_id = {i["id"].rsplit("/", 2)[-2] + "/" + i["id"].rsplit("/", 1)[-1]: i for i in items}
    assert by_id["p-1/eq-000"]["pred_latex"] == r"v = \frac{d}{t}"
    assert by_id["p-1/eq-001"]["route"] == "escalated"
    assert by_id["p-2/eq-000"]["status"] == "failed" and by_id["p-2/eq-000"]["pred_mathml"] is None


def test_spend_stop_reports_a_partial_run(monkeypatch, equation_gold):
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    spend = budget(max_calls=2)
    report = pe.run_pipeline_eval(
        config(equation_gold, "equations", equation_client(budget=spend))
    ).report
    # Call 1 = eq0; calls 2-3 = eq1 (cheap, then escalation refused): eq1 is dropped.
    assert (report["n"], report["n_planned"]) == (1, 4)
    assert report["complete"] is False
    assert report["stopped_reason"] == "budget_job_calls"
    assert report["metrics"]["exact_lenient"] == 1.0  # over the finished item only
    assert spend.calls == 2


def test_dollar_cap_stops_before_overspending(monkeypatch, equation_gold):
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    # Each main-model call reserves its worst case (~$0.020) and settles at ~$0.002, so
    # a $0.022 cap fits the first equation and the cheap call, not the escalation.
    spend = budget(cap=0.022)
    report = pe.run_pipeline_eval(
        config(equation_gold, "equations", equation_client(budget=spend))
    ).report
    assert not report["complete"] and report["stopped_reason"] == "budget_job_usd"
    assert report["n"] == 1
    assert spend.spent_usd <= 0.022


def test_budget_failed_fix_also_stops_the_run(monkeypatch, equation_gold):
    def transcribe(element, png, **_):
        return Fix(
            element_id=element.id, kind=Kind.EQUATION, status=Status.FAILED, reason="budget_daily"
        )

    monkeypatch.setattr(equation_step, "transcribe", transcribe)
    report = pe.run_pipeline_eval(config(equation_gold, "equations", FakeVisionClient())).report
    assert (report["n"], report["stopped_reason"]) == (0, "budget_daily")
    assert report["metrics"]["exact_lenient"] is None


def test_limit_changes_the_selection_hash(monkeypatch, equation_gold):
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    full = pe.run_pipeline_eval(config(equation_gold, "equations", equation_client())).report
    two = pe.run_pipeline_eval(
        config(equation_gold, "equations", equation_client(), limit=2)
    ).report
    assert two["n_planned"] == 2 and two["dataset"]["limit"] == 2
    assert two["dataset"]["selection_sha256"] != full["dataset"]["selection_sha256"]
    assert two["dataset"]["manifest_sha256"] == full["dataset"]["manifest_sha256"]


def test_markdown_summary_has_the_headline_and_tables(monkeypatch, equation_gold):
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    report = pe.run_pipeline_eval(config(equation_gold, "equations", equation_client())).report
    text = pe.format_markdown(report)
    assert "## Eval: equations (2026-10-07, fake client)" in text
    assert "| exact_lenient | 0.500 |" in text
    assert "### Selective prediction" in text and "| 0.50 | 0.750 | 0.667 | 3 |" in text
    assert "### By route" in text
    # Metrics only: no predicted LaTeX or MathML in the printed summary.
    assert "frac" not in text and "<math" not in text


def test_unknown_task_is_refused(equation_gold):
    with pytest.raises(ValueError, match="unknown task"):
        pe.run_pipeline_eval(config(equation_gold, "tables", FakeVisionClient()))


# ------------------------------------------------------------------------- headings


def test_heading_eval_runs_layout_and_heading_fixes_on_printed_pages(monkeypatch, tmp_path):
    gold = write_gold(
        tmp_path,
        [
            heading("p-1", 0, 2, "2.1 Motion"),
            heading("p-1", 1, 3, "Teacher Support", "os-teacher"),
            heading("p-1", 2, 3, "Speed"),
            equation("p-2", 0, EQ_V, "block"),  # a page without headings is not a unit
        ],
    )
    seen_pdfs = []

    def analyze(pdf_bytes, *, pages=None, max_pages=None):
        seen_pdfs.append(pdf_bytes)
        elements = [
            Element(id=f"p1-e{i}", kind=Kind.HEADING, page=0, bbox=(0, 0, 1, 1), order=i, text=t)
            for i, t in enumerate(["2.1 Motion", "Teacher Support", "Footer"])
        ]
        return Layout(elements=elements, page_count=1, pages=[0])

    def heading_fixes(elements):
        levels = [1, 2, 2]
        return [
            Fix(
                element_id=e.id,
                kind=Kind.HEADING,
                status=Status.ACCEPTED,
                confidence=1.0,
                heading=HeadingFix(level=level, text=e.text or ""),
            )
            for e, level in zip(elements, levels, strict=True)
        ]

    monkeypatch.setattr(layout, "analyze", analyze)
    monkeypatch.setattr(heading_step, "heading_fixes", heading_fixes)
    report = pe.run_pipeline_eval(config(gold, "headings", FakeVisionClient())).report
    assert seen_pdfs == [b"%PDF-fake p-1"]
    m = report["metrics"]
    assert (m["precision"], m["recall"], m["level_accuracy"]) == (
        pytest.approx(2 / 3),
        pytest.approx(2 / 3),
        1.0,
    )
    assert report["n"] == 1 and report["dataset"]["unit"] == "page"
    assert report["cost"]["model_calls"] == 0 and report["prompt_versions"] == {}


# -------------------------------------------------------------------------- figures


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


def test_figure_eval_describes_then_judges_blind(monkeypatch, tmp_path):
    gold = write_gold(
        tmp_path,
        [
            figure("p-1", 0, "A ball on a ramp.", "Figure 1.1 A ball rolls."),
            figure("p-1", 1, "A graph of speed.", "Figure 1.2 Speed."),
            figure("p-1", 2, "", "Figure 1.3 Decoration."),  # no reference: not judged
            figure("p-1", 3, "A cart.", "Figure 1.4 Cart."),  # step abstains
        ],
    )
    urls = [
        f"https://openstax.org/apps/archive/20990101.000000/resources/p-1-{i}" for i in range(4)
    ]
    by_image = {
        ("figure", image_key(fake_png(urls[0]))): {"alt": "Ball on a ramp.", "confidence": 0.9},
        ("figure", image_key(fake_png(urls[1]))): {"alt": "A pie chart.", "confidence": 0.7},
        ("figure", image_key(fake_png(urls[2]))): {"alt": "A line.", "confidence": 0.9},
        ("figure", image_key(fake_png(urls[3]))): None,
    }
    client = FakeVisionClient(by_image=by_image, responders={"judge": judge_responder})
    monkeypatch.setattr(figure_step, "describe", fake_describe)
    run_ = pe.run_pipeline_eval(config(gold, "figures", client))
    m = run_.report["metrics"]
    assert m["with_reference"] == 3
    assert m["judge_pass_rate"] == pytest.approx(1 / 3)  # pass, fail, abstained
    assert m["judge_pass_rate_answered"] == 0.5
    assert m["proposal_rate"] == 0.75

    judge_requests = [r for r in client.requests if r.step == "judge"]
    assert len(judge_requests) == 2
    for request in judge_requests:
        assert request.model == "gpt-6.1-sol" and request.images == []
        text = (request.instructions + (request.context_text or "")).lower()
        for leak in ("gpt", "luna", "sol", "a11y-stem", "pipeline", "model output"):
            assert leak not in text
    assert "<caption>\nFigure 1.1 A ball rolls.\n</caption>" in judge_requests[0].context_text

    figure_requests = [r for r in client.requests if r.step == "figure"]
    assert figure_requests[0].context_text == "Figure 1.1 A ball rolls."  # caption as context
    report = run_.report
    assert report["models"]["judge"] == "gpt-6.1-sol"
    assert report["prompt_versions"]["judge"].startswith("judge_figure-")
    assert report["cost"]["judge_usd"] > 0
    assert report["cost"]["per_item_usd"] == pytest.approx(report["cost"]["pipeline_usd"] / 4)
    row = run_.items[0]
    assert (row["candidate_alt"], row["reference_alt"], row["judge_pass"]) == (
        "Ball on a ramp.",
        "A ball on a ramp.",
        True,
    )


# ------------------------------------------------------------------------- document


def test_document_eval_aligns_equations_and_runs_html_checks(monkeypatch, tmp_path):
    gold = write_gold(
        tmp_path,
        [
            equation("p-1", 0, EQ_V, "block"),
            equation("p-1", 1, EQ_X, "inline"),
            heading("p-1", 0, 2, "Title"),
        ],
    )

    def convert_pdf(pdf_bytes, *, settings, client, concurrency=4, **_):
        assert pdf_bytes == b"%PDF-fake p-1"
        elements = [
            Element(
                id="p1-e0",
                kind=Kind.EQUATION,
                page=0,
                bbox=(0, 0, 1, 1),
                order=0,
                text="v = d/t",
                display="block",
            )
        ]
        result = DocumentResult(
            pipeline_version=run.PIPELINE_VERSION,
            prompt_versions={"equation": "equation-v9"},
            elements=elements,
            fixes=[eq_fix("p1-e0", r"v=\frac{d}{t}")],
            stats=DocumentStats(pages=1, cost_usd=0.01, latency_ms=1500, model_calls=1),
            html="<html></html>",
        )
        return ConversionOutput(result=result)

    monkeypatch.setattr(run, "convert_pdf", convert_pdf)
    monkeypatch.setattr(html_checks, "static_checks", lambda html: [Check(name="ok", passed=True)])
    monkeypatch.setattr(
        html_checks,
        "axe_checks",
        lambda html: [Check(name="axe.color-contrast", passed=False, code="color-contrast")],
    )
    report = pe.run_pipeline_eval(config(gold, "document", FakeVisionClient())).report
    m = report["metrics"]
    assert (m["detection_precision"], m["detection_recall"]) == (1.0, 0.5)
    assert m["matched_exact_lenient"] == 1.0 and m["end_to_end_exact_lenient"] == 0.5
    assert (m["static_violations"], m["axe_violations"]) == (0, 1)
    assert m["axe_violation_codes"] == {"color-contrast": 1}
    assert report["latency_ms"]["p50"] == 1500.0
    assert report["prompt_versions"]["equation"] == "equation-v9"


def test_document_eval_drops_a_half_converted_page(monkeypatch, tmp_path):
    gold = write_gold(
        tmp_path, [equation("p-1", 0, EQ_V, "block"), equation("p-2", 0, EQ_X, "inline")]
    )

    def convert_pdf(pdf_bytes, **_):
        fix = Fix(
            element_id="p1-e0", kind=Kind.EQUATION, status=Status.FAILED, reason="budget_job_usd"
        )
        result = DocumentResult(
            pipeline_version="x",
            fixes=[fix],
            stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
        )
        return ConversionOutput(result=result)

    monkeypatch.setattr(run, "convert_pdf", convert_pdf)
    report = pe.run_pipeline_eval(config(gold, "document", FakeVisionClient())).report
    assert (report["n"], report["n_planned"], report["stopped_reason"]) == (0, 2, "budget_job_usd")


def test_a_page_the_layout_cannot_read_scores_as_found_nothing(monkeypatch, tmp_path):
    gold = write_gold(
        tmp_path,
        [
            heading("p-1", 0, 2, "One"),
            heading("p-2", 0, 2, "Two"),
            equation("p-2", 0, EQ_V, "block"),
        ],
    )

    def analyze(pdf_bytes, **_):
        if pdf_bytes.endswith(b"p-1"):
            raise layout.LayoutError("unreadable", "cannot open")
        element = Element(
            id="p1-e0", kind=Kind.HEADING, page=0, bbox=(0, 0, 1, 1), order=0, text="Two"
        )
        return Layout(elements=[element], page_count=1, pages=[0])

    def heading_fixes(elements):
        return [
            Fix(
                element_id=e.id,
                kind=Kind.HEADING,
                status=Status.ACCEPTED,
                heading=HeadingFix(level=1, text=e.text or ""),
            )
            for e in elements
        ]

    monkeypatch.setattr(layout, "analyze", analyze)
    monkeypatch.setattr(heading_step, "heading_fixes", heading_fixes)
    report = pe.run_pipeline_eval(config(gold, "headings", FakeVisionClient())).report
    assert report["complete"] and report["n"] == 2  # a pipeline failure is a result
    m = report["metrics"]
    assert (m["recall"], m["precision"], m["page_errors"]) == (0.5, 1.0, {"unreadable": 1})
    assert "| page_errors | unreadable: 1 |" in pe.format_markdown(report)

    def convert_pdf(pdf_bytes, **_):
        raise layout.LayoutError("too_many_pages", "too long")

    monkeypatch.setattr(run, "convert_pdf", convert_pdf)
    report = pe.run_pipeline_eval(config(gold, "document", FakeVisionClient())).report
    m = report["metrics"]
    assert report["n"] == 2 and m["page_errors"] == {"too_many_pages": 2}
    assert (m["detection_recall"], m["predicted_equations"]) == (0.0, 0)
