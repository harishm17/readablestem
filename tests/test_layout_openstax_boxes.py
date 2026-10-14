"""OpenStax's teacher boxes and key-terms lines (T1-13): terms as a list, level tags dropped.

The public demo showed "dependent variable independent variable tangent" as one
paragraph and "[BL][OL] Describe a scenario ..." as text to read. The pages are made here
with PyMuPDF and base-14 fonts, with invented text.
"""

from __future__ import annotations

import pymupdf
import pytest

from a11y_stem.core.types import Element, Kind
from a11y_stem.pipeline import layout
from a11y_stem.pipeline.layout import analyze

BODY = 11.0


def page_with(fill) -> list[Element]:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), "Introduction to the section.", fontsize=BODY)
    fill(page)
    return analyze(doc.tobytes()).elements


def terms_line(page: pymupdf.Page, y: float, terms: list[str], gap: float = 13.0) -> None:
    x = 72.0
    for term in terms:
        page.insert_text((x, y), term, fontsize=BODY)
        x += pymupdf.get_text_length(term, fontname="helv", fontsize=BODY) + gap


def test_a_line_of_terms_under_key_terms_is_a_list():
    def fill(page):
        page.insert_text((72, 120), "Section Key Terms", fontsize=16, fontname="hebo")
        terms_line(page, 150, ["dependent variable", "independent variable", "tangent"])
        page.insert_text((72, 200), "The graph of a line has a constant slope.", fontsize=BODY)

    elements = page_with(fill)
    assert [(e.kind.value, e.text) for e in elements[1:]] == [
        ("heading", "Section Key Terms"),
        ("list_item", "dependent variable"),
        ("list_item", "independent variable"),
        ("list_item", "tangent"),
        ("paragraph", "The graph of a line has a constant slope."),
    ]
    items = [e for e in elements if e.kind == Kind.LIST_ITEM]
    assert [e.bbox[0] for e in items] == sorted(e.bbox[0] for e in items)
    assert all(e.list_marker is None for e in items)  # plain bullets, no printed marker


def test_two_lines_of_terms_are_read_left_to_right_and_top_down():
    def fill(page):
        page.insert_text((72, 120), "Key Terms", fontsize=16, fontname="hebo")
        terms_line(page, 150, ["position", "reference frame", "scalar"])
        terms_line(page, 165, ["vector", "displacement", "distance"])

    items = [e.text for e in page_with(fill) if e.kind == Kind.LIST_ITEM]
    assert items == ["position", "reference frame", "scalar", "vector", "displacement", "distance"]


@pytest.mark.parametrize("heading", ["Chapter Summary", "Learning Objectives"])
def test_the_same_line_under_another_heading_is_left_alone(heading):
    def fill(page):
        page.insert_text((72, 120), heading, fontsize=16, fontname="hebo")
        terms_line(page, 150, ["dependent variable", "independent variable", "tangent"])

    elements = page_with(fill)
    assert [e.kind.value for e in elements[2:]] == ["paragraph"]


def test_a_sentence_under_key_terms_stays_a_paragraph():
    def fill(page):
        page.insert_text((72, 120), "Key Terms", fontsize=16, fontname="hebo")
        page.insert_text(
            (72, 150),
            "These terms are used throughout the chapter and are defined in the text.",
            fontsize=BODY,
        )

    elements = page_with(fill)
    assert [e.kind.value for e in elements[2:]] == ["paragraph"]


def test_a_line_of_long_pieces_is_not_a_list_of_terms():
    def fill(page):
        page.insert_text((72, 120), "Key Terms", fontsize=16, fontname="hebo")
        terms_line(
            page,
            150,
            ["one two three four five six seven", "eight nine ten eleven twelve thirteen"],
        )

    assert [e.kind.value for e in page_with(fill)[2:]] == ["paragraph"]


@pytest.mark.parametrize(
    ("printed", "kept"),
    [
        ("[BL][OL] Describe a scenario in which you launch a rocket.", "Describe a scenario"),
        ("[AL] Once the students have looked at the graph, ask more.", "Once the students"),
        ("[All] Discuss what can be uncovered from this graph.", "Discuss what can be"),
        ("[OL][AL][BL] Ask if the zero matters.", "Ask if the zero matters."),
    ],
)
def test_openstax_level_tags_at_the_start_of_a_paragraph_are_dropped(printed, kept):
    def fill(page):
        page.insert_text((72, 120), printed, fontsize=BODY)

    (paragraph,) = [
        e for e in page_with(fill) if e.text and e.text != "Introduction to the section."
    ]
    assert paragraph.text.startswith(kept) and "[" not in paragraph.text


@pytest.mark.parametrize(
    "printed",
    [
        "[Visual] Set up a meter stick for the students.",  # not a level tag
        "[1] Newton described the law of motion in 1687.",
        "See the note [BL] in the middle of a sentence for more.",
        "[bl] lower case is not an OpenStax tag.",
    ],
)
def test_other_text_in_brackets_is_kept(printed):
    def fill(page):
        page.insert_text((72, 120), printed, fontsize=BODY)

    texts = [e.text for e in page_with(fill)]
    assert printed in texts


def test_a_paragraph_of_only_tags_is_kept_not_emptied():
    pieces = layout._strip_teacher_tags(["[BL][OL]"])
    assert pieces == ["[BL][OL]"]
    assert (
        layout._strip_teacher_tags(
            ["[BL] ", layout._Inline((0, 0, 1, 1), "x", 1.0, [], 11.0, None), " go"]
        )[0]
        != "[BL] "
    )
