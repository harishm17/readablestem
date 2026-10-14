"""pipeline.source_facts: what the input PDF gives a screen-reader user, as numbers.

PDFs are made here with PyMuPDF: an untagged one with pictures, links, bookmarks, a
title and a bold maths letter, and a tagged one whose structure tree gives some figures
alt text (one through a role map). Facts degrade one by one: a file that cannot be
opened still gets the counts that come from the elements.
"""

from __future__ import annotations

import logging

import pymupdf
import pytest

from readablestem.core.types import (
    Element,
    EquationFix,
    Fix,
    Kind,
    SourceFacts,
    Status,
)
from readablestem.pipeline import source_facts
from readablestem.pipeline.source_facts import flattened_parts, measure, structure_parts

MATH = "http://www.w3.org/1998/Math/MathML"


def png(width: int = 60, height: int = 40) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (90, 120, 200))
    return pixmap.tobytes("png")


def untagged_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Kinematics", fontsize=18)
    page.insert_text((72, 120), "d", fontname="tibo", fontsize=12)  # a bold d, stored as "d"
    page.insert_text((80, 120), "= vt", fontname="tiro", fontsize=12)
    page.insert_image(pymupdf.Rect(72, 200, 272, 300), stream=png())
    page.insert_image(pymupdf.Rect(300, 200, 306, 206), stream=png(8, 8))  # decoration
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 76),
                      "uri": "https://openstax.org"})  # fmt: skip
    second = doc.new_page()
    second.insert_image(pymupdf.Rect(72, 72, 272, 172), stream=png())
    doc.set_toc([[1, "Kinematics", 1], [2, "Speed", 2]])
    doc.set_metadata({"title": "Kinematics notes"})
    data = doc.tobytes()
    doc.close()
    return data


def tagged_pdf(*, alts: list[str | None], role_map: bool = False) -> bytes:
    """Two pictures; a structure tree with one figure element per entry of `alts` (None:
    no /Alt). With `role_map`, the second element uses a custom type mapped to Figure."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Tagged")
    page.insert_image(pymupdf.Rect(72, 100, 272, 200), stream=png())
    page.insert_image(pymupdf.Rect(72, 220, 272, 320), stream=png())
    kids = []
    for i, alt in enumerate(alts):
        xref = doc.get_new_xref()
        kind = "/Diagram" if role_map and i == 1 else "/Figure"
        alt_entry = f" /Alt ({alt})" if alt is not None else ""
        doc.update_object(xref, f"<< /Type /StructElem /S {kind}{alt_entry} >>")
        kids.append(f"{xref} 0 R")
    root = doc.get_new_xref()
    roles = " /RoleMap << /Diagram /Figure >>" if role_map else ""
    doc.update_object(root, f"<< /Type /StructTreeRoot /K [{' '.join(kids)}]{roles} >>")
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "StructTreeRoot", f"{root} 0 R")
    doc.xref_set_key(catalog, "MarkInfo", "<< /Marked true >>")
    doc.xref_set_key(catalog, "Lang", "(en-GB)")
    data = doc.tobytes()
    doc.close()
    return data


def equation(eid: str, bbox=(70, 105, 140, 125), **kw) -> Element:
    return Element(id=eid, kind=Kind.EQUATION, page=0, bbox=bbox, order=1, display="block", **kw)


def eq_fix(eid: str, mathml: str, status: Status = Status.ACCEPTED) -> Fix:
    return Fix(element_id=eid, kind=Kind.EQUATION, status=status, confidence=0.9,
               equation=EquationFix(latex="x", mathml=mathml, speech="x"))  # fmt: skip


def math(body: str) -> str:
    return f'<math xmlns="{MATH}" display="block">{body}</math>'


# --------------------------------------------------------------------------- structure


@pytest.mark.parametrize(
    ("body", "parts"),
    [
        ("<mi>x</mi><mo>=</mo><mn>5</mn>", []),
        ("<mfrac><mi>a</mi><mi>b</mi></mfrac>", ["fraction"]),
        ("<msup><mi>λ</mi><mn>2</mn></msup>", ["power"]),
        ("<msup><mi>A</mi><mrow><mo>−</mo><mn>1</mn></mrow></msup>", ["superscript"]),
        ("<msup><mi>f</mi><mo>′</mo></msup>", []),  # a prime reads the same on one line
        ("<msub><mi>v</mi><mn>0</mn></msub>", ["subscript"]),
        ("<msubsup><mi>x</mi><mn>1</mn><mn>2</mn></msubsup>", ["power", "subscript"]),
        ("<msqrt><mi>x</mi></msqrt>", ["root"]),
        ("<munderover><mo>∑</mo><mi>i</mi><mi>n</mi></munderover>", ["limits"]),
        ("<mtable><mtr><mtd><mn>1</mn></mtd></mtr></mtable>", ["rows"]),
        # An aligned derivation: its rows are lines of the text layer, only the fraction
        # inside is lost.
        (
            "<mtable><mtr><mtd><mi>v</mi></mtd><mtd><mo>=</mo></mtd><mtd><mfrac><mi>d</mi>"
            "<mi>t</mi></mfrac></mtd></mtr><mtr><mtd></mtd><mtd><mrow><mo>=</mo></mrow></mtd>"
            "<mtd><mn>2</mn></mtd></mtr></mtable>",
            ["fraction"],
        ),
        (
            "<mtable><mtr><mtd><mi>x</mi></mtd><mtd><mi>x</mi><mo>&gt;</mo><mn>0</mn></mtd>"
            "</mtr></mtable>",
            ["rows"],  # cases: a relation inside a cell, not starting one
        ),
        ("<mover><mi>v</mi><mo>→</mo></mover>", ["accent"]),
        ("<mfrac><msqrt><mi>x</mi></msqrt><mn>2</mn></mfrac>", ["fraction", "root"]),
    ],
)
def test_structure_parts_name_what_one_line_of_text_loses(body, parts):
    assert structure_parts(math(body)) == parts


def test_structure_parts_of_unparseable_or_huge_mathml_are_unknown():
    assert structure_parts("<math><mfrac>") == []
    assert structure_parts("") == []
    assert structure_parts(math("<mfrac><mi>a</mi><mi>b</mi></mfrac>" * 20_000)) == []


def test_flattened_parts_use_the_mathml_the_output_uses_else_layouts_evidence():
    element = equation("p1-e1", reasons=["fraction bar", "math font"])
    used = eq_fix("p1-e1", math("<msqrt><mi>x</mi></msqrt>"))
    assert flattened_parts(element, used) == ["root"]
    kept = eq_fix("p1-e1", math("<msqrt><mi>x</mi></msqrt>"), Status.REJECTED)
    assert flattened_parts(element, kept) == ["fraction"]  # the image stays: layout says
    assert flattened_parts(element, None) == ["fraction"]
    assert flattened_parts(equation("p1-e2", reasons=["math font"]), None) == []


def test_a_script_the_text_layer_keeps_is_not_lost():
    element = equation("p1-e1", text="x² + 1")
    fix = eq_fix("p1-e1", math("<msup><mi>x</mi><mn>2</mn></msup><mo>+</mo><mn>1</mn>"))
    assert flattened_parts(element, fix) == []
    fraction = eq_fix("p1-e1", math("<mfrac><msup><mi>x</mi><mn>2</mn></msup><mn>2</mn></mfrac>"))
    assert flattened_parts(element, fraction) == ["fraction"]


# --------------------------------------------------------------------------- the PDF


def test_an_untagged_pdf_is_measured():
    eq = equation("p1-e1", text="\U0001d41d = vt")
    fix = eq_fix("p1-e1", math("<mi>\U0001d41d</mi><mo>=</mo><msub><mi>v</mi><mi>t</mi></msub>"))
    table = Element(id="p2-e1", kind=Kind.TABLE, page=1, bbox=(0, 0, 1, 1), order=3)
    facts = measure(untagged_pdf(), pages=[0, 1], elements=[eq, table], fixes=[fix])
    assert facts == SourceFacts(
        tagged=False,
        has_lang=False,
        has_title=True,
        bookmarks=2,
        images=2,  # the 6-point square is decoration
        images_with_alt=None,  # only known for tagged PDFs
        equations=1,
        equations_flattened=1,
        bold_maths_letters=1,
        links=1,
        tables=1,
    )


def test_only_the_analysed_pages_are_counted():
    facts = measure(untagged_pdf(), pages=[1], elements=[], fixes=[])
    assert (facts.images, facts.links, facts.bookmarks) == (1, 0, 2)


def test_bold_letters_the_pdf_stores_styled_are_not_counted():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 120), "x = 1", fontname="tibo")  # bold, but outside the box
    data = doc.tobytes()
    facts = measure(data, pages=[0], elements=[equation("p1-e1", bbox=(300, 300, 320, 320))],
                    fixes=[])  # fmt: skip
    assert facts.bold_maths_letters == 0


def test_a_tagged_pdf_counts_its_figures_with_alt_text():
    facts = measure(tagged_pdf(alts=["A cart", None]), pages=[0], elements=[], fixes=[])
    assert (facts.tagged, facts.has_lang, facts.images, facts.images_with_alt) == (
        True,
        True,
        2,
        1,
    )


def test_alt_text_through_a_role_map_counts_and_never_exceeds_the_pictures():
    facts = measure(tagged_pdf(alts=["A", "B", "C"], role_map=True), pages=[0], elements=[],
                    fixes=[])  # fmt: skip
    assert facts.images_with_alt == 2  # three figure elements, two pictures on the page


def test_a_junk_title_is_no_title():
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"title": "Microsoft Word - notes.docx"})
    facts = measure(doc.tobytes(), pages=[0], elements=[], fixes=[])
    assert facts.has_title is False


def test_a_file_that_cannot_be_opened_keeps_the_counts_from_the_elements(caplog):
    caplog.set_level(logging.WARNING)
    eq = equation("p1-e1", reasons=["fraction bar"])
    facts = measure(b"%PDF- SECRET-DOC-TEXT", pages=[0], elements=[eq], fixes=[])
    assert facts == SourceFacts(tagged=None, equations=1, equations_flattened=1)
    assert "SECRET" not in caplog.text


def test_too_many_objects_leave_alt_text_unknown(monkeypatch):
    monkeypatch.setattr(source_facts, "_MAX_OBJECTS", 3)
    facts = measure(tagged_pdf(alts=["A cart"]), pages=[0], elements=[], fixes=[])
    assert facts.tagged is True and facts.images_with_alt is None


def test_measuring_leaves_no_mupdf_messages_behind():
    measure(b"%PDF-1.4 broken", pages=[0], elements=[], fixes=[])
    assert pymupdf.TOOLS.mupdf_warnings() == ""
