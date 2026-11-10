"""run_pipeline_eval end to end, offline: fake inputs, FakeVisionClient keyed to the
rendered PNG hashes, and the other work packages' steps replaced by contract-shaped fakes."""

from __future__ import annotations

import json
import math

import pytest

from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import (
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
from readablestem.eval import pipeline_eval as pe
from readablestem.eval.render import RenderError
from readablestem.models.fake import FakeVisionClient, image_key
from readablestem.pipeline import equation_step, figure_step, heading_step, html_checks, layout, run
from readablestem.pipeline.layout import Layout
from test_eval_support import (
    EQ_A,
    EQ_F,
    EQ_V,
    EQ_X,
    FakeMedia,
    base_record,
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
    assert [c[0] for c in media.calls] == ["equation"] * 4 + ["close"]

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


def test_judge_refused_after_describe_keeps_the_describe_cost(monkeypatch, tmp_path):
    gold = write_gold(tmp_path, [figure("p-1", 0, "A ball on a ramp.", "Figure 1.1")])
    spend = budget(max_calls=1)  # describe fits, the judge call does not
    client = FakeVisionClient(defaults={"figure": {"alt": "A ramp.", "confidence": 0.9}})
    client.budget = spend
    monkeypatch.setattr(figure_step, "describe", fake_describe)
    report = pe.run_pipeline_eval(config(gold, "figures", client, budget=spend)).report
    assert (report["n"], report["stopped_reason"]) == (0, "budget_job_calls")
    assert report["cost"]["total_usd"] == 0
    assert spend.spent_usd > 0
    assert report["cost"]["dropped_usd"] == pytest.approx(spend.spent_usd)


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


def test_text_layer_cross_check_keeps_a_confident_wrong_answer_for_review(monkeypatch, tmp_path):
    gold = write_gold(
        tmp_path, [equation("p-1", 0, EQ_A, "block"), equation("p-1", 1, EQ_X, "inline")]
    )
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    by_image = {
        ("equation", image_key(fake_png(EQ_A))): {"latex": "x = y", "confidence": 0.95},
        ("equation", image_key(fake_png(EQ_X))): {"latex": "x = 2", "confidence": 0.95},
    }

    def run_with(media):
        client = FakeVisionClient(by_image=by_image)
        return pe.run_pipeline_eval(config(gold, "equations", client, media=media))

    items = run_with(FakeMedia()).items
    # "a=b+c" in the text layer vs "x=y" transcribed: no symbol in common.
    assert (items[0]["status"], items[0]["failed_checks"]) == ("needs_review", [])
    assert items[0]["confidence"] == pytest.approx(0.475)
    assert items[1]["route"] == "cheap"  # a short inline text layer starts cheap
    # Without a text layer (the old eval input) the same answer looked accepted.
    blind = run_with(FakeMedia(texts={EQ_A: None, EQ_X: None})).items
    assert (blind[0]["status"], blind[0]["confidence"]) == ("accepted", 0.95)
    # A long inline text layer goes straight to the main model, as in the app.
    long_text = run_with(FakeMedia(texts={EQ_X: "x = 2 (a long text layer here)"})).items
    assert long_text[1]["route"] == "main"


class FailingMedia(FakeMedia):
    """Building input number `fail_at` (0-based, any kind) fails, like an openstax.org
    download that times out."""

    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self.fail_at = fail_at
        self.built = 0

    def _count(self) -> None:
        if self.built == self.fail_at:
            raise RenderError("image download failed (URLError)")
        self.built += 1

    def equation_png(self, record):
        self._count()
        return super().equation_png(record)

    def figure_png(self, url):
        self._count()
        return super().figure_png(url)

    def page_pdf(self, page_records):
        self._count()
        return super().page_pdf(page_records)


@pytest.mark.parametrize(
    ("task", "records"),
    [
        ("equations", [equation("p-1", i, m, "block") for i, m in enumerate([EQ_V, EQ_A, EQ_F])]),
        ("figures", [figure("p-1", i, "A ramp.", f"Figure 1.{i}") for i in range(3)]),
        ("document", [equation(f"p-{i}", 0, EQ_V, "block") for i in range(3)]),
    ],
)
def test_an_input_that_fails_to_build_fails_the_run_before_any_spend(
    monkeypatch, tmp_path, task, records
):
    """Regression: inputs were built lazily, so a failed download on item k raised after
    k-1 paid items and the CLI wrote no report: the spend was lost."""
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    monkeypatch.setattr(figure_step, "describe", fake_describe)
    convert_calls = []
    monkeypatch.setattr(run, "convert_pdf", lambda *a, **k: convert_calls.append(1) or converted())
    monkeypatch.setattr(html_checks, "static_checks", lambda html: [])
    monkeypatch.setattr(html_checks, "axe_checks", lambda html: [])
    spend = budget()
    client = FakeVisionClient(
        defaults={"equation": {"latex": "x", "confidence": 0.9}, "figure": None}, budget=spend
    )
    gold = write_gold(tmp_path, records)
    with pytest.raises(RenderError, match="download failed"):
        pe.run_pipeline_eval(config(gold, task, client, media=FailingMedia(2), budget=spend))
    assert client.requests == [] and convert_calls == []
    assert (spend.calls, spend.spent_usd) == (0, 0)


# ------------------------------------------------------------------------- headings


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
    figure_images = [r.images for r in client.requests if r.step == "figure"]
    for request in judge_requests:
        # The judge sees the same figure image the figure step saw (blind to who wrote
        # the alt text, not to the picture).
        assert request.model == "gpt-6.1-sol" and request.images in figure_images
        text = (request.instructions + (request.context_text or "")).lower()
        for leak in ("gpt", "luna", "sol", "readablestem", "pipeline", "model output"):
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


# WP4's html_checks.axe_checks result when axe could not run (its own Playwright session
# failed to start, for one), verbatim.
AXE_ERROR = Check(name="axe", passed=False, severity="warning", code="axe_error")


def converted(html: str = "<html></html>") -> ConversionOutput:
    result = DocumentResult(
        pipeline_version=run.PIPELINE_VERSION,
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
        html=html,
    )
    return ConversionOutput(result=result)


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


def test_document_eval_scores_the_dev_v2_classes_on_the_output_html(monkeypatch, tmp_path):
    """Scripts, code, glossary and hyphenated words (eval.prose_classes) are scored on the
    page's converted HTML, beside the equations; a page without such gold has no block."""
    gold = write_gold(
        tmp_path,
        [
            equation("p-1", 0, EQ_V, "block"),
            base_record(
                "p-1", "script", 0, text="the ion Zn^{2+} here", classes=["prose_chem_script"],
                runs=[{"script": "sup", "value": "2+", "class": "prose_chem_script"}],
            ),
            base_record("p-1", "glossary", 0, term="Limit", definition="The value approached."),
            base_record("p-1", "hyphen_break", 0, word="pattern", left="pat", right="tern",
                        compound=False),
            equation("p-2", 0, EQ_X, "inline"),
        ],
    )  # fmt: skip
    html = "<html><body><p>the ion Zn<sup>2+</sup> here</p><p>a pat-tern</p></body></html>"
    monkeypatch.setattr(run, "convert_pdf", lambda pdf, **_: converted(html))
    monkeypatch.setattr(html_checks, "static_checks", lambda html: [])
    monkeypatch.setattr(html_checks, "axe_checks", lambda html: [])
    run_ = pe.run_pipeline_eval(config(gold, "document", FakeVisionClient()))
    classes = run_.report["metrics"]["prose_classes"]
    assert classes["scripts"]["scripted"] == 1 and classes["scripts"]["sub_sup"] == 1
    assert classes["glossary"]["missing"] == 1
    assert classes["hyphen_breaks"]["kept_hyphen"] == 1 and "code" not in classes
    by_page = {i["id"]: i for i in run_.items}
    assert "prose_classes" in by_page["physics/p-1"]
    assert "prose_classes" not in by_page["physics/p-2"]
    assert "### Prose classes" in pe.format_markdown(run_.report)


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
