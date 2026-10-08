"""assemble.render_html: list items as nested <ul>/<ol>, and equation numbers."""

from __future__ import annotations

import re

import pytest

from a11y_stem.core.types import Element, EquationFix, Fix, Kind, Status, inline_marker
from a11y_stem.pipeline.assemble import render_html
from a11y_stem.pipeline.html_checks import axe_checks, static_checks

MATHML = '<math display="inline"><mi>x</mi><mo>=</mo><mn>5</mn></math>'


def item(index: int, marker: str, text: str, *, level: int = 1, ordered=None) -> Element:
    is_ordered = ordered if ordered is not None else marker not in ("\u2022", "\u2013", "\u25b6")
    return Element(
        id=f"p1-e{index}",
        kind=Kind.LIST_ITEM,
        page=0,
        bbox=(72, 100 + 15 * index, 300, 110 + 15 * index),
        order=index,
        text=text,
        list_marker=marker,
        list_ordered=is_ordered,
        list_level=level,
    )


def paragraph(index: int, text: str) -> Element:
    return Element(
        id=f"p1-e{index}",
        kind=Kind.PARAGRAPH,
        page=0,
        bbox=(72, 0, 300, 10),
        order=index,
        text=text,
    )


def equation(index: int, *, display: str = "block", parent: str | None = None, label=None):
    return Element(
        id=f"p1-e{index}",
        kind=Kind.EQUATION,
        page=0,
        bbox=(100, 0, 200, 20),
        order=index,
        text="x = 5",
        display=display,  # type: ignore[arg-type]
        parent_id=parent,
        equation_label=label,
    )


def accepted(element: Element, display: str = "block") -> Fix:
    mathml = MATHML.replace('display="inline"', f'display="{display}"')
    return Fix(
        element_id=element.id,
        kind=Kind.EQUATION,
        status=Status.ACCEPTED,
        confidence=0.95,
        equation=EquationFix(latex="x = 5", mathml=mathml, speech="x equals 5", display=display),  # type: ignore[arg-type]
    )


def body(elements: list[Element], fixes: list[Fix] | None = None) -> str:
    html = render_html(title="T", lang="en", elements=elements, fixes=fixes or [], crops={})
    main = html.split("<main>", 1)[1].split('<section id="about-conversion"', 1)[0]
    return "".join(line.strip() for line in main.splitlines())


def list_tags(html: str) -> str:
    """Only the list markup: <ul>, <ol ...>, <li>, their ends, and printed markers."""
    return "".join(
        re.findall(r"</?(?:ul|ol|li)\b[^>]*>|<span class=\"list-marker\">[^<]*</span>", html)
    )


# ---------------------------------------------------------------- ul / ol


def test_bullets_become_one_unordered_list_with_the_item_ids():
    html = body([item(0, "\u2022", "Measure the ramp."), item(1, "\u2022", "Time the cart.")])
    assert html == (
        '<ul><li id="p1-e0">Measure the ramp.</li><li id="p1-e1">Time the cart.</li></ul>'
    )


@pytest.mark.parametrize(
    ("markers", "open_tag"),
    [
        (["1.", "2.", "3."], "<ol>"),
        (["3.", "4."], '<ol start="3">'),
        (["a.", "b.", "c."], '<ol type="a">'),
        (["c.", "d."], '<ol type="a" start="3">'),
        (["A.", "B."], '<ol type="A">'),
        (["i.", "ii.", "iii."], '<ol type="i">'),
        (["iv.", "v."], '<ol type="i" start="4">'),
        (["h.", "i.", "j."], '<ol type="a" start="8">'),  # "i" between h and j is a letter
        (["IV.", "V."], '<ol type="I" start="4">'),
    ],
)
def test_regular_ordered_markers_use_the_browser_numbering(markers, open_tag):
    html = body([item(i, m, f"Item {i}") for i, m in enumerate(markers)])
    assert html.startswith(open_tag) and "list-marker" not in html
    assert html.count("<li") == len(markers)


@pytest.mark.parametrize(
    "markers",
    [
        ["(a)", "(b)", "(c)"],  # the browser cannot print brackets
        ["1)", "2)"],
        ["1.", "2.", "4."],  # a gap in the numbering
        ["a.", "B."],  # mixed case
    ],
)
def test_irregular_markers_are_printed_in_a_list_without_its_own_markers(markers):
    html = body([item(i, m, f"Item {i}") for i, m in enumerate(markers)])
    assert html.startswith('<ol class="printed-markers" role="list">')
    for marker in markers:
        assert f'<span class="list-marker">{marker}</span> ' in html


def test_nested_levels_nest_inside_the_item_before_them():
    elements = [
        item(0, "1.", "The power rule."),
        item(1, "2.", "The product rule:"),
        item(2, "\u2013", "for two factors,", level=2),
        item(3, "\u2013", "for three factors.", level=2),
        item(4, "3.", "The chain rule."),
    ]
    assert list_tags(body(elements)) == (
        '<ol><li id="p1-e0"></li><li id="p1-e1"><ul><li id="p1-e2"></li><li id="p1-e3"></li>'
        '</ul></li><li id="p1-e4"></li></ol>'
    )


def test_a_jump_of_two_levels_or_a_list_starting_deep_still_nests_correctly():
    elements = [
        item(0, "\u2022", "starts at level two", level=2),
        item(1, "\u2022", "jumps to level four", level=4),
        item(2, "\u2022", "back to two", level=2),
    ]
    assert list_tags(body(elements)) == (
        '<ul><li id="p1-e0"><ul><li id="p1-e1"></li></ul></li><li id="p1-e2"></li></ul>'
    )


def test_a_new_kind_of_marker_at_the_same_level_starts_a_new_list():
    elements = [
        item(0, "\u2022", "a bullet"),
        item(1, "1.", "a numbered step"),
        item(2, "2.", "another step"),
        item(3, "(a)", "a lettered part"),
    ]
    tags = list_tags(body(elements))
    assert tags.startswith('<ul><li id="p1-e0"></li></ul><ol><li id="p1-e1">')
    assert tags.endswith('<ol class="printed-markers" role="list"><li id="p1-e3"><span '
                         'class="list-marker">(a)</span></li></ol>')  # fmt: skip


def test_a_paragraph_ends_the_list_and_numbering_resumes_with_start():
    elements = [item(0, "1.", "First"), paragraph(1, "An aside."), item(2, "2.", "Second")]
    html = body(elements)
    assert html == (
        '<ol><li id="p1-e0">First</li></ol><p id="p1-e1">An aside.</p>'
        '<ol start="2"><li id="p1-e2">Second</li></ol>'
    )


def test_list_item_text_is_escaped_and_its_inline_equations_are_placed():
    eq = equation(1, display="inline", parent="p1-e0")
    elements = [item(0, "a.", f"<b>Find</b> {inline_marker(eq.id)} now"), eq, item(2, "b.", "x")]
    html = body(elements, [accepted(eq, "inline")])
    assert "&lt;b&gt;Find&lt;/b&gt;" in html
    assert '<li id="p1-e0">&lt;b&gt;Find&lt;/b&gt; <math display="inline" id="p1-e1">' in html
    assert html.count('id="p1-e1"') == 1  # placed in the item, not repeated after it
    assert html.count("<ol") == 1  # the item's equation does not split the list


def test_assembled_lists_pass_the_static_checks():
    elements = [
        item(0, "\u2022", "a"),
        item(1, "(i)", "b", level=2),
        item(2, "(ii)", "c", level=2),
        item(3, "\u2022", "d"),
    ]
    html = render_html(title="T", lang="en", elements=elements, fixes=[], crops={})
    assert all(c.passed for c in static_checks(html))


# ---------------------------------------------------------------- equation numbers


def test_an_equation_number_follows_its_maths_and_is_announced():
    eq = equation(0, label="(2.4)")
    html = body([eq], [accepted(eq)])
    assert html == (
        '<div class="equation equation-labelled" id="p1-e0"><math display="block">'
        "<mi>x</mi><mo>=</mo><mn>5</mn></math>"
        '<span class="equation-label"><span class="visually-hidden a11y-stem">Equation </span>'
        "(2.4)</span></div>"
    )


def test_an_unconverted_equation_keeps_its_number_after_the_original():
    eq = equation(0, label="2.4")
    failed = Fix(element_id=eq.id, kind=Kind.EQUATION, status=Status.FAILED, reason="invalid_latex")
    html = body([eq], [failed])
    assert html.startswith('<div class="flagged" id="p1-e0"')
    original_then_label = html.index('<p class="original-text">') < html.index("equation-label")
    assert original_then_label and html.index("equation-label") < html.index("flag-note")


def test_an_equation_without_a_number_is_unchanged_and_labels_are_escaped():
    plain = equation(0)
    assert body([plain], [accepted(plain)]).startswith('<div class="equation" id="p1-e0">')
    hostile = equation(1, label="<script>1</script>")
    assert "&lt;script&gt;1&lt;/script&gt;" in body([hostile], [accepted(hostile)])


# ---------------------------------------------------------------- axe-core


@pytest.mark.browser
def test_axe_finds_no_problems_with_nested_lists_and_numbered_equations():
    eq = equation(4, label="(1)")
    inline = equation(6, display="inline", parent="p1-e5")
    elements = [
        item(0, "1.", "Measure the ramp."),
        item(1, "(a)", "with a tape", level=2),
        item(2, "(b)", "with a laser", level=2),
        item(3, "2.", "Time the cart."),
        eq,
        item(5, "\u2022", f"where {inline_marker(inline.id)} is the start"),
        inline,
    ]
    fixes = [accepted(eq), accepted(inline, "inline")]
    html = render_html(title="Lists", lang="en", elements=elements, fixes=fixes, crops={})
    checks = axe_checks(html)
    if checks == [] or (len(checks) == 1 and checks[0].code == "skipped"):
        pytest.skip("axe-core needs Playwright and its Chromium")
    assert [c.name for c in checks] == ["axe"] and checks[0].passed
