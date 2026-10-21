# ruff: noqa: RUF001
"""The "symbols" scorer (secondary): tables flattened, scripts as markers. What it forgives
(the concrete cases of the held-out analysis), what it must not, and that it sits beside the
unchanged primary scorer and stream v2."""

from __future__ import annotations

import pytest

from readablestem.eval import equations_symbols as sym
from readablestem.eval import equations_v2 as v2
from readablestem.eval import pipeline_eval as pe
from readablestem.eval.equations import latex_to_mathml, score_latex
from test_eval_support import eq_fix, equation


def mml(body: str) -> str:
    return f"<math>{body}</math>"


def same(pred: str, gold: str) -> bool:
    return sym.score_symbols(mml(pred), mml(gold)).exact


# ----------------------------------------------------------------------- chemistry


GOLD_H3O = (
    "<msub><mrow><mo>[</mo><msub><mtext>H</mtext><mn>3</mn></msub>"
    "<msup><mtext>O</mtext><mtext>+</mtext></msup><mo>]</mo></mrow><mn>0</mn></msub>"
    "<mo>=</mo><mn>0.100</mn><mi>M</mi>"
)


def test_a_subscript_on_a_bracketed_group_equals_one_on_the_closing_bracket():
    """OpenStax (Chemistry 14.7, [H3O+]_0): the subscript hangs on the whole group; a
    transcription hangs it on the closing bracket. Stream v2 keeps the difference, the
    primary scorer too; the symbols are the same."""
    pred = r"[\text{H}_3\text{O}^+]_0 = 0.100\ M"
    assert sym.score_symbols_latex(pred, mml(GOLD_H3O)).exact
    assert not v2.score_stream(latex_to_mathml(pred), mml(GOLD_H3O)).exact
    assert not score_latex(pred, mml(GOLD_H3O)).exact_lenient


def test_the_charge_as_text_or_operator_is_the_same_symbol():
    assert same(
        "<msup><mtext>OH</mtext><mtext>−</mtext></msup>",
        "<msup><mi>OH</mi><mrow><mo>-</mo></mrow></msup>",
    )
    assert not same(
        "<msup><mtext>OH</mtext><mtext>−</mtext></msup>",
        "<msup><mtext>OH</mtext><mtext>+</mtext></msup>",
    )


def test_a_wrong_charge_or_subscript_is_still_wrong():
    assert not same(GOLD_H3O, GOLD_H3O.replace("<mn>3</mn>", "<mn>2</mn>"))
    assert not same(GOLD_H3O, GOLD_H3O.replace("<mn>0</mn></msub><mo>=", "<mn>1</mn></msub><mo>="))


# ------------------------------------------------------------- packing and token type


def test_a_signed_number_is_the_same_packed_or_not():
    assert same("<mo>-</mo><mn>5</mn>", "<mn>-5</mn>")
    assert same("<mo>−</mo><mn>5</mn>", "<mtext>−5</mtext>")
    assert not same("<mn>5</mn>", "<mn>-5</mn>")  # a dropped sign is a different number


def test_mo_mtext_mi_mn_are_one_token_type():
    assert same("<mtext>=</mtext>", "<mo>=</mo>")
    assert same("<mi>2</mi><mo>+</mo><mtext>x</mtext>", "<mn>2</mn><mtext>+</mtext><mi>x</mi>")
    assert same("<mtext>time =</mtext>", "<mtext>time</mtext><mo>=</mo>")


def test_bars_and_angle_brackets_with_other_code_points_are_the_same_symbol():
    assert same("<mo>∣</mo><mi>x</mi><mo>∣</mo>", "<mo>|</mo><mi>x</mi><mo>|</mo>")
    assert same("<mo>〈</mo><mi>x</mi><mo>〉</mo>", "<mo>⟨</mo><mi>x</mi><mo>⟩</mo>")


# ------------------------------------------------------------------------ tables


def cell(body: str = "") -> str:
    return f"<mtd>{body}</mtd>"


def row(*cells: str) -> str:
    return "<mtr>" + "".join(cells) + "</mtr>"


def test_matrix_with_spacer_columns_equals_the_plain_matrix():
    """OpenStax writes a spacer cell between a matrix's columns."""
    gold = (
        "<mo>[</mo><mtable>"
        + row(cell("<mn>1</mn>"), cell(), cell("<mn>2</mn>"))
        + row(cell("<mn>3</mn>"), cell(), cell("<mn>4</mn>"))
        + "</mtable><mo>]</mo>"
    )
    pred = (
        "<mo>[</mo><mtable>"
        + row(cell("<mn>1</mn>"), cell("<mn>2</mn>"))
        + row(cell("<mn>3</mn>"), cell("<mn>4</mn>"))
        + "</mtable><mo>]</mo>"
    )
    assert same(pred, gold)
    # a wrong entry is still wrong
    assert not same(pred.replace("<mn>4</mn>", "<mn>5</mn>"), gold)
    # and a reordered one
    assert not same(
        pred.replace("<mn>1</mn>", "<mn>9</mn>").replace("<mn>2</mn>", "<mn>1</mn>"), gold
    )


def test_aligned_system_one_cell_per_row_equals_the_aligned_array():
    """The answer key puts each line of a system in one cell; a transcription with `&` puts
    the left side, the sign and the right side in cells of their own."""
    gold = (
        "<mtable>"
        + row(cell("<mi>x</mi><mo>=</mo><mn>2</mn>"))
        + row(cell("<mi>y</mi><mo>=</mo><mn>3</mn>"))
        + "</mtable>"
    )
    pred = (
        "<mtable>"
        + row(cell("<mi>x</mi>"), cell("<mo>=</mo><mn>2</mn>"))
        + row(cell("<mi>y</mi>"), cell("<mo>=</mo><mn>3</mn>"))
        + "</mtable>"
    )
    assert same(pred, gold)
    assert not v2.score_stream(mml(pred), mml(gold)).exact
    assert not same(pred.replace("<mn>3</mn>", "<mn>4</mn>"), gold)


def test_a_table_is_equal_to_the_same_lines_without_a_table_only_in_this_scorer():
    lines = "<mi>x</mi><mo>=</mo><mn>2</mn><mi>y</mi><mo>=</mo><mn>3</mn>"
    table = (
        "<mtable>"
        + row(cell("<mi>x</mi><mo>=</mo><mn>2</mn>"))
        + row(cell("<mi>y</mi><mo>=</mo><mn>3</mn>"))
        + "</mtable>"
    )
    assert same(lines, table)  # the price of the flattening, documented in the module
    assert not v2.score_stream(mml(lines), mml(table)).exact


# ---------------------------------------------------------- what must stay different


def test_structure_that_changes_the_reading_is_kept():
    frac = "<mfrac><mi>a</mi><mi>b</mi></mfrac>"
    assert not same(frac, "<mi>a</mi><mo>/</mo><mi>b</mi>")
    assert not same(
        "<msup><mi>x</mi><mrow><mi>a</mi><mi>b</mi></mrow></msup>",
        "<msup><mi>x</mi><mi>a</mi></msup><mi>b</mi>",
    )
    assert not same("<msqrt><mi>a</mi></msqrt><mi>b</mi>", "<msqrt><mi>a</mi><mi>b</mi></msqrt>")
    assert not same("<msub><mi>x</mi><mi>a</mi></msub>", "<msup><mi>x</mi><mi>a</mi></msup>")
    assert not same("<mfrac><mn>1</mn><mn>2</mn></mfrac>", "<mfrac><mn>2</mn><mn>1</mn></mfrac>")


def test_the_degree_sign_and_primes_as_scripts_are_inline():
    assert same("<msup><mn>90</mn><mo>∘</mo></msup>", "<mn>90</mn><mo>°</mo>")
    assert same("<msup><mi>f</mi><mo>′</mo></msup>", "<mi>f</mi><mo>'</mo>")


def test_an_empty_group_or_wrapper_row_changes_nothing():
    assert same(
        "<mrow><mrow><mi>x</mi></mrow></mrow><mo>=</mo><mn>1</mn>", "<mi>x</mi><mo>=</mo><mn>1</mn>"
    )


def test_an_unparseable_prediction_scores_zero_with_the_error():
    score = sym.score_symbols("<math><mi>x</mi>", mml("<mi>x</mi>"))
    assert not score.exact and score.similarity == 0.0 and score.error


def test_no_proposal_is_wrong():
    score = sym.missing_symbols(mml("<mi>x</mi>"))
    assert not score.exact and score.error == "no proposal"


def test_similarity_counts_atoms():
    score = sym.score_symbols(
        mml("<mi>a</mi><mo>+</mo><mi>b</mi>"), mml("<mi>a</mi><mo>+</mo><mi>c</mi>")
    )
    assert not score.exact
    assert score.similarity == pytest.approx(2 / 3)


def test_split_and_merge_credit_work_on_these_streams():
    gold = sym.symbols_of(
        mml("<mi>x</mi><mo>=</mo><mn>1</mn><mo>,</mo><mi>y</mi><mo>=</mo><mn>2</mn>")
    )
    a = sym.symbols_of(mml("<mi>x</mi><mo>=</mo><mn>1</mn>"))
    b = sym.symbols_of(mml("<mi>y</mi><mo>=</mo><mn>2</mn>"))
    assert v2.credit_split([a, b], gold)
    assert v2.credit_merged(gold, [a, b]) == 2


# ------------------------------------------------------------- beside the primary scorer


def test_every_stream_v2_exact_stays_exact_on_equations_without_tables_or_bracket_scripts():
    pairs = [
        (r"\frac{a}{b} + c", r"\frac{a}{b}+c"),
        (r"v_{\text{avg}} = \frac{\Delta d}{\Delta t}", r"v_{avg}=\frac{\Delta d}{\Delta t}"),
        (r"x^{2} - 5", r"x^2-5"),
        (r"\sqrt[3]{x}", r"\sqrt[3]{x}"),
    ]
    for pred, gold in pairs:
        assert v2.score_stream_latex(pred, latex_to_mathml(gold)).exact
        assert sym.score_symbols_latex(pred, latex_to_mathml(gold)).exact


def test_the_primary_item_fields_are_unchanged_and_the_symbols_ones_are_added():
    record = equation("p", 1, latex_to_mathml(r"\frac{a}{b}"), "block")
    item = pe.equation_item(record, eq_fix("g1", r"\frac{a}{b}"), pe.Settings())
    assert item["exact_lenient"] is True
    assert item["exact_symbols"] is True
    assert item["symbols_similarity"] == 1.0


def test_summary_rates_include_errors():
    scores = [
        sym.score_symbols(mml("<mi>x</mi>"), mml("<mi>x</mi>")),
        sym.missing_symbols(mml("<mi>x</mi>")),
    ]
    summary = sym.summarize(scores)
    assert summary["exact"] == 0.5
    assert summary["errors"] == 1
    assert summary["label"] == sym.LABEL


# --------------------------------------------------------------- the document task


def _element(order: int, text: str, display: str = "block", parent: str | None = None):
    from readablestem.core.types import Element, Kind

    return Element(
        id=f"p1-e{order}",
        kind=Kind.EQUATION,
        page=1,
        bbox=(0, 0, 10, 10),
        order=order,
        text=text,
        display=display,  # type: ignore[arg-type]
        parent_id=parent,
    )


def test_a_matched_pair_is_wrong_for_primary_and_v2_and_right_for_symbols():
    gold = [equation("p-1", 0, mml(GOLD_H3O), "inline")]
    fixes = {"p1-e0": eq_fix("p1-e0", r"[\text{H}_3\text{O}^+]_0 = 0.100\ M")}
    item = pe.document_page_item("p", gold, [_element(0, "[H3O+]0 = 0.100 M")], fixes, [], [])
    pair = item["pairs"][0]
    assert pair["exact_lenient"] is False
    assert pair["exact_stream_v2"] is False
    assert pair["exact_symbols"] is True
    metrics = pe.document_metrics([item])
    assert metrics["matched_exact_lenient"] == 0.0  # primary, unchanged
    secondary = metrics["secondary_symbols"]
    assert secondary["matched_exact"] == 1.0 and secondary["end_to_end_exact"] == 1.0
    assert "secondary" in secondary["label"]
    assert metrics["secondary_stream_v2"]["matched_exact"] == 0.0


def test_a_split_gold_equation_is_credited_in_the_symbols_scorer_too():
    gold = [
        equation(
            "p-1",
            0,
            '<math display="inline"><mi>x</mi><mo>=</mo><mn>2</mn><mo>+</mo><mi>y</mi></math>',
            "inline",
        )
    ]
    predicted = [_element(0, "x = 2", "inline", "p1-e5"), _element(1, "+ y", "inline", "p1-e5")]
    fixes = {"p1-e0": eq_fix("p1-e0", "x = 2"), "p1-e1": eq_fix("p1-e1", "+ y")}
    item = pe.document_page_item("p", gold, predicted, fixes, [], [])
    assert item["symbols_credited"]["split"] == [gold[0]["id"]]
    secondary = pe.document_metrics([item])["secondary_symbols"]
    assert secondary["end_to_end_exact"] == 0.0
    assert secondary["end_to_end_exact_with_credit"] == 1.0
