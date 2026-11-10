"""The synthetic scans and the prose word error rate of the image page evaluation (ADR 0007)."""

from __future__ import annotations

import random

import pytest

from readablestem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)
from readablestem.eval import prose_wer

# ----------------------------------------------------------------------------- scans


# ------------------------------------------------------------------------------ words


def test_gold_words_keep_or_leave_out_the_mathematics_and_always_the_markup_and_footer():
    html = (
        "<html><head><title>T</title><style>p{}</style></head><body><main>"
        "<h2>Speed</h2><p>The speed <math><semantics><mi>v</mi><mo>=</mo><mn>5</mn>"
        "<annotation>v=5</annotation></semantics></math> is 10<sup>3</sup> "
        "<b>constant</b>.</p><ul><li>one</li><li>two</li></ul>"
        "<table><tr><td>a</td><td>b</td></tr></table></main>"
        "<footer><p>Access for free</p></footer></body></html>"
    )
    assert prose_wer.gold_words(html) == [
        "speed", "the", "speed", "v", "5", "is", "10", "3", "constant", "one", "two", "a", "b",
    ]  # fmt: skip
    assert prose_wer.gold_words(html, include_math=False) == [
        "speed", "the", "speed", "is", "10", "constant", "one", "two", "a", "b",
    ]  # fmt: skip
    assert prose_wer.prose_only(["speed", "is", "10", "3", "kg", "constant"]) == [
        "speed",
        "constant",
    ]


def naive(a: list[str], b: list[str]) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        new = [i]
        for j, y in enumerate(b, 1):
            new.append(min(row[j] + 1, new[j - 1] + 1, row[j - 1] + (x != y)))
        row = new
    return row[-1]


def test_the_bit_parallel_distance_is_the_plain_one():
    rng = random.Random(5)
    vocabulary = ["a", "b", "c", "d", "e", "f"]
    for _ in range(300):
        a = [rng.choice(vocabulary) for _ in range(rng.randint(0, 40))]
        b = [rng.choice(vocabulary) for _ in range(rng.randint(0, 40))]
        assert prose_wer.edit_distance(a, b) == naive(a, b), (a, b)


def test_the_word_error_rate_counts_substitutions_deletions_and_insertions():
    gold = prose_wer.words("the speed of the cart is five")
    score = prose_wer.score(gold, prose_wer.words("the speed of a cart is five metres"))
    assert (score.errors, score.gold_words, score.predicted_words) == (2, 7, 8)
    assert score.wer == pytest.approx(2 / 7)
    assert prose_wer.score([], []).wer is None
    pooled = prose_wer.pooled([score.as_dict(), prose_wer.score(["a"] * 3, ["a"] * 3).as_dict()])
    assert pooled["errors"] == 2 and pooled["gold_words"] == 10 and pooled["wer"] == 0.2


# ------------------------------------------------------------------------- prediction


def element(eid: str, kind: Kind, order: int, **kw) -> Element:
    return Element(id=eid, kind=kind, page=0, bbox=(0, 0, 1, 1), order=order, **kw)


def test_the_predicted_words_are_the_prose_in_reading_order_with_or_without_formulas():
    elements = [
        element("p1-e1", Kind.PARAGRAPH, 2, text="The speed \u27e6p1-e9\u27e7 is constant."),
        element("p1-e0", Kind.HEADING, 1, text="Old heading"),
        element("p1-e2", Kind.FIGURE, 3, caption="Figure 1 A cart"),
        element("p1-e3", Kind.TABLE, 4, caption="Table 1 Speeds"),
        element(
            "p1-e4",
            Kind.PARAGRAPH,
            5,
            text="Access for free at openstax.org. Physics (OpenStax), CC BY 4.0. Page 3 of 14",
        ),
        element("p1-e9", Kind.EQUATION, 6, display="inline", parent_id="p1-e1"),
        element("p1-e8", Kind.EQUATION, 7, display="block"),
    ]
    grid = TableGrid(
        rows=1,
        cols=2,
        cells=[
            TableCell(row=0, col=0, text="t"),
            TableCell(row=0, col=1, text="9", latex="9", mathml="<math><mn>9</mn></math>"),
        ],
    )
    inline = EquationFix(latex="v", mathml="<math><mi>v</mi></math>", speech="v", display="inline")
    block = EquationFix(
        latex="a=b", mathml="<math><mi>a</mi><mo>=</mo><mi>b</mi></math>", speech=""
    )
    fixes = [
        Fix(
            element_id="p1-e0",
            kind=Kind.HEADING,
            status=Status.ACCEPTED,
            heading=HeadingFix(level=1, text="Motion"),
        ),
        Fix(
            element_id="p1-e3",
            kind=Kind.TABLE,
            status=Status.NEEDS_REVIEW,
            table=TableFix(grid=grid),
        ),
        Fix(element_id="p1-e9", kind=Kind.EQUATION, status=Status.ACCEPTED, equation=inline),
        Fix(element_id="p1-e8", kind=Kind.EQUATION, status=Status.ACCEPTED, equation=block),
    ]
    result = DocumentResult(
        pipeline_version="t",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
    )
    assert prose_wer.predicted_words(result) == [
        "motion", "the", "speed", "v", "is", "constant", "figure", "1", "a", "cart",
        "t", "9", "table", "1", "speeds", "a", "b",
    ]  # fmt: skip
    assert prose_wer.predicted_words(result, include_math=False) == [
        "motion", "the", "speed", "is", "constant", "figure", "1", "a", "cart", "t", "table",
        "1", "speeds",
    ]  # fmt: skip


def test_the_bag_of_words_scores_recall_and_precision_without_order():
    score = prose_wer.bag_score(
        ["the", "speed", "of", "the", "cart"], ["cart", "the", "speed", "x"]
    )
    assert score == {"gold_words": 5, "predicted_words": 4, "matched": 3}
    pooled = prose_wer.bag_pooled([score, {"gold_words": 5, "predicted_words": 6, "matched": 5}])
    assert pooled["recall"] == 0.8 and pooled["precision"] == 0.8 and pooled["sections"] == 2
    assert prose_wer.bag_pooled([])["recall"] is None
