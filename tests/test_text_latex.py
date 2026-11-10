"""The text pass on real PDFs: a LaTeX document with hyperref links, cross-references, a
French quotation and coloured text (tests/fixtures/layout/latex/links_language_colour.tex,
compiled by Tectonic), and PDFs built here for rotated pages, named destinations and /Lang.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.core.types import ConversionOptions, Element, Kind
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import text_pass
from readablestem.pipeline.html_checks import axe_checks
from readablestem.pipeline.layout import analyze
from readablestem.pipeline.run import convert_pdf
from readablestem.pipeline.text_pass import TextPassResult

FIXTURE = Path(__file__).parent / "fixtures" / "layout" / "latex" / "links_language_colour.pdf"


@cache
def passed(colour: bool = True) -> TextPassResult:
    data = FIXTURE.read_bytes()
    layout = analyze(data)
    return text_pass.apply(
        data, layout.elements, pages=layout.pages, layout_lang=layout.lang, colour=colour
    )


def element(result: TextPassResult, starts: str) -> Element:
    return next(e for e in result.elements if (e.text or "").startswith(starts))


def shown(e: Element) -> list[tuple[str, str, str | None]]:
    text = e.text or ""
    return [(k.kind, text[k.start : k.end], k.href or k.target_id) for k in e.links]


def target_kind(result: TextPassResult, target: str | None) -> Kind:
    return next(e.kind for e in result.elements if e.id == target)


# --------------------------------------------------------------------------- the LaTeX fixture


def test_web_and_email_links_keep_their_text():
    result = passed()
    links = shown(element(result, "The course syllabus"))
    assert [(kind, href) for kind, _, href in links] == [
        ("uri", "https://example.org/physics/syllabus"),
        ("uri", "mailto:instructor@example.edu"),
        ("uri", "https://example.org/physics/archive/2025/lecture-notes/week-01-kinematics.pdf"),
        ("uri", "https://example.org/physics/problems"),
    ]
    texts = [text for _, text, _ in links]
    assert texts[:2] == ["course syllabus", "the instructor by email"]
    assert texts[2].startswith("https://example.org/") and texts[3] == "here"


def test_the_ftp_link_is_dropped_and_unclear_links_are_noted():
    result = passed()
    owner = element(result, "The course syllabus").id
    notices = [(n.code, n.element_id, n.count, n.review) for n in result.notices]
    assert ("link_scheme_dropped", owner, 1, "optional") in notices
    # "here"; the bare URL is not counted: the sentence around it says what it is.
    assert ("link_text_unclear", owner, 1, "optional") in notices
    assert "file server" in (element(result, "The course syllabus").text or "")


def test_cross_references_and_hyperref_links_point_at_equations_and_the_figure():
    result = passed()
    links = shown(element(result, "Eliminating"))
    assert [text for _, text, _ in links] == [
        "Eqs. (1)",
        "(2)",
        "Equation (1)",  # hyperref linked only the "1"; widened to the reference
        "Eq. 2",
        "Figure 1",
        "Figure 1",
    ]
    kinds = [target_kind(result, target) for _, _, target in links]
    assert kinds == [Kind.EQUATION] * 4 + [Kind.FIGURE] * 2
    assert {kind for kind, _, _ in links} == {"crossref", "internal"}
    first, second = (target for _, _, target in links[:2])
    assert first != second and links[2][2] == first


def test_the_french_quotation_is_marked_and_the_document_is_english():
    result = passed()
    assert (result.lang, result.lang_source) == ("en", "detected")
    assert element(result, "La vitesse moyenne").lang == "fr"
    assert [e.id for e in result.elements if e.lang] == [element(result, "La vitesse").id]
    assert ("lang_detected", None) in [(n.code, n.element_id) for n in result.notices]


def test_coloured_text_becomes_runs_with_the_flag():
    result = passed()
    paragraph = element(result, "In these notes")
    text = paragraph.text or ""
    runs = [(text[r.start : r.end], r.rgb) for r in paragraph.colour_runs]
    assert runs == [
        ("the vectors are printed in red", (255, 0, 0)),
        ("A light grey hint follows each problem.", (210, 210, 210)),
    ]  # the dark grey sentence reads as black: no run
    assert ("colour_reference", paragraph.id) in [(n.code, n.element_id) for n in result.notices]


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


@cache
def converted(colour: bool = True):
    return convert_pdf(
        FIXTURE.read_bytes(),
        settings=settings(),
        client=FakeVisionClient(),
        options=ConversionOptions(colour=colour),
    ).result


@pytest.mark.browser
def test_axe_finds_no_problems_with_links_language_and_colour():
    checks = axe_checks(converted().html)
    if len(checks) == 1 and checks[0].code == "skipped":
        pytest.skip("axe-core needs Playwright and its Chromium")
    assert [c.name for c in checks] == ["axe"] and checks[0].passed


# --------------------------------------------------------------------------- built PDFs


def built(rotation: int = 0, lang: str | None = None) -> bytes:
    """Two pages: a paragraph with a web link and two internal links (one explicit, one
    to a named destination), and a heading on page 2 that both point at."""
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page(width=612, height=792)
    first, second = doc[0], doc[1]
    text = "Read the course notes or go to Section Two for the details of the motion."
    first.insert_text((72, 100), text, fontsize=11, fontname="helv")
    second.insert_text((72, 300), "Section Two", fontsize=16, fontname="hebo")
    second.insert_text((72, 330), "The body of the second section is here.", fontsize=11)

    def rect(part: str) -> pymupdf.Rect:
        return first.search_for(part)[0]

    first.insert_link({"kind": pymupdf.LINK_URI, "from": rect("course notes"),
                       "uri": "https://example.org/notes"})  # fmt: skip
    first.insert_link({"kind": pymupdf.LINK_GOTO, "from": rect("Section Two"), "page": 1,
                       "to": pymupdf.Point(72, 284)})  # fmt: skip
    # A named destination, as hyperref writes them: PDF coordinates (y up).
    names = doc.get_new_xref()
    doc.update_object(names, f"<< /Names [(sec.2) [{second.xref} 0 R /XYZ 72 508 null]] >>")
    tree = doc.get_new_xref()
    doc.update_object(tree, f"<< /Dests {names} 0 R >>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"{tree} 0 R")
    target = rect("the details")
    annot = doc.get_new_xref()
    y0, y1 = 792 - target.y1, 792 - target.y0
    doc.update_object(
        annot,
        f"<< /Type /Annot /Subtype /Link /Rect [{target.x0} {y0} {target.x1} {y1}] "
        "/Border [0 0 0] /A << /S /GoTo /D (sec.2) >> >>",
    )
    annots = doc.xref_get_key(first.xref, "Annots")[1].strip("[]")
    doc.xref_set_key(first.xref, "Annots", f"[{annots} {annot} 0 R]")
    if lang:
        doc.xref_set_key(doc.pdf_catalog(), "Lang", f"({lang})")
    if rotation:
        for page in doc:
            page.set_rotation(rotation)
    return doc.tobytes()


@pytest.mark.parametrize("rotation", [0, 90])
def test_links_map_onto_text_on_rotated_pages_too(rotation):
    data = built(rotation)
    layout = analyze(data)
    result = text_pass.apply(data, layout.elements, pages=layout.pages)
    paragraph = next(e for e in result.elements if (e.text or "").startswith("Read the"))
    heading = next(e for e in result.elements if e.text == "Section Two")
    assert shown(paragraph) == [
        ("uri", "course notes", "https://example.org/notes"),
        ("internal", "Section Two", heading.id),
        ("internal", "the details", heading.id),
    ]
    assert result.annotations == 3


def test_the_pdfs_lang_is_used_and_not_detected():
    data = built(lang="en-GB")
    layout = analyze(data)
    result = text_pass.apply(data, layout.elements, pages=layout.pages, layout_lang=layout.lang)
    assert (result.lang, result.lang_source) == ("en-GB", "pdf")
    assert not any(n.code.startswith("lang_") for n in result.notices)
