"""pipeline.text_pass on made-up pages: links, cross-references, colour runs, language.

Glyphs and link rectangles are built here (`line`), so each rule is tested without a PDF;
tests/test_text_latex.py runs the same pass on a real LaTeX document.
"""

from __future__ import annotations

import logging

import pymupdf
import pytest

from a11y_stem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    Kind,
    Link,
    Notice,
    inline_marker,
)
from a11y_stem.pipeline import text_pass
from a11y_stem.pipeline.assemble import render_html
from a11y_stem.pipeline.layout import Glyph
from a11y_stem.pipeline.text_pass import (
    PdfLink,
    PdfText,
    align,
    annotate,
    body_colour,
    crossref_targets,
    crossrefs,
    is_run_colour,
    references,
    set_document_language,
    target_at,
    unclear_link_text,
    widen_internal,
)

BLACK = (0, 0, 0)
RED = (204, 0, 0)
W = 5.0  # width of every made-up glyph


def line(text: str, x: float = 72.0, y: float = 100.0, rgb=BLACK, colours=None) -> list[Glyph]:
    """Glyphs for `text` printed left to right from (x, y), W points per character
    (spaces take room but are not glyphs). `colours` maps character offsets to colours."""
    glyphs = []
    for i, c in enumerate(text):
        if not c.isspace():
            colour = (colours or {}).get(i, rgb)
            glyphs.append(Glyph(c, (x + i * W, y, x + (i + 1) * W, y + 10), colour))
    return glyphs


def rect_of(text: str, part: str, x: float = 72.0, y: float = 100.0) -> tuple:
    """The rectangle a link annotation would have over `part` of a `line(text)`."""
    start = text.index(part)
    return (x + start * W, y - 1, x + (start + len(part)) * W, y + 11)


def el(eid: str, kind: Kind = Kind.PARAGRAPH, text: str | None = None, bbox=None, order=0, **kw):
    return Element(id=eid, kind=kind, page=kw.pop("page", 0), bbox=bbox or (72, 98, 572, 112),
                   order=order, text=text, **kw)  # fmt: skip


def links_of(result, eid: str) -> list[tuple[str, str, str | None]]:
    element = next(e for e in result.elements if e.id == eid)
    text = element.text or ""
    return [(k.kind, text[k.start : k.end], k.href or k.target_id) for k in element.links]


def codes(result) -> list[tuple[str, str | None, int]]:
    return [(n.code, n.element_id, n.count) for n in result.notices]


# --------------------------------------------------------------------------- alignment


def test_alignment_places_characters_and_skips_spaces_and_markers():
    marker = inline_marker("p1-e1")
    text = f"ab {marker} cd"
    glyphs = [*line("ab"), Glyph("v", (90, 100, 95, 110), BLACK), *line("cd", x=100)]
    assert align(text, glyphs) == [0, 1, *[None] * (len(marker) + 2), 3, 4]


def test_alignment_survives_a_repaired_line_break():
    glyphs = line("veloc-") + line("ity", y=115)
    out = align("velocity", glyphs)
    assert [glyphs[i].c for i in out if i is not None] == list("velocity")


# --------------------------------------------------------------------------- web links


def pdf_with(glyphs, *links, lang=None) -> PdfText:
    return PdfText(lang=lang, links={0: list(links)}, glyphs={0: glyphs}, annotations=len(links))


def test_a_web_link_covers_exactly_its_words():
    text = "Read the course syllabus first."
    link = PdfLink(0, rect_of(text, "course syllabus"), "uri", uri="https://example.org/s")
    result = annotate([el("p1-e0", text=text)], pdf_with(line(text), link))
    assert links_of(result, "p1-e0") == [("uri", "course syllabus", "https://example.org/s")]


def test_a_rectangle_that_grazes_the_next_word_does_not_take_it():
    text = "Read the syllabus first."
    x0, y0, x1, y1 = rect_of(text, "syllabus")
    link = PdfLink(0, (x0 - 0.4 * W, y0, x1 + 0.4 * W, y1), "uri", uri="https://example.org/s")
    result = annotate([el("p1-e0", text=text)], pdf_with(line(text), link))
    assert links_of(result, "p1-e0") == [("uri", "syllabus", "https://example.org/s")]


def test_link_offsets_count_the_inline_equation_markers():
    marker = inline_marker("p1-e1")
    text = f"The speed {marker} is in the notes online."
    printed = "The speed v is in the notes online."
    glyphs = line(printed)
    link = PdfLink(0, rect_of(printed, "notes online"), "uri", uri="https://example.org/n")
    equation = el("p1-e1", Kind.EQUATION, "v", display="inline", parent_id="p1-e0",
                  bbox=(122, 100, 127, 110))  # fmt: skip
    elements = [el("p1-e0", text=text), equation]
    result = annotate(elements, pdf_with(glyphs, link))
    ((_, shown, href),) = links_of(result, "p1-e0")
    assert shown == "notes online" and href == "https://example.org/n"
    link_obj = result.elements[0].links[0]
    assert text[link_obj.start : link_obj.end] == "notes online"
    assert link_obj.start > text.index(marker) + len(marker)


def test_a_link_over_an_inline_equation_is_split_around_it():
    marker = inline_marker("p1-e1")
    text = f"the speed {marker} of carts"
    printed = "the speed v of carts"
    link = PdfLink(0, rect_of(printed, "speed v of"), "uri", uri="https://example.org/v")
    result = annotate([el("p1-e0", text=text)], pdf_with(line(printed), link))
    assert [shown for _, shown, _ in links_of(result, "p1-e0")] == ["speed", "of"]


def test_a_link_broken_across_two_lines_is_one_link():
    first, second = "notes at https://example.org/a-", "long-name.pdf today."
    text = f"{first} {second}"
    glyphs = line(first) + line(second, y=115)
    url = "https://example.org/a-long-name.pdf"
    links = [
        PdfLink(0, rect_of(first, "https://example.org/a-"), "uri", uri=url),
        PdfLink(0, rect_of(second, "long-name.pdf", y=115), "uri", uri=url),
    ]
    element = el("p1-e0", text=text, bbox=(72, 98, 572, 127))
    result = annotate([element], pdf_with(glyphs, *links))
    assert links_of(result, "p1-e0") == [("uri", "https://example.org/a- long-name.pdf", url)]


@pytest.mark.parametrize(
    "link",
    [
        PdfLink(0, (0, 0, 0, 0), "uri", uri="ftp://ftp.example.org/data.csv"),
        PdfLink(0, (0, 0, 0, 0), "uri", uri="javascript:alert(1)"),
        PdfLink(0, (0, 0, 0, 0), "uri", uri="file:///etc/passwd"),
        PdfLink(0, (0, 0, 0, 0), "other"),
    ],
)
def test_a_link_that_is_not_web_or_email_is_dropped_and_its_text_stays(link):
    text = "The lab data file is online."
    placed = PdfLink(0, rect_of(text, "data file"), link.kind, uri=link.uri)
    result = annotate([el("p1-e0", text=text)], pdf_with(line(text), placed))
    assert result.elements[0].links == [] and result.elements[0].text == text
    assert codes(result)[:1] == [("link_scheme_dropped", "p1-e0", 1)]
    assert result.notices[0].review == "optional"


def test_a_dropped_link_over_no_text_is_counted_on_its_page():
    links = [PdfLink(0, (300, 500, 320, 520), "other"), PdfLink(0, (300, 600, 320, 620), "other")]
    result = annotate([el("p1-e0", text="Text.")], pdf_with(line("Text."), *links))
    assert result.notices[0] == Notice(code="link_scheme_dropped", page=0, count=2)


@pytest.mark.parametrize(
    ("text", "unclear"),
    [
        ("click here", True),
        ("Here.", True),
        ("this link", True),
        ("Read more", True),
        ("https://example.org/physics/archive/2025/lecture-notes.pdf", True),
        ("https://example.org/ physics/archive/2025/lecture-notes.pdf", True),
        ("https://example.org/syllabus", False),  # short enough to read
        ("the course syllabus", False),
        ("instructor@example.edu", False),
        ("Figure 2", False),
    ],
)
def test_unclear_link_text(text, unclear):
    assert unclear_link_text(text) is unclear


def test_an_unclear_link_gets_an_optional_notice():
    text = "The problem sets are here and the syllabus there."
    links = [
        PdfLink(0, rect_of(text, "here"), "uri", uri="https://example.org/p"),
        PdfLink(0, rect_of(text, "syllabus"), "uri", uri="https://example.org/s"),
    ]
    result = annotate([el("p1-e0", text=text)], pdf_with(line(text), *links))
    assert len(result.elements[0].links) == 2
    assert ("link_text_unclear", "p1-e0", 1) in codes(result)


URL = "https://example.org/physics/archive/2025/lecture-notes.pdf"


@pytest.mark.parametrize(
    ("text", "unclear"),
    [
        (URL, True),  # the URL alone
        (f"Notes: {URL}", True),
        (f"{URL} (PDF, 2 MB)", True),  # two words and a number say little
        (f"Smith, A. (2024). Notes on kinematics. Retrieved from {URL}", False),
        (f"Last year's notes are at {URL} for revision.", False),
    ],
)
def test_a_long_url_is_unclear_only_with_next_to_no_words_around_it(text, unclear):
    link = PdfLink(0, rect_of(text, URL), "uri", uri=URL)
    result = annotate([el("p1-e0", text=text, bbox=(72, 98, 900, 112))],
                      pdf_with(line(text), link))  # fmt: skip
    assert links_of(result, "p1-e0") == [("uri", URL, URL)]
    assert ("link_text_unclear" in [code for code, _, _ in codes(result)]) is unclear


def test_context_words_leave_out_the_link_urls_and_maths():
    marker = inline_marker("p1-e1")
    text = f"See {marker} at https://example.org/a and x = 2 in the notes"
    start = text.index("https")
    assert text_pass.context_words(text, start, start + len("https://example.org/a")) == 6
    assert text_pass.context_words("https://a.org/x https://b.org/y", 0, 15) == 0


# --------------------------------------------------------------------------- internal links


def page_two() -> list[Element]:
    return [
        el("p2-e0", Kind.HEADING, "2 Motion", bbox=(72, 100, 300, 115), page=1, order=10),
        el("p2-e1", Kind.PARAGRAPH, "Text of the section.", bbox=(72, 120, 540, 160), page=1,
           order=11),
        el("p2-e2", Kind.FIGURE, bbox=(200, 200, 400, 300), page=1, order=12,
           caption="Figure 1: A cart."),
        el("p2-e3", Kind.EQUATION, "v = at", bbox=(250, 400, 330, 415), page=1, order=13,
           display="block", equation_label="(1)"),
        el("p2-e4", Kind.PARAGRAPH, "", bbox=(72, 500, 540, 520), page=1, order=14),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("point", "target"),
    [
        ((72, 98), "p2-e0"),  # just above the heading
        ((250, 396), "p2-e3"),  # a little above the equation
        ((290, 310), "p2-e2"),  # below the figure, at its caption
        ((100, 130), "p2-e1"),  # inside the paragraph
        (None, "p2-e0"),  # no position: the top of the page
    ],
)
def test_a_destination_names_the_element_at_or_just_below_it(point, target):
    found = target_at(page_two(), point)
    assert found is not None and found.id == target


def test_a_destination_prefers_its_column():
    left = el("p2-e0", text="Left column.", bbox=(72, 300, 290, 340), page=1)
    right = el("p2-e1", text="Right column.", bbox=(310, 320, 540, 360), page=1, order=1)
    assert target_at([left, right], (312, 296)).id == "p2-e1"  # type: ignore[union-attr]
    assert target_at([left, right], (74, 296)).id == "p2-e0"  # type: ignore[union-attr]


def test_an_empty_paragraph_is_never_a_target():
    assert target_at([page_two()[-1]], (72, 500)) is None


def test_an_internal_link_points_at_the_element_there():
    text = "See section 2 for more."
    source = el("p1-e0", text=text)
    link = PdfLink(0, rect_of(text, "section 2"), "goto", dest_page=1, dest_point=(72, 96))
    result = annotate([source, *page_two()], pdf_with(line(text), link))
    assert links_of(result, "p1-e0") == [("internal", "section 2", "p2-e0")]


def test_an_internal_link_to_an_inline_equation_points_at_its_paragraph():
    text = "See the speed."
    elements = [
        el("p1-e0", text=text),
        el("p2-e0", text=f"The speed {inline_marker('p2-e1')} grows.", page=1,
           bbox=(72, 100, 540, 115), order=1),
        el("p2-e1", Kind.EQUATION, "v", page=1, bbox=(130, 100, 136, 112), order=2,
           display="inline", parent_id="p2-e0"),
    ]  # fmt: skip
    link = PdfLink(0, rect_of(text, "the speed"), "goto", dest_page=1, dest_point=(131, 101))
    result = annotate(elements, pdf_with(line(text), link))
    assert links_of(result, "p1-e0") == [("internal", "the speed", "p2-e0")]


def test_an_internal_link_to_its_own_paragraph_is_dropped_quietly():
    text = "See this very paragraph."
    link = PdfLink(0, rect_of(text, "this very"), "goto", dest_page=0, dest_point=(72, 100))
    result = annotate([el("p1-e0", text=text)], pdf_with(line(text), link))
    assert result.elements[0].links == [] and not any("link" in n.code for n in result.notices)


def test_hyperrefs_number_only_link_is_widened_to_the_whole_reference():
    text = "Figure 1 and Eqs. (1) and (2) say so."
    links = [Link(start=7, end=8, kind="internal", target_id="p2-e2"),
             Link(start=19, end=20, kind="internal", target_id="p2-e3")]  # fmt: skip
    widened = widen_internal(text, links)
    assert [text[k.start : k.end] for k in widened] == ["Figure 1", "Eqs. (1)"]
    assert [k.target_id for k in widened] == ["p2-e2", "p2-e3"]  # the PDF's own targets


# --------------------------------------------------------------------------- cross-references


def targets_for(*elements: Element):
    return crossref_targets(elements)


FIGURES = [
    el("f1", Kind.FIGURE, caption="Figure 2.3 Velocity against time."),
    el("f2", Kind.FIGURE, caption="Fig. 4: A ramp."),
    el("f3", Kind.FIGURE, caption="Figure: no number."),
    el("t1", Kind.TABLE, caption="Table 1 Measured speeds."),
    el("e3", Kind.EQUATION, display="block", equation_label="(3)"),
    el("e4", Kind.EQUATION, display="block", equation_label="(4) (5)"),
    el("e6", Kind.EQUATION, display="block", equation_label="2.4"),
    el("i1", Kind.EQUATION, display="inline", equation_label="(9)"),
]


def refs(text: str, *, taken=(), elements=FIGURES) -> list[tuple[str, str | None]]:
    return [
        (text[k.start : k.end], k.target_id) for k in crossrefs(text, targets_for(*elements), taken)
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("As Figure 2.3 shows, the speed grows.", [("Figure 2.3", "f1")]),
        ("See Fig. 4 and fig. 2.3.", [("Fig. 4", "f2"), ("fig. 2.3", "f1")]),
        ("Figures 2.3 and 4 show it.", [("Figures 2.3", "f1"), ("4", "f2")]),
        ("Table 1 lists them.", [("Table 1", "t1")]),
        ("From Equation (3) we get", [("Equation (3)", "e3")]),
        ("from equation (3) we get", [("equation (3)", "e3")]),
        ("by Eq. 3 and Eqn. (4)", [("Eq. 3", "e3"), ("Eqn. (4)", "e4")]),
        ("Eqs. (3) and (4) give", [("Eqs. (3)", "e3"), ("(4)", "e4")]),
        ("Equations (3), (4) and (5)", [("Equations (3)", "e3"), ("(4)", "e4"), ("(5)", "e4")]),
        ("Eq. (2.4) is the definition.", [("Eq. (2.4)", "e6")]),
        ("Adding (3) to (4) gives", [("(3)", "e3"), ("(4)", "e4")]),
    ],
)
def test_references_link_to_the_one_target_with_that_number(text, expected):
    assert refs(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Figure 7 shows nothing we have.",  # no such figure
        "the value f(3) is small",  # maths, not a reference
        "items (1) and (2) of the list",  # no equation prints (1) or (2)
        "as (2.4) shows",  # 2.4 is printed bare, so "(2.4)" is not its label
        "Equation (9) is inline.",  # inline equations are not targets
        "Figure 2.3a shows a detail.",  # a part of a figure no caption names
        "The period of 1990s tables.",
        "(3) Measure the speed of the cart.",  # an enumeration label, though (3) exists
        "  (4) Then the time.",
    ],
)
def test_no_link_without_one_target(text):
    assert refs(text) == []


def test_an_ambiguous_number_gets_no_link():
    twice = [
        el("f1", Kind.FIGURE, caption="Figure 2 A."),
        el("f2", Kind.FIGURE, caption="Figure 2 B."),
    ]
    assert refs("See Figure 2.", elements=twice) == []


def test_an_unattached_caption_does_not_link_to_itself():
    assert refs("Figure 2.3: Velocity against time.") == []
    assert refs("Figure 2.3 shows the velocity.") == [("Figure 2.3", "f1")]


def test_references_never_overlap_existing_links_or_markers():
    text = "See Figure 2.3 and Eq. (3)."
    taken = [Link(start=4, end=14, kind="uri", href="https://example.org/f")]
    assert refs(text, taken=taken) == [("Eq. (3)", "e3")]
    assert [r.number for r in references(f"Eq. {inline_marker('p1-e1')}")] == []


# --------------------------------------------------------------------------- colour


@pytest.mark.parametrize(
    ("rgb", "run"),
    [
        (BLACK, False),
        ((38, 38, 38), False),  # dark grey reads as body text
        ((90, 90, 90), False),
        ((0, 0, 40), False),  # nearly black, whatever its tint
        ((210, 210, 210), True),  # light grey: too light to read, so kept and darkened
        ((250, 250, 250), False),  # near white: printed on a dark fill
        ((255, 0, 0), True),
        ((0, 0, 255), True),
        ((51, 51, 179), True),
        ((0, 128, 0), True),
    ],
)
def test_which_colours_are_runs(rgb, run):
    assert is_run_colour(rgb, BLACK) is run


def test_the_body_colour_is_never_a_run():
    assert not is_run_colour((0, 0, 128), (0, 0, 128))


def test_the_body_colour_is_the_commonest_readable_one():
    navy = [Glyph("a", (0, 0, 1, 1), (0, 0, 128))] * 5 + [Glyph("b", (0, 0, 1, 1), RED)]
    assert body_colour(navy) == (0, 0, 128)
    pale = [Glyph("a", (0, 0, 1, 1), (210, 210, 210))] * 5
    assert body_colour(pale) is None  # light body text stays a run, to be darkened
    assert body_colour([]) is None


# Black text elsewhere on the page: most text is in the body colour, as on a real page.
BODY_TEXT = line("Body text in black, far more of it than of any colour. " * 3, y=700)


def coloured(text: str, part: str, rgb=RED) -> list[Glyph]:
    """`line(text)` with `part` in `rgb`, plus the page's black body text."""
    start = text.index(part)
    return line(text, colours={i: rgb for i in range(start, start + len(part))}) + BODY_TEXT


def test_a_coloured_phrase_becomes_a_run_and_the_body_text_does_not():
    text = "Read the terms in red as vectors, the rest as scalars."
    glyphs = coloured(text, "terms in red as vectors")
    result = annotate([el("p1-e0", text=text)], PdfText(glyphs={0: glyphs}), colour=True)
    (run,) = result.elements[0].colour_runs
    assert text[run.start : run.end] == "terms in red as vectors" and run.rgb == RED


def test_colour_runs_need_the_flag():
    text = "Read the terms in red as vectors."
    result = annotate([el("p1-e0", text=text)], PdfText(glyphs={0: coloured(text, "terms")}))
    assert result.elements[0].colour_runs == []
    assert not any(n.code == "colour_reference" for n in result.notices)


def test_a_run_continues_across_an_inline_equation_of_the_same_colour():
    marker = inline_marker("p1-e1")
    text = f"the force {marker} acts here"
    printed = "the force F acts here"
    glyphs = coloured(printed, "force F acts")
    result = annotate([el("p1-e0", text=text)], PdfText(glyphs={0: glyphs}), colour=True)
    (run,) = result.elements[0].colour_runs
    assert text[run.start : run.end] == f"force {marker} acts"


def test_linked_text_is_left_out_of_a_run():
    text = "The red notes online are here."
    link = PdfLink(0, rect_of(text, "notes online"), "uri", uri="https://example.org/n")
    pdf = PdfText(links={0: [link]}, glyphs={0: coloured(text, "red notes online are")})
    result = annotate([el("p1-e0", text=text)], pdf, colour=True)
    assert [text[r.start : r.end] for r in result.elements[0].colour_runs] == ["red", "are"]
    assert links_of(result, "p1-e0") == [("uri", "notes online", "https://example.org/n")]


def test_a_colour_reference_near_coloured_text_gets_a_notice():
    first = "The vectors are printed in red."
    elements = [
        el("p1-e0", text=first, bbox=(72, 98, 572, 112)),
        el("p1-e1", text="Nothing else here.", bbox=(72, 120, 572, 134), order=1),
        el("p1-e2", text="The blue curve is the speed.", bbox=(72, 140, 572, 154), order=2),
        el("p1-e3", text="Far away.", bbox=(72, 160, 572, 174), order=3),
        el("p1-e4", text="Far away.", bbox=(72, 180, 572, 194), order=4),
        el("p1-e5", text="Shown in green, far from colour.", bbox=(72, 200, 572, 214), order=5),
    ]
    pdf = PdfText(glyphs={0: coloured(first, "vectors", (255, 0, 0))})
    result = annotate(elements, pdf, colour=True)
    notices = [(n.code, n.element_id) for n in result.notices if n.code == "colour_reference"]
    assert notices == [("colour_reference", "p1-e0"), ("colour_reference", "p1-e2")]


@pytest.mark.parametrize(
    ("text", "refers"),
    [
        ("shown in red", True),
        ("highlighted in yellow", True),
        ("the blue curve", True),
        ("red arrows mark the forces", True),
        ("coloured green", True),
        ("the reddish glow", False),
        ("in order to read", False),
        ("the Red River", False),
    ],
)
def test_colour_reference_phrases(text, refers):
    assert bool(text_pass.COLOUR_REFERENCE.search(text)) is refers


# --------------------------------------------------------------------------- language

ENGLISH = (
    "The velocity of a particle is the derivative of its position with respect to time, "
    "and its acceleration is the derivative of the velocity."
)
FRENCH = "La vitesse moyenne est le quotient du déplacement par la durée du trajet."


def test_the_pdfs_language_comes_first():
    result = annotate([el("p1-e0", text=ENGLISH)], PdfText(lang="en-GB"))
    assert (result.lang, result.lang_source, result.notices) == ("en-GB", "pdf", [])
    result = annotate([el("p1-e0", text=ENGLISH)], PdfText(), layout_lang="de")
    assert (result.lang, result.lang_source) == ("de", "pdf")


def test_without_lang_the_language_is_detected_and_flagged():
    result = annotate([el("p1-e0", text=ENGLISH)], PdfText())
    assert (result.lang, result.lang_source) == ("en", "detected")
    assert result.notices == [Notice(code="lang_detected")]


def test_with_too_little_text_english_is_assumed_and_flagged():
    result = annotate([el("p1-e0", text="Speeds.")], PdfText())
    assert (result.lang, result.lang_source) == ("en", "default")
    assert result.notices == [Notice(code="lang_assumed")]


def test_a_passage_in_another_language_is_marked():
    elements = [el("p1-e0", text=ENGLISH), el("p1-e1", text=FRENCH, order=1),
                el("p1-e2", Kind.HEADING, text=FRENCH, order=2)]  # fmt: skip
    result = annotate(elements, PdfText(lang="en"))
    assert [e.lang for e in result.elements] == [None, "fr", None]  # not on headings


# --------------------------------------------------------------------------- language change


def stored(lang: str = "en", source="detected") -> DocumentResult:
    elements = [el("p1-e0", text=ENGLISH), el("p1-e1", text=FRENCH, order=1, lang="fr")]
    return DocumentResult(
        pipeline_version="0.4.0",
        lang=lang,
        lang_source=source,
        elements=elements,
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
        notices=[Notice(code="lang_detected"), Notice(code="colour_reference", page=0)],
    )


def test_the_instructor_can_change_the_document_language():
    result = set_document_language(stored(), "fr")
    assert (result.lang, result.lang_source) == ("fr", "user")
    assert [n.code for n in result.notices] == ["colour_reference"]
    # Against French, the English paragraph is now the passage in another language.
    assert [e.lang for e in result.elements] == ["en", None]


def test_a_language_name_or_tag_is_accepted_and_anything_else_refused():
    assert set_document_language(stored(), "English").lang == "en"
    assert set_document_language(stored(), "en_gb").lang == "en-GB"
    for bad in ("", "Klingonese", "x-none", 'en" onload="'):
        with pytest.raises(ValueError):
            set_document_language(stored(), bad)


def test_the_original_result_is_not_changed():
    before = stored()
    set_document_language(before, "fr")
    assert (before.lang, before.lang_source, len(before.notices)) == ("en", "detected", 2)


def test_the_changed_result_renders_with_the_new_language():
    result = set_document_language(stored(), "fr")
    html = render_html(
        title="T", lang=result.lang, elements=result.elements, fixes=result.fixes, crops={}
    )
    assert '<html lang="fr">' in html and '<p id="p1-e0" lang="en">' in html
    assert '<p id="p1-e1">La vitesse' in html
    loaded = DocumentResult.model_validate(result.model_dump(mode="json"))
    assert (loaded.lang, loaded.lang_source) == ("fr", "user")


# --------------------------------------------------------------------------- the pass as a whole


def test_a_pdf_that_cannot_be_read_still_gets_references_and_a_language(caplog):
    elements = [el("p1-e0", text=f"As Figure 2.3 shows. {ENGLISH}"), FIGURES[0]]
    with caplog.at_level(logging.WARNING):
        result = text_pass.apply(b"not a pdf", elements, pages=[0])
    assert links_of(result, "p1-e0") == [("crossref", "Figure 2.3", "f1")]
    assert result.lang_source == "detected"
    assert "not a pdf" not in caplog.text and "Figure" not in caplog.text


def test_a_failing_pass_leaves_the_elements_as_they_were(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(text_pass, "annotate", broken)
    elements = [el("p1-e0", text=ENGLISH)]
    result = text_pass.apply(b"not a pdf", elements, pages=[0], layout_lang="fr")
    assert result.elements == elements and (result.lang, result.lang_source) == ("fr", "pdf")
    assert result.notices == []
    # With no language from the PDF either, English is assumed and the job page asks.
    result = text_pass.apply(b"not a pdf", elements, pages=[0])
    assert (result.lang, result.lang_source) == ("en", "default")
    assert result.notices == [Notice(code="lang_assumed")]


def test_counts_are_numbers_only():
    text = "Read the syllabus and Figure 2.3."
    link = PdfLink(0, rect_of(text, "syllabus"), "uri", uri="https://example.org/s")
    result = annotate([el("p1-e0", text=text), FIGURES[0]], pdf_with(line(text), link))
    assert result.counts() == {"links": 1, "crossrefs": 1, "colour_runs": 0, "passages": 0,
                               "notices": 1}  # fmt: skip


def test_link_count_counts_every_annotation():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 10, 50, 20),
                      "uri": "https://example.org"})  # fmt: skip
    page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 30, 50, 40),
                      "page": 0, "to": pymupdf.Point(0, 0)})  # fmt: skip
    doc.new_page()
    assert text_pass.link_count(doc, [0, 1, 7]) == 2
