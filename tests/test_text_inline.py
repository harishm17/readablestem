"""pipeline.inline_text: links, colour runs and passage languages in the output.

Offsets are into the element's stored text, inline markers included. Links are written
outermost and never wrap an inline equation; colour spans sit inside links; spans that
start or end inside a marker, reach past the text or overlap are skipped. Text without
links or colour is written exactly as before.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from readablestem.core.types import (
    ColourRun,
    Element,
    Kind,
    Link,
    Run,
    ScriptRun,
    inline_marker,
)
from readablestem.pipeline import inline_text, student_sheet
from readablestem.pipeline.inline_text import colour_class, passage_attrs, render_inline_text

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


def test_an_unsafe_href_never_reaches_the_output():
    # The model refuses one; a stored result edited by hand could still hold one.
    link = uri(0, 4).model_construct(start=0, end=4, kind="uri", href="javascript:alert(1)")
    internal = to(5, 9).model_construct(start=5, end=9, kind="crossref", target_id='x" onclick')
    stored = paragraph("Open this now").model_copy(update={"links": [link, internal]})
    assert html(stored) == "Open this now"


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
    assert styles.added == {
        "colour-tc-ff0000": ".tc-ff0000 { color: #ee0000; }\n"
        "@media screen and (prefers-color-scheme: dark) { .tc-ff0000 { color: #ff0000; } }\n"
    }


def test_without_add_style_colour_is_not_written():
    element = paragraph("red text", colour_runs=[ColourRun(start=0, end=3, rgb=RED)])
    assert render_inline_text("red text", element=element, equation=maths) == "red text"


def test_colour_reaches_the_minimum_contrast():
    assert student_sheet.colour(colour_class((210, 210, 210)), (210, 210, 210)) == (
        ".tc-d2d2d2 { color: #767676; }\n"
        "@media screen and (prefers-color-scheme: dark) { .tc-d2d2d2 { color: #d2d2d2; } }\n"
    )
    # Blue is readable on white as printed; on the dark page it is lightened.
    light, dark = student_sheet.colour(colour_class((0, 0, 255)), (0, 0, 255)).split("\n@media", 1)
    assert light == ".tc-0000ff { color: #0000ff; }"
    assert "prefers-color-scheme: dark) { .tc-0000ff { color: #" in dark and "#0000ff" not in dark


# --------------------------------------------------------------------------- unchanged text


# --------------------------------------------------------------------------- language


def test_a_passage_language_is_an_attribute_of_its_block():
    assert passage_attrs(paragraph("Bonjour.", lang="fr")) == ' lang="fr"'
    assert passage_attrs(paragraph("Hello.")) == ""
    # A stored value that is not a well-formed tag is left out (the model only checks
    # the shape loosely).
    assert passage_attrs(paragraph("x", lang="en-a")) == ""


# ------------------------------------------------------------------ raised and lowered text


def script(start: int, end: int, kind: str) -> ScriptRun:
    return ScriptRun(start=start, end=end, kind=kind)  # type: ignore[arg-type]


def test_script_runs_are_written_as_sup_and_sub():
    text = "H2O and Na+ and g/cm3"
    element = paragraph(
        text, script_runs=[script(1, 2, "sub"), script(10, 11, "sup"), script(20, 21, "sup")]
    )
    assert html(element) == "H<sub>2</sub>O and Na<sup>+</sup> and g/cm<sup>3</sup>"


def test_text_around_a_script_is_escaped():
    element = paragraph("a<b & c2", script_runs=[script(7, 8, "sub")])
    assert html(element) == "a&lt;b &amp; c<sub>2</sub>"


def test_a_lowered_run_beside_a_raised_one_is_one_stacked_pair():
    styles = Styles()
    element = paragraph("SO42-", script_runs=[script(2, 3, "sub"), script(3, 5, "sup")])
    # Text order (4, then 2-) is the reading order; the class says the lowered run is first.
    assert html(element, styles) == (
        'SO<span class="script-stack script-stack-low-first"><sub>4</sub><sup>2-</sup></span>'
    )
    assert styles.added == {"script-stack": student_sheet.SCRIPT_STACK}
    # A nuclide: the mass number comes first, and is drawn on top.
    nuclide = paragraph("226Ra", script_runs=[script(0, 2, "sup"), script(2, 3, "sub")])
    assert html(nuclide).startswith('<span class="script-stack"><sup>22</sup><sub>6</sub>')


# --------------------------------------------------------------------------- struck text


def test_struck_text_is_written_as_del_and_said_as_deleted():
    # F-509: the meaning must not depend on a screen reader announcing <del>.
    text = f"Midterm on October 3 October 10, see {inline_marker('p1-e1')} and the notes."
    start, marker = text.index("October 3"), text.index(inline_marker("p1-e1"))
    element = paragraph(
        text,
        links=[uri(0, 7)],
        colour_runs=[ColourRun(start=start, end=start + 7, rgb=RED)],
        runs=[
            Run(start=start, end=start + 9, kind="deleted"),
            Run(start=marker, end=text.index(" and"), kind="deleted"),  # an equation
        ],
    )
    styles = Styles()
    out = html(element, styles)
    said = '<span class="visually-hidden readablestem">deleted: </span><del>{}</del>' + (
        '<span class="visually-hidden readablestem"> end deleted</span>'
    )
    # The struck run is said once, the colour inside it; the words stay as they were.
    assert out.startswith('<a href="https://example.org/a">Midterm</a> on ')
    coloured = '<span class="tc-ff0000">October</span> 3'
    assert said.format(coloured) + " October 10, see " + said.format(MATHS) in out
    assert out.count("deleted: ") == 2 and out.endswith(" and the notes.")
    assert inline_text.DELETED_CLASS in styles.added
    # Without struck runs the text is written exactly as before, with no rule added.
    unmarked = Styles()
    assert html(paragraph(text, links=[uri(0, 7)]), unmarked) == (
        f'<a href="https://example.org/a">Midterm</a> on October 3 October 10, see {MATHS}'
        " and the notes."
    )
    assert unmarked.added == {}


@pytest.mark.parametrize("edge", ["deleted", "link"])
def test_a_blank_that_a_struck_or_link_edge_cuts_is_said_once(edge):
    text = "Name: ______ here"
    blank = Run(start=6, end=12, kind="blank")
    cut = (
        dict(runs=[blank, Run(start=9, end=17, kind="deleted")])
        if edge == "deleted"
        else dict(runs=[blank], links=[uri(9, 17)])
    )
    out = html(paragraph(text, **cut))
    assert out.count(">blank<") == 1 and out.count("______") == 1


# --------------------------------------------------------------------------- bold, italic, code


class Nesting(HTMLParser):
    """The tags of a piece of HTML must close in the order they opened; the words are what a
    reader gets with the tool's own hidden words and the maths left out."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.words: list[str] = []
        self.ok = True
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("math", "br"):
            self.hidden += 1  # the stand-in maths holds its own letters
        self.stack.append(tag)
        if tag == "span" and "visually-hidden" in dict(attrs).get("class", ""):
            self.hidden += 1

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.ok = False
            return
        if tag in ("math", "br") or (tag == "span" and self.hidden):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.words.append(data)


MARKER = inline_marker("p1-e1")
MIXED = f"alpha beta gamma delta epsilon {MARKER} zeta eta theta iota kappa lambda ____"
END = len(MIXED)
AFTER_MARKER = MIXED.index(MARKER) + len(MARKER)


@pytest.mark.parametrize(
    "spans",
    [
        pytest.param(
            dict(
                runs=[
                    ("strong", 0, 16),
                    ("emphasis", 6, 26),
                    ("code", 20, 34),
                    ("highlight", 3, 30),
                ]
            ),
            id="overlapping",
        ),
        pytest.param(
            dict(
                links=[uri(0, 30)],
                colour_runs=[ColourRun(start=5, end=25, rgb=RED)],
                runs=[("strong", 8, 18), ("emphasis", 10, 40)],
            ),
            id="inside a link and a colour run, one crossing the link",
        ),
        pytest.param(
            dict(
                script_runs=[script(3, 4, "sup")],
                runs=[
                    ("deleted", 12, 22),
                    ("strong", 15, AFTER_MARKER + 9),
                    ("emphasis", 0, END),
                    ("highlight", 8, AFTER_MARKER + 14),
                ],
                colour_runs=[ColourRun(start=18, end=AFTER_MARKER + 20, rgb=RED)],
            ),
            id="round a struck run, maths and a raised letter",
        ),
        pytest.param(
            dict(runs=[("code", 0, END), ("strong", END - 20, END), ("blank", END - 4, END)]),
            id="over a blank",
        ),
    ],
)
def test_every_mix_of_runs_nests_and_adds_no_words(spans):
    # F-510: bold, italic and code are carried, whatever else covers the same words.
    runs = [Run(start=a, end=b, kind=k) for k, a, b in spans.pop("runs")]
    kinds = {r.kind for r in runs}
    element = paragraph(MIXED, runs=runs, **spans)
    out = html(element)
    check = Nesting()
    check.feed(out)
    assert check.ok and not check.stack, out
    said = "".join(check.words).replace("deleted: ", "").replace(" end deleted", "")
    assert said == MIXED.replace(MARKER, "")
    assert "<strong>" in out and ("<mark>" in out) == ("highlight" in kinds)
    for tag in ("strong", "em", "mark"):
        assert f"<{tag}>{MATHS}" not in out
