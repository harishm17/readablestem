"""pipeline.source_facts: what the input PDF gives a screen-reader user, as numbers.

PDFs are made here with PyMuPDF: an untagged one with pictures, links, bookmarks, a
title and a bold maths letter, and a tagged one whose structure tree gives some figures
alt text (one through a role map). Facts degrade one by one: a file that cannot be
opened still gets the counts that come from the elements.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import (
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from readablestem.pipeline import source_facts
from readablestem.pipeline.source_facts import flattened_parts, measure, structure_parts

MATH = "http://www.w3.org/1998/Math/MathML"


def png(width: int = 60, height: int = 40) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (90, 120, 200))
    return pixmap.tobytes("png")


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


def test_a_script_the_text_layer_keeps_is_not_lost():
    element = equation("p1-e1", text="x² + 1")
    fix = eq_fix("p1-e1", math("<msup><mi>x</mi><mn>2</mn></msup><mo>+</mo><mn>1</mn>"))
    assert flattened_parts(element, fix) == []
    fraction = eq_fix("p1-e1", math("<mfrac><msup><mi>x</mi><mn>2</mn></msup><mn>2</mn></mfrac>"))
    assert flattened_parts(element, fraction) == ["fraction"]


# --------------------------------------------------------------------------- the PDF


def test_too_many_objects_leave_alt_text_unknown(monkeypatch):
    monkeypatch.setattr(source_facts, "_MAX_OBJECTS", 3)
    facts = measure(tagged_pdf(alts=["A cart"]), pages=[0], elements=[], fixes=[])
    assert facts.tagged is True and facts.images_with_alt is None
