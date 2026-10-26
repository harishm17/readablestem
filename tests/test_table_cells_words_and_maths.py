"""A table cell of words with formulas in it is written as its words and a `<math>` for each
formula.

`pipeline.table_cells` cuts the LaTeX the table step gives for a cell at its `\\text{}` runs;
`table_html` writes the pieces. Found on the real-world development set
(docs/eval-equation-round2.md):
"Muon (mu)", "Array of the Transverse Momenta (p_T) of the muons" were one `<math>` holding a "
"sentence.
"""

from __future__ import annotations

from latex2mathml.converter import convert

from readablestem.core.types import TableCell, TableGrid
from readablestem.pipeline.table_cells import Piece, cell_pieces
from readablestem.pipeline.table_html import render_table


def test_a_symbol_in_brackets_after_a_word_is_text_math_text():
    assert cell_pieces(r"\text{Muon (}\mu\text{)}") == [
        Piece("text", "Muon ("),
        Piece("math", r"\mu"),
        Piece("text", ")"),
    ]


def test_brackets_round_a_whole_formula_and_the_sentence_end_belong_to_the_words():
    pieces = cell_pieces(r"\text{Momenta } (p_{T}) \text{ of the muons.}")
    assert pieces == [
        Piece("text", "Momenta ("),
        Piece("math", "p_{T}"),
        Piece("text", ") of the muons."),
    ]


def test_a_script_alone_is_maths_and_a_unit_in_words_stays_words():
    assert cell_pieces(r"\text{W Boson (W}^{\pm}\text{)}") == [
        Piece("text", "W Boson (W"),
        Piece("math", r"^{\pm}"),
        Piece("text", ")"),
    ]
    assert cell_pieces(r"\text{Value }2\text{ units}") is None  # a bare number is text


def test_a_cell_of_only_maths_or_only_words_is_not_cut():
    assert cell_pieces(r"1.5 \times 10^{-3}") is None
    assert cell_pieces(r"\text{only words}") is None
    assert cell_pieces(None) is None
    assert cell_pieces(r"\text{a \textbf{b}} x") is None  # a command in the words: whole


def _cell(latex: str, text: str = "") -> TableCell:
    mathml = convert(latex, display="inline")
    return TableCell(row=1, col=0, text=text, latex=latex, mathml=mathml)


def _grid(cell: TableCell, header: bool = False) -> TableGrid:
    head = TableCell(row=0, col=0, text="Particle", header="col")
    if header:
        cell = cell.model_copy(update={"row": 0, "header": "col"})
        return TableGrid(
            rows=2, cols=1, cells=[cell, TableCell(row=1, col=0, text="x")], header_rows=1
        )
    return TableGrid(rows=2, cols=1, cells=[head, cell], header_rows=1)


def test_the_html_has_the_words_as_text_and_each_formula_as_math():
    html = render_table(_grid(_cell(r"\text{Muon (}\mu\text{)}", "Muon (μ)")), id_prefix="t")
    td = html.split("<td")[1]
    assert td.count("<math") == 1
    assert ">Muon (<math" in td and "</math>)</td>" in td
    assert "mtext" not in td


def test_a_header_cell_of_words_and_maths_keeps_a_reading_for_screen_readers():
    html = render_table(
        _grid(_cell(r"\text{Mass (GeV/}c^{2}\text{)}", "Mass"), header=True), id_prefix="t"
    )
    th = html.split("<th ")[1]
    assert "visually-hidden" in th and 'aria-hidden="true"' in th


def test_a_cell_of_only_maths_is_written_whole_as_before():
    html = render_table(_grid(_cell(r"1.5 \times 10^{-3}", "1.5 x 10^-3")), id_prefix="t")
    td = html.split("<td")[1]
    assert td.count("<math") == 1 and td.count("</math>") == 1


def test_a_unit_with_a_script_on_its_text_is_left_whole():
    # `\\text{m/s}^2` is part of the formula; cutting at it leaves "g\\ (" and "^2)".
    assert cell_pieces(r"g\ (\text{m/s}^2)") is None


def test_a_cut_that_leaves_unbalanced_brackets_is_not_made():
    assert cell_pieces(r"\left( a \text{ and } b \right)") is None
