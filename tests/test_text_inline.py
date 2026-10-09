"""pipeline.inline_text: links, colour runs and passage languages in the output.

Offsets are into the element's stored text, inline markers included. Links are written
outermost and never wrap an inline equation; colour spans sit inside links; spans that
start or end inside a marker, reach past the text or overlap are skipped. Text without
links or colour is written exactly as before.
"""

from __future__ import annotations

import pytest

from a11y_stem.core.types import ColourRun, Element, Fix, Kind, Link, Status, inline_marker
from a11y_stem.pipeline import inline_text
from a11y_stem.pipeline.assemble import render_html
from a11y_stem.pipeline.html_checks import static_checks
from a11y_stem.pipeline.inline_text import colour_css, passage_attrs, render_inline_text

MATHS = '<math display="inline"><mi>v</mi></math>'


def paragraph(text: str, **kw) -> Element:
    return Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(72, 72, 500, 100), order=0,
                   text=text, **kw)  # fmt: skip


def maths(element_id: str) -> str | None:
    return MATHS if element_id == "p1-e1" else None


class Styles:
    def __init__(self) -> None:
        self.added: dict[str, str] = {}

    def __call__(self, name: str, css: str) -> None:
        self.added.setdefault(name, css)


def html(element: Element, styles: Styles | None = None) -> str:
    return render_inline_text(
        element.text or "", element=element, equation=maths, add_style=styles or Styles()
    )


def uri(start: int, end: int, href: str = "https://example.org/a") -> Link:
    return Link(start=start, end=end, kind="uri", href=href)


def to(start: int, end: int, target: str = "p1-e5", kind: str = "crossref") -> Link:
    return Link(start=start, end=end, kind=kind, target_id=target)  # type: ignore[arg-type]


RED = (255, 0, 0)


# --------------------------------------------------------------------------- links


def test_a_web_link_keeps_its_visible_text():
    text = "Read the course syllabus first."
    element = paragraph(text, links=[uri(9, 24)])
    assert html(element) == 'Read the <a href="https://example.org/a">course syllabus</a> first.'


def test_hrefs_are_escaped_and_internal_links_point_at_ids():
    text = "See Figure 2 and the notes."
    element = paragraph(
        text, links=[to(4, 12, "p2-e3"), uri(21, 26, "https://example.org/?a=1&b=<2>")]
    )
    assert html(element) == (
        'See <a href="#p2-e3">Figure 2</a> and the '
        '<a href="https://example.org/?a=1&amp;b=&lt;2&gt;">notes</a>.'
    )


def test_offsets_count_the_inline_markers():
    marker = inline_marker("p1-e1")
    text = f"The speed {marker} is in Equation (3), see the notes."
    start = text.index("Equation (3)")
    element = paragraph(text, links=[to(start, start + len("Equation (3)"), "p1-e7")])
    assert html(element) == (
        f'The speed {MATHS} is in <a href="#p1-e7">Equation (3)</a>, see the notes.'
    )


def test_a_link_across_an_inline_equation_is_split_around_it():
    marker = inline_marker("p1-e1")
    text = f"the speed {marker} of the cart"
    element = paragraph(text, links=[uri(4, len(text))])
    assert html(element) == (
        'the <a href="https://example.org/a">speed </a>'
        f'{MATHS}<a href="https://example.org/a"> of the cart</a>'
    )


def test_a_link_that_is_only_an_equation_is_not_written():
    marker = inline_marker("p1-e1")
    text = f"the speed {marker} here"
    start = text.index(marker)
    element = paragraph(text, links=[uri(start, start + len(marker))])
    assert html(element) == f"the speed {MATHS} here"


@pytest.mark.parametrize(
    "link",
    [
        uri(2, 40),  # past the end of the text
        uri(11, 15),  # starts inside the marker
        uri(0, 12),  # ends inside the marker
    ],
)
def test_spans_outside_the_text_or_inside_a_marker_are_skipped(link):
    text = f"Speed is {inline_marker('p1-e1')} now."
    assert html(paragraph(text, links=[link])) == f"Speed is {MATHS} now."


def test_an_unsafe_href_never_reaches_the_output():
    # The model refuses one; a stored result edited by hand could still hold one.
    link = uri(0, 4).model_construct(start=0, end=4, kind="uri", href="javascript:alert(1)")
    internal = to(5, 9).model_construct(start=5, end=9, kind="crossref", target_id='x" onclick')
    stored = paragraph("Open this now").model_copy(update={"links": [link, internal]})
    assert html(stored) == "Open this now"


def test_overlapping_links_keep_the_first():
    element = paragraph("course syllabus", links=[uri(0, 6), uri(3, 15, "https://example.org/b")])
    assert html(element) == '<a href="https://example.org/a">course</a> syllabus'


def test_links_apply_only_to_the_elements_own_text():
    element = paragraph("course syllabus", links=[uri(0, 6)])
    out = render_inline_text("Another text", element=None, equation=maths)
    assert out == "Another text"
    assert "<a" not in render_inline_text(
        "course syllabus", element=None, equation=maths, add_style=Styles()
    )
    assert "<a" in html(element)


# --------------------------------------------------------------------------- colour


def test_a_colour_run_gets_a_class_and_one_rule_per_colour():
    text = "Vectors are red and so is this."
    styles = Styles()
    element = paragraph(
        text, colour_runs=[ColourRun(start=0, end=7, rgb=RED), ColourRun(start=20, end=30, rgb=RED)]
    )
    assert html(element, styles) == (
        '<span class="tc-ff0000">Vectors</span> are red and '
        '<span class="tc-ff0000">so is this</span>.'
    )
    assert styles.added == {"colour-tc-ff0000": ".tc-ff0000 { color: #ee0000; }\n"}


def test_colour_spans_sit_inside_links_and_may_wrap_maths():
    marker = inline_marker("p1-e1")
    text = f"see the red {marker} term"
    run = ColourRun(start=4, end=len(text), rgb=RED)
    element = paragraph(text, links=[uri(0, 3)], colour_runs=[run])
    assert html(element) == (
        '<a href="https://example.org/a">see</a> '
        f'<span class="tc-ff0000">the red {MATHS} term</span>'
    )
    across = paragraph(
        "see the notes", links=[uri(4, 13)], colour_runs=[ColourRun(start=0, end=7, rgb=RED)]
    )
    assert html(across) == (
        '<span class="tc-ff0000">see </span><a href="https://example.org/a">'
        '<span class="tc-ff0000">the</span> notes</a>'
    )


def test_without_add_style_colour_is_not_written():
    element = paragraph("red text", colour_runs=[ColourRun(start=0, end=3, rgb=RED)])
    assert render_inline_text("red text", element=element, equation=maths) == "red text"


def test_colour_css_reaches_the_minimum_contrast():
    assert colour_css((210, 210, 210)) == ".tc-d2d2d2 { color: #767676; }\n"
    assert colour_css((0, 0, 255)) == ".tc-0000ff { color: #0000ff; }\n"


# --------------------------------------------------------------------------- unchanged text


def test_text_without_spans_is_written_as_before():
    marker = inline_marker("p1-e1")
    text = f"a < b {marker} and {inline_marker('p9-e9')}."
    assert html(paragraph(text)) == f"a &lt; b {MATHS} and {inline_marker('p9-e9')}."


# --------------------------------------------------------------------------- language


def test_a_passage_language_is_an_attribute_of_its_block():
    assert passage_attrs(paragraph("Bonjour.", lang="fr")) == ' lang="fr"'
    assert passage_attrs(paragraph("Hello.")) == ""
    # A stored value that is not a well-formed tag is left out (the model only checks
    # the shape loosely).
    assert passage_attrs(paragraph("x", lang="en-a")) == ""


def test_the_assembled_document_carries_links_colour_and_language():
    figure = Element(id="p1-e5", kind=Kind.FIGURE, page=0, bbox=(72, 200, 300, 300), order=2,
                     caption="Figure 1 A cart.")  # fmt: skip
    text = "Figure 1 shows the cart in red; see the syllabus."
    first = paragraph(
        text,
        links=[to(0, 8, "p1-e5"), uri(40, 48)],
        colour_runs=[ColourRun(start=19, end=30, rgb=(210, 210, 210))],
    )
    french = Element(id="p1-e1", kind=Kind.LIST_ITEM, page=0, bbox=(72, 100, 500, 120), order=1,
                     text="La vitesse moyenne.", lang="fr", list_marker="•",
                     list_ordered=False, list_level=1)  # fmt: skip
    fix = Fix(element_id="p1-e5", kind=Kind.FIGURE, status=Status.ACCEPTED,
              figure={"figure_type": "diagram", "alt": "A cart on a ramp."})  # fmt: skip
    out = render_html(title="T", lang="en", elements=[first, french, figure], fixes=[fix], crops={})
    assert '<a href="#p1-e5">Figure 1</a>' in out and 'id="p1-e5"' in out
    assert '<li id="p1-e1" lang="fr">La vitesse moyenne.</li>' in out
    assert ".tc-d2d2d2 { color: #767676; }" in out
    assert [c.code for c in static_checks(out) if not c.passed] == []
    assert inline_text.COLOUR_CLASS_PREFIX == "tc-"
