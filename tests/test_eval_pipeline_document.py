"""The document task's honesty metrics: why predicted equations did not match gold, and
which gold equations the layout cut into pieces (pipeline_eval.document_page_item)."""

from __future__ import annotations

from readablestem.core.types import Element, Kind
from readablestem.eval import pipeline_eval as pe
from test_eval_support import eq_fix, equation

# Delta d = d_f - d_0 (OpenStax 2-1) and v = Delta d / Delta t (2-3), as gold MathML.
DISPLACEMENT = (
    '<math display="block"><mi>\u0394</mi><mi mathvariant="bold">d</mi><mo>=</mo>'
    '<msub><mi mathvariant="bold">d</mi><mi>f</mi></msub><mo>\u2212</mo>'
    '<msub><mi mathvariant="bold">d</mi><mn>0</mn></msub></math>'
)
VELOCITY = (
    '<math display="block"><mi mathvariant="bold">v</mi><mo>=</mo><mfrac><mrow><mi>\u0394</mi>'
    '<mi mathvariant="bold">d</mi></mrow><mrow><mi>\u0394</mi><mi>t</mi></mrow></mfrac><mo>=</mo>'
    "<mn>250</mn><mtext>m/s</mtext></math>"
)


def element(index: int, text: str, display: str = "block", parent: str | None = None) -> Element:
    return Element(
        id=f"p1-e{index}",
        kind=Kind.EQUATION,
        page=0,
        bbox=(0, 0, 1, 1),
        order=index,
        text=text,
        display=display,  # type: ignore[arg-type]
        parent_id=parent,
    )


def page(gold: list[dict], predicted: list[Element], fixes=None) -> dict:
    return pe.document_page_item("p", gold, predicted, fixes or {}, [], [])


def test_an_equation_cut_into_pieces_counts_as_split_and_its_pieces_as_fragments():
    """2-3's worked example came out as "v =", "Delta d", "Delta t" and the rest."""
    gold = [equation("p-1", 0, VELOCITY, "block")]
    predicted = [
        element(0, "\U0001d42f="),
        element(1, "\u0394\U0001d41d"),
        element(2, "\u0394\U0001d461"),
        element(3, "= 250 m/s"),
    ]
    item = page(gold, predicted)
    assert item["matched"] == 1
    assert item["split_gold_ids"] == [gold[0]["id"]]
    assert item["unmatched_by_reason"] == {"prose_math": 0, "fragment": 3, "other": 0}
    assert {u["piece_of"] for u in item["unmatched"]} == {gold[0]["id"]}


def test_whole_inline_maths_without_gold_is_prose_maths_and_broken_pieces_are_fragments():
    gold = [equation("p-1", 0, DISPLACEMENT, "block")]
    predicted = [
        element(0, "\U0001d42d0", "inline", parent="p1-e9"),  # "d0" in the text above
        element(1, "\u0394\U0001d41d = \U0001d41df \u2212 \U0001d41d0 ."),
        element(2, "/h)] = 40", "inline", parent="p1-e10"),
        element(3, "t = 0 s", "inline", parent="p1-e10"),
        element(4, "b. 40 m/s"),
    ]
    item = page(gold, predicted)
    assert item["matched"] == 1 and item["split_gold_ids"] == []
    reasons = {u["element_id"]: u["reason"] for u in item["unmatched"]}
    assert reasons == {
        "p1-e0": "prose_math",  # a mention, not a piece of the display equation below
        "p1-e2": "fragment",
        "p1-e3": "prose_math",
        "p1-e4": "other",
    }


def test_prose_maths_needs_evidence_of_a_formula_and_quantities_are_other():
    """prose_math was a catch-all for unmatched inline predictions, so precision
    "excluding prose maths" was 1.0 by construction. Bare quantities and lone symbols
    (the fragment false positives of the smoke test) are "other" now."""
    texts = {
        "-49 m": [],
        "\u2212 40 m/s": ["signed number"],
        "2.5": [],
        "(\u0394)": ["math font or symbol"],
        "125m/s": ["italic variable with operator"],
        "9.8 m/s2": ["letter with sub/superscript"],
        "t = 0 s": [],
        "\U0001d42d0": [],  # bold d with subscript 0, as text
        "v f": ["letter with sub/superscript"],
        "x + y": ["italic variable with operator"],
    }
    predicted = [
        element(i, text, "inline", parent="p1-e99").model_copy(update={"reasons": reasons})
        for i, (text, reasons) in enumerate(texts.items())
    ]
    item = page([], predicted)
    reasons = [u["reason"] for u in item["unmatched"]]
    assert reasons == [*["other"] * 6, *["prose_math"] * 4]
    metrics = pe.document_metrics([item])
    assert metrics["detection_precision_excluding_prose_math"] == 0.0  # 0 of 6, not 0 of 0


def test_a_split_inline_equation_needs_its_pieces_in_the_same_paragraph():
    gold = [equation("p-1", 0, '<math display="inline"><mi>x</mi><mo>=</mo><mn>2</mn></math>',
                     "inline")]  # fmt: skip
    same = [element(0, "x = 2", "inline", "p1-e5"), element(1, "x =", "inline", "p1-e5")]
    other = [element(0, "x = 2", "inline", "p1-e5"), element(1, "x =", "inline", "p1-e8")]
    assert page(gold, same)["split_gold_ids"] == [gold[0]["id"]]
    assert page(gold, other)["split_gold_ids"] == []


def test_document_metrics_report_the_breakdown_and_precision_without_prose_maths():
    gold = [equation("p-1", 0, DISPLACEMENT, "block"), equation("p-1", 1, VELOCITY, "block")]
    predicted = [
        element(0, "d0", "inline", parent="p1-e9"),
        element(1, "\u0394d = df \u2212 d0"),
        element(2, "v ="),
        element(3, "\u0394d \u0394t = 250 m/s"),
    ]
    fixes = {"p1-e1": eq_fix("p1-e1", r"\Delta \mathbf{d} = \mathbf{d}_f - \mathbf{d}_0")}
    metrics = pe.document_metrics([page(gold, predicted, fixes)])
    assert metrics["unmatched_predicted"] == 2
    assert metrics["unmatched_by_reason"] == {"prose_math": 1, "fragment": 1, "other": 0}
    assert metrics["split_gold_equations"] == 1
    assert metrics["detection_precision"] == 0.5
    assert metrics["detection_precision_excluding_prose_math"] == 2 / 3


def test_the_markdown_summary_lists_the_unmatched_reasons():
    metrics = pe.document_metrics([page([], [element(0, "t = 0 s", "inline", "p1-e1")])])
    report = {
        "task": "document",
        "date": "2026-10-08",
        "client": "fake",
        "complete": True,
        "stopped_reason": None,
        "n": 1,
        "n_planned": 1,
        "dataset": {"unit": "page", "manifest_sha256": "0" * 64},
        "pipeline_version": "x",
        "metrics": metrics,
        "cost": {"total_usd": 0.0, "per_item_usd": 0.0, "per_item_unit": "page", "model_calls": 0},
        "latency_ms": {"p50": None, "p95": None},
    }
    text = pe.format_markdown(report)
    assert "### Unmatched predicted equations" in text
    assert "| prose_math | 1 |" in text and "| split_gold_equations | 0 |" in text


def test_extras_the_text_layer_supports_are_counted_apart_from_fragments_and_the_rest():
    """T2-13: prose maths the model read right (its symbols agree with the PDF's own text)
    is not a detection error of the pipeline; a fragment agrees with its own text trivially
    and an unread or misread extra is not supported."""
    gold = [equation("p-1", 0, DISPLACEMENT, "block")]
    predicted = [
        element(0, "Δ\U0001d41d = \U0001d41df − \U0001d41d0 ."),  # matched
        element(1, "\U0001d461= 0 s", "inline", parent="p1-e10"),  # prose maths, read right
        element(2, "\U0001d465= 3 m", "inline", parent="p1-e10"),  # prose maths, misread
        element(3, "/h)] = 40", "inline", parent="p1-e10"),  # a fragment, read right
        element(4, "\U0001d466= 5 m", "inline", parent="p1-e10"),  # prose maths, not read
    ]
    fixes = {
        "p1-e0": eq_fix("p1-e0", r"\Delta \mathbf{d} = \mathbf{d}_f - \mathbf{d}_0"),
        "p1-e1": eq_fix("p1-e1", r"t = 0\ \text{s}"),
        "p1-e2": eq_fix("p1-e2", r"x = 8\ \text{m}"),
        "p1-e3": eq_fix("p1-e3", r"/h)] = 40"),
    }
    item = page(gold, predicted, fixes)
    supported = {u["element_id"]: u["text_layer_supported"] for u in item["unmatched"]}
    assert supported == {"p1-e1": True, "p1-e2": False, "p1-e3": False, "p1-e4": False}
    metrics = pe.document_metrics([item])
    assert metrics["unmatched_predicted"] == 4
    assert metrics["unmatched_text_layer_supported"] == 1
    assert metrics["unmatched_text_layer_supported_by_reason"]["prose_math"] == 1
    assert metrics["unmatched_not_supported"] == 3
    assert metrics["detection_precision"] == 1 / 5
    assert metrics["detection_precision_excluding_text_layer_supported"] == 1 / 4
