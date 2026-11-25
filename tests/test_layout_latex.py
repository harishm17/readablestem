"""layout.analyze on real LaTeX output: tests/fixtures/layout/latex/*.pdf.

Four lecture-note documents written for these tests and compiled with Tectonic
(`make_pdfs.py` in that folder): article-class notes, a physics problem set, beamer
slides and a two-column handout, in Computer Modern (CMR, CMMI, CMSY, CMEX, CMBX) and
beamer's Latin Modern Sans. They cover what TeX does differently from the Chromium
fixtures: equation numbers at the right margin, align environments, inline integrals
set on a baseline of their own, limits under a sum, matrices, bold vectors, the
negation slash of \\neq, a cases brace, absolute-value bars, nested itemize/enumerate
with TeX's bullets and dashes, beamer's triangle bullets and sans-serif maths, a
two-column page and a TikZ figure. A fifth page, vectors_relations, holds signs TeX
draws from pieces (arrows over points, \\cong, \\models) for tests/test_sign_check.py.
A sixth, sum_limits, holds sums and a product with limits over and under the sign, and
pdftex/sum_limits_pdftex holds five more as pdfTeX sets them (built with pdfTeX: its header
says why and how).
"""

from __future__ import annotations

import unicodedata
from functools import cache
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind, inline_marker
from readablestem.pipeline.layout import Layout, analyze, heading_levels

LATEX = Path(__file__).parent / "fixtures" / "layout" / "latex"
NAMES = (
    "calculus_notes",
    "problem_set",
    "kinematics_slides",
    "linear_algebra_twocol",
    "vectors_relations",
)


@cache
def load(name: str) -> Layout:
    return analyze((LATEX / f"{name}.pdf").read_bytes())


def plain(text: str | None) -> str:
    """NFKC (bold maths letters become plain ones) with all whitespace removed."""
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def outline(layout: Layout) -> list[tuple[int, str]]:
    levels = heading_levels(layout.elements)
    return [(levels[e.id], e.text or "") for e in layout.elements if e.kind == Kind.HEADING]


def blocks(layout: Layout) -> list[Element]:
    return [e for e in layout.elements if e.kind == Kind.EQUATION and e.display == "block"]


def items(layout: Layout) -> list[tuple[str | None, bool | None, int | None]]:
    return [
        (e.list_marker, e.list_ordered, e.list_level)
        for e in layout.elements
        if e.kind == Kind.LIST_ITEM
    ]


def children(layout: Layout, parent: Element) -> list[Element]:
    return [e for e in layout.elements if e.parent_id == parent.id]


def number_rect_of(name: str, page: int, text: str) -> pymupdf.Rect:
    """Where `text` is printed on `page` (its first occurrence)."""
    with pymupdf.open(LATEX / f"{name}.pdf") as doc:
        found = doc[page].search_for(text)
    assert found, text
    return found[0]


def number_rect(name: str, equation: Element) -> pymupdf.Rect:
    """Where the equation's number is printed: the occurrence of its text level with the
    equation (not a reference to it in the running text)."""
    with pymupdf.open(LATEX / f"{name}.pdf") as doc:
        found = doc[equation.page].search_for(equation.equation_label or "")
    middle = (equation.bbox[1] + equation.bbox[3]) / 2
    assert found, equation.equation_label
    return min(found, key=lambda r: abs((r.y0 + r.y1) / 2 - middle))


@pytest.mark.parametrize("name", NAMES)
def test_fixtures_are_small_and_well_formed(name):
    assert (LATEX / f"{name}.pdf").stat().st_size < 300_000
    layout = load(name)
    elements = layout.elements
    assert [e.order for e in elements] == list(range(len(elements)))
    assert len({e.id for e in elements}) == len(elements)
    for e in elements:
        if e.kind == Kind.EQUATION and e.display == "inline":
            parent = next(p for p in elements if p.id == e.parent_id)
            assert parent.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM, Kind.FIGURE)
            holder = parent.caption_marked if parent.kind is Kind.FIGURE else parent.text
            assert (holder or "").count(inline_marker(e.id)) == 1
        if e.kind == Kind.LIST_ITEM:
            assert e.list_marker and e.list_ordered is not None and e.list_level
            assert not (e.text or "").startswith(e.list_marker)


# ---------------------------------------------------------------- calculus notes (article)


def test_calculus_notes_outline():
    assert outline(load("calculus_notes")) == [
        (1, "1 Derivatives"),
        (2, "1.1 Rules for derivatives"),
        (1, "2 Integrals"),
        (2, "2.1 The fundamental theorem"),
        (2, "2.2 Exercises"),
        (1, "3 Continuity"),
    ]


def test_calculus_notes_numbered_equations_keep_their_numbers_out_of_the_crop():
    layout = load("calculus_notes")
    equations = blocks(layout)
    assert [e.equation_label for e in equations] == ["(1)", "(2)", "(3)", None, "(4)", None]
    for equation in equations:
        if equation.equation_label:
            number = number_rect("calculus_notes", equation)
            assert equation.bbox[2] < number.x0  # crop = bbox + 2 pt padding
            assert equation.equation_label not in (equation.text or "")
    definition, product, chain, theorem, riemann, _ = equations
    assert "fraction bar" in definition.reasons and "lim" in (definition.text or "")
    # Each row of the numbered align is its own equation; both rows are whole equations.
    assert "uv" in plain(product.text) and "g(x)" in plain(chain.text)
    assert plain(theorem.text).startswith("\u222bab")
    # The sum's lower limit "i = 1" is part of the equation, not a stray equation.
    assert "\u2211" in (riemann.text or "") and "i=1" in plain(riemann.text)
    assert "\u222b" in (riemann.text or "")


def test_a_sign_that_took_its_upper_limit_keeps_its_place_and_the_line_its_number():
    # sum_limits.tex, with the switches production runs. The product of "p(x0) = int prod_{t=1}^T
    # p_t(...) dx" took its limits and was then longer than a fragment may be: it stayed a row
    # of its own and was read after the line, and the integral with it. And the line of the
    # numbered sum on the left, taller with the limit in it, lost its "(1)" to a paragraph:
    # the number was refused because the line is level with the numbered equation of the
    # other column. And on the second page, which has no line of running text, "Etot = sum ei
    # + Ereg" with the "n" in it counted as a line of script size: its roman subscripts were
    # words of a sentence and the display a paragraph with three pieces of maths. The same
    # line as an item of a list lost the size it was given when its marker was cut off it:
    # "Ekin" and "Epot" were then a letter each and a subscript left in the item.
    # Not repaired: the "i=1" under the sum of the list item is still an equation of its own
    # after the item (it is among the blocks left unnamed below).
    names = ("display_grouping", "inline_joins", "region_tree", "join_panels")
    layout = analyze((LATEX / "sum_limits.pdf").read_bytes(), **dict.fromkeys(names, True))
    total, product, entropy, energy, *_ = blocks(layout)
    [kinetic] = children(layout, next(e for e in layout.elements if e.kind == Kind.LIST_ITEM))
    assert (total.equation_label, entropy.equation_label) == ("(1)", "(2)")
    assert plain(product.text).startswith("p(x0)=\u222b\u220fT")
    for display, first, last in ((energy, "Etot", "Ereg"), (kinetic, "Ekin", "Epot")):
        assert plain(display.text).startswith(f"{first}=\u2211") and last in plain(display.text)


def test_limits_as_pdftex_sets_them_leave_each_display_whole_and_in_order():
    # pdftex/sum_limits_pdftex.tex, with the switches production runs: five displays, each a
    # shape of a real paper, each wrong when one condition of the limits rule is taken out.
    names = ("display_grouping", "inline_joins", "region_tree", "join_panels")
    page = (LATEX / "pdftex" / "sum_limits_pdftex.pdf").read_bytes()
    short, bracket, fraction, tall, root = (
        plain(e.text) for e in blocks(analyze(page, **dict.fromkeys(names, True)))
    )
    # The limit under a short sum is in its display: the line counts as script size (three
    # subscripted letters side by side), and the limit was an equation of its own.
    assert short.startswith("dij=\u2211n") and short.endswith("k=1")
    # A bracket and a sum in one row with no limit in it stay where the row is: placed by the
    # sign, the right-hand side was read before the left.
    assert bracket.startswith("hL\u03b2SE(v):=1")
    # The sum of a numerator stays in the numerator, not beside the sum of the denominator.
    assert "\u2211n\u2212hj=n" in fraction and "\u2211n\u2212h\u2211" not in fraction
    # The limit under the sum of one line is no upper limit of the sum on the next: the
    # display was cut in two after its first sum sign.
    assert tall.startswith("E(") and "\u2211|u|=k" in tall and "|u|=|v|=k" in tall
    # A root takes no limits: it stays under its bar, not between the terms over it.
    assert "V1(u)\u2212\u03ba" in root and root.endswith("\u221atn")


def test_calculus_notes_nested_itemize_and_enumerate():
    layout = load("calculus_notes")
    assert items(layout) == [
        ("\u2022", False, 1),
        ("\u2022", False, 1),
        ("\u2013", False, 2),
        ("\u2013", False, 2),
        ("\u2022", False, 1),
        ("1.", True, 1),
        ("2.", True, 1),
        ("(a)", True, 2),
        ("(b)", True, 2),
        ("3.", True, 1),
    ]
    texts = [e.text for e in layout.elements if e.kind == Kind.LIST_ITEM]
    assert texts[1] == "the product rule, which also works"
    assert texts[2] == "for three factors at once, and"


def test_calculus_notes_inline_integrals_include_their_integral_sign():
    """TeX sets the sign of $\\int_0^1 x^2\\,dx$ on a baseline of its own; it used to come
    out as a display equation of its own, leaving the inline crop without it."""
    layout = load("calculus_notes")
    exercise = next(e for e in layout.elements if (e.text or "").startswith("Compute"))
    [integral] = children(layout, exercise)
    assert plain(integral.text).startswith("\u222b") and "x2dx" in plain(integral.text)
    assert not any(plain(e.text) in ("\u222b1", "\u222b\u03c0", "i=1") for e in blocks(layout))


def test_calculus_notes_figure_keeps_its_axis_labels_and_caption():
    layout = load("calculus_notes")
    page_two = [e for e in layout.elements if e.page == 1]
    assert [e.kind for e in page_two if e.kind == Kind.FIGURE] == [Kind.FIGURE]
    figure = page_two[0]
    assert (figure.caption or "").startswith("Figure 1: The area under")
    assert sorted((figure.text or "").split()) == ["1", "x", "y"]


def test_calculus_notes_cases_brace_is_one_equation():
    """The middle row of a cases brace sits on the baseline of "sgn(x) =", and the rows
    above and below it are whole equations of their own ("1  x > 0"): the brace used to
    come out as two overlapping display equations, each cropped and sent on its own."""
    layout = load("calculus_notes")
    on_page_two = [e for e in blocks(layout) if e.page == 1]
    assert len(on_page_two) == 1
    sign = on_page_two[0]
    assert sign.equation_label is None
    for part in ("sgn(x)=", "1x>0", "0x=0", "\u22121x<0"):
        assert part in plain(sign.text)
    top_row = number_rect_of("calculus_notes", sign.page, "x > 0")
    bottom_row = number_rect_of("calculus_notes", sign.page, "x < 0")
    assert sign.bbox[1] <= top_row.y0 + 1 and sign.bbox[3] >= bottom_row.y1 - 1


def test_calculus_notes_absolute_values_keep_both_bars():
    """A leading "|" was trimmed as an infix-only operator: "|r| < 1" became "r| < 1"
    with the opening bar left in the paragraph text and outside the crop."""
    layout = load("calculus_notes")
    paragraph = next(e for e in layout.elements if (e.text or "").startswith("The geometric"))
    values = [plain(e.text) for e in children(layout, paragraph)]
    assert values[1:] == ["|r|<1", "|x|"]
    assert "|" not in (paragraph.text or "")


# ---------------------------------------------------------------- problem set (article)


def test_problem_set_headings_lists_and_equations():
    layout = load("problem_set")
    assert [t for _, t in outline(layout)] == [
        "Problem Set 2: Kinematics",
        "Problem 1",
        "Problem 2",
        "Problem 3",
    ]
    assert [e.list_marker for e in layout.elements if e.kind == Kind.LIST_ITEM] == [
        *("(a)", "(b)", "(c)"),
        *("A.", "B.", "C.", "D."),
        *("(i)", "(ii)"),
    ]
    assert {(o, lv) for _, o, lv in items(layout)} == {(True, 1)}
    equations = blocks(layout)
    assert [e.equation_label for e in equations] == ["(2.1)", None, "(2.2)", "(2.3)"]
    derivation = equations[1]  # align*: three rows, one equation
    assert any(r.endswith("lines merged") for r in derivation.reasons)
    assert plain(derivation.text).startswith("v2=") and "72m2/s2" in plain(derivation.text)
    assert "radical sign" in equations[3].reasons
    for equation in (equations[0], equations[2], equations[3]):
        number = number_rect("problem_set", equation)
        assert equation.bbox[2] < number.x0


def test_problem_set_options_and_a_decimal_sentence():
    layout = load("problem_set")
    option = next(e for e in layout.elements if e.list_marker == "B.")
    [value] = children(layout, option)
    assert option.text == inline_marker(value.id) and plain(value.text) == "8.5m/s"
    last = layout.elements[-1]
    assert last.kind == Kind.PARAGRAPH  # "2.5 metres is ..." is not a list item
    assert last.text == "2.5 metres is the height of the classroom ceiling, for comparison."


# ---------------------------------------------------------------- slides (beamer)


def test_beamer_slides_titles_triangle_bullets_and_levels():
    layout = load("kinematics_slides")
    assert [t for _, t in outline(layout)] == [
        "Motion in One Dimension",
        "Constant Acceleration",
        "Velocity Graph",
    ]
    assert items(layout) == [
        ("\u25b6", False, 1),
        ("\u25b6", False, 1),
        ("\u25b6", False, 2),
        ("\u25b6", False, 2),
        ("\u25b6", False, 1),
    ]


def test_beamer_align_in_sans_serif_maths_is_one_display_equation():
    """Beamer sets maths letters in Latin Modern Sans Oblique, a text font: "at" in
    v = v0 + at used to read as the English word and turn the rows into prose."""
    layout = load("kinematics_slides")
    equations = blocks(layout)
    assert len(equations) == 2
    assert plain(equations[0].text).startswith("v=v0+at,x=x0+v0t+")
    assert plain(equations[1].text).startswith("v2=v20+2a(x")
    figure = next(e for e in layout.elements if e.kind == Kind.FIGURE)
    assert (figure.caption or "").startswith("Figure: Velocity rises linearly")
    assert sorted((figure.text or "").split()) == ["t", "v"]


# ---------------------------------------------------------------- two-column handout


def test_two_column_handout_reads_the_left_column_first():
    layout = load("linear_algebra_twocol")
    assert [t for _, t in outline(layout)] == [
        "1 Vectors",
        "2 Matrices",
        "3 Eigenvalues",
        "4 Projections",
    ]
    projections = next(e for e in layout.elements if e.text == "4 Projections")
    left = [e for e in layout.elements if e.order < projections.order and e.parent_id is None]
    assert all(e.bbox[2] < 306 for e in left)  # nothing from the right column before it
    right = [e for e in layout.elements if e.order > projections.order and e.parent_id is None]
    assert right and all(e.bbox[0] > 306 for e in right)


def test_two_column_handout_equations_matrices_and_numbers():
    layout = load("linear_algebra_twocol")
    equations = blocks(layout)
    assert [e.equation_label for e in equations] == [
        *("(1.1)", "(1.2)", "(2.1)", "(2.2)", "(3.1)", "(4.1)"),
    ]
    dot, cross, matrix, system, polynomial, projection = equations
    assert plain(polynomial.text).startswith("\u03bb2\u22125\u03bb\u22122=0")
    assert "\U0001d42e" in (dot.text or "") and "\u2211" in (dot.text or "")  # bold u, sum
    assert "\U0001d6da" in (cross.text or "")  # bold omega (\bm, CMMIB)
    assert "1 2" in (matrix.text or "") and "3 4" in (matrix.text or "")
    assert all(n in plain(system.text) for n in "213135")
    # both rows of the system's matrix: the second lies inside the box of the first row's
    # tall bracket, and was left beside the equation as a paragraph of its own
    assert "2 1 1 3" in (system.text or "")
    assert "proj" in (projection.text or "") and "fraction bar" in projection.reasons
    for equation in equations:
        number = number_rect("linear_algebra_twocol", equation)
        assert equation.bbox[2] < number.x0


def test_two_column_handout_inline_maths():
    layout = load("linear_algebra_twocol")
    inline = [plain(e.text) for e in layout.elements if e.display == "inline"]
    assert "detA\u22600" in inline  # \neq: a negation slash over "=", joined into one sign
    # The last line of a sentence that is only maths is the end of the sentence, inline.
    assert "x=A\u22121b" in inline
    assert not any(plain(e.text).startswith("x=A") for e in blocks(layout))
    assert "ATA\u02c6x=ATb" in inline  # the hat of x-hat does not cut the formula
    assert "b\u2212projab" in inline  # \operatorname{proj} is maths, not an English word
    eigen = [e for e in layout.elements if e.kind == Kind.LIST_ITEM]
    assert [(e.list_marker, e.list_level) for e in eigen] == [
        ("(i)", 1),
        ("(ii)", 1),
        ("(iii)", 1),
        ("\u2022", 1),
        ("\u2022", 1),
        ("\u2022", 1),
    ]


def test_two_column_handout_records_which_letters_are_known_plain():
    """Element.regular_letters: letters in a regular font with a bold face (CMMI), so
    the equation step can tell a scalar made bold; MSBM's double-struck R and the bold
    CMMIB/CMBX vectors are not in it."""
    layout = load("linear_algebra_twocol")
    by_text = {plain(e.text): e for e in layout.elements if e.kind == Kind.EQUATION}
    assert by_text["v∈Rn"].regular_letters == "n"  # bold v in R^n: R is MSBM
    assert by_text["\u03c9"].regular_letters is None  # bold omega alone
    assert by_text["Ax=b"].regular_letters == "A"  # bold x and b


def test_a_picture_environment_drawing_is_one_figure_and_a_font_named_for_a_line_is_text():
    """A slanted line of LaTeX's picture environment is glyphs of the font line10 (F-550): it
    was a paragraph of quote marks beside seven equations, with no figure. The glyphs are
    strokes of the page, so the triangle is one figure that takes its caption. A font whose
    name only contains "line10" is not a drawing font."""
    layout = load("picture_environment")
    figures = [e for e in layout.elements if e.kind == Kind.FIGURE]
    assert len(figures) == 1
    assert (figures[0].caption or "").startswith("Figure 1")
    assert not any('""' in (e.text or "") for e in layout.elements)

    with pymupdf.open() as doc:
        page = doc.new_page()
        xref = page.insert_font(fontname="helv")
        doc.xref_set_key(xref, "BaseFont", "/Underline10")
        words = "The slope of the ramp is found from its rise and its run. " * 2
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 200), words, fontname="helv", fontsize=11)
        text = [e for e in analyze(doc.tobytes()).elements if e.kind == Kind.PARAGRAPH]
    assert len(text) == 1 and "slope of the ramp" in (text[0].text or "")
