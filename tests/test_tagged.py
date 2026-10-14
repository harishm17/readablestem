"""Tagged PDFs (T2-18): headings and figure alt text from a Word-style export, used as
proposals. The fixture is HTML exported by LibreOffice Writer as a tagged PDF
(tests/fixtures/tagged/make_tagged_pdf.py): a title and a Method heading tagged H1 and H2, a
body-size line "Practical tips" tagged H3, and three pictures (a real alt text, Word's
auto-generated one, none)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.config import MAIN_MODEL
from readablestem.core.types import Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import figure_step, tagged
from readablestem.pipeline.heading_step import heading_fixes
from readablestem.pipeline.layout import analyze, heading_levels, tagged_level
from readablestem.pipeline.run import convert_pdf
from test_steps_support import figure_element, make_settings, use_fallback_router

FIXTURE = Path(__file__).parent / "fixtures" / "tagged" / "tagged_notes.pdf"
ALT = "Bar chart: the period rises with the length of the string"


@pytest.fixture(scope="module")
def pdf() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def layout(pdf):
    return analyze(pdf)


def test_the_tags_of_the_fixture_are_read(pdf):
    found = tagged.read(pdf, [0])
    assert [(h.level, h.text) for h in found.headings] == [
        (1, "Pendulum Lab Notes"),
        (2, "Method"),
        (3, "Practical tips"),
    ]
    assert [f.alt for f in found.figures] == [
        ALT,
        "A picture containing text Description automatically generated",
    ]
    assert all(f.bbox is not None for f in found.figures)


def test_an_untagged_pdf_has_no_tags():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Plain")
    assert not tagged.read(doc.tobytes(), [0])
    assert not tagged.read(b"not a pdf", [0])


def test_a_body_size_line_the_tags_call_a_heading_becomes_one(pdf, layout):
    before = {e.text: e for e in layout.elements if e.text}
    assert before["Practical tips"].kind is Kind.PARAGRAPH  # layout alone misses it
    elements = tagged.apply(layout.elements, tagged.read(pdf, [0]))
    by_text = {e.text: e for e in elements if e.text}
    assert by_text["Practical tips"].kind is Kind.HEADING
    assert [tagged_level(e) for e in elements if e.kind is Kind.HEADING] == [1, 2, 3]
    assert heading_levels([e for e in elements if e.kind is Kind.HEADING]) == {
        by_text["Pendulum Lab Notes"].id: 1,
        by_text["Method"].id: 2,
        by_text["Practical tips"].id: 3,
    }


def test_a_tagged_heading_is_a_certain_heading(pdf, layout):
    elements = tagged.apply(layout.elements, tagged.read(pdf, [0]))
    fixes = {f.element_id: f for f in heading_fixes(elements)}
    tips = next(e for e in elements if e.text == "Practical tips")
    fix = fixes[tips.id]
    assert (fix.status, fix.review, fix.confidence) == (Status.ACCEPTED, "none", 0.9)
    assert fix.heading is not None and fix.heading.level == 3
    assert not fix.failed_checks


def test_a_tag_over_other_text_changes_nothing(pdf, layout):
    found = tagged.read(pdf, [0])
    wrong = tagged.TaggedPdf(
        [tagged.TaggedHeading(0, 2, "A heading nobody printed", (0, 0, 10, 10))], found.figures
    )
    elements = tagged.apply(layout.elements, wrong)
    assert [e.kind for e in elements if e.text] == [e.kind for e in layout.elements if e.text]
    assert all(tagged_level(e) is None for e in elements)


def test_only_alt_text_that_says_something_is_kept(pdf, layout):
    elements = tagged.apply(layout.elements, tagged.read(pdf, [0]))
    figures = [e for e in elements if e.kind is Kind.FIGURE]
    # The first picture's alt text; Word's own sentence and the missing one are dropped.
    assert [f.source_alt for f in figures] == [ALT, None, None]


@pytest.mark.parametrize(
    ("alt", "kept"),
    [
        (ALT, True),
        ("Picture 3", False),
        ("image.png", False),
        ("A picture containing text Description automatically generated", False),
        ("  ", False),
        ("https://example.com/chart", False),
    ],
)
def test_usable_alt(alt, kept):
    assert (tagged.usable_alt(alt) is not None) is kept


def test_a_conversion_uses_the_tags(pdf):
    output = convert_pdf(
        pdf, settings=make_settings(), client=FakeVisionClient(), notice="offline test"
    )
    html = output.result.html
    assert "<h3 " in html and ">Practical tips</h3>" in html
    figures = [e for e in output.result.elements if e.kind is Kind.FIGURE]
    assert [f.source_alt for f in figures] == [ALT, None, None]


# ----------------------------------------------------------------- the figure step


@pytest.fixture
def router(monkeypatch):
    use_fallback_router(monkeypatch)


def test_the_tag_is_context_for_the_model(router):
    element = figure_element().model_copy(update={"source_alt": ALT})
    client = FakeVisionClient(
        responders={
            "figure": lambda req: {
                "figure_type": "chart",
                "decorative": False,
                "alt": "Bar chart of period against length: the period grows with the length.",
                "confidence": 0.93,
            }
        }
    )
    fix = figure_step.describe(element, b"\x89PNG crop", client=client, settings=make_settings())
    assert fix.status is Status.ACCEPTED  # the model's description, not the tag's
    (request,) = client.requests
    assert request.model == MAIN_MODEL
    assert f"Alt text in the PDF's own tags (may be wrong): {ALT}" in (request.context_text or "")


def test_a_figure_without_a_tag_sends_the_context_it_always_did(router):
    assert figure_step.figure_context(figure_element(), "near text") == "Nearby text: near text"


def test_when_the_model_gives_nothing_the_tag_is_the_proposal(router):
    element = figure_element().model_copy(update={"source_alt": ALT})
    client = FakeVisionClient(responders={"figure": lambda req: None})
    fix = figure_step.describe(element, b"\x89PNG crop", client=client, settings=make_settings())
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert fix.figure is not None and fix.figure.alt == ALT
    assert [c.code for c in fix.failed_checks] == ["source_alt_unchecked"]


def test_with_no_tag_a_model_failure_is_still_a_failure(router):
    client = FakeVisionClient(responders={"figure": lambda req: None})
    fix = figure_step.describe(
        figure_element(), b"\x89PNG crop", client=client, settings=make_settings()
    )
    assert fix.status is Status.FAILED and fix.figure is None
