"""A small, real-shaped document for the review screen tests.

`review_pdf()` makes a two-page PDF with PyMuPDF; `review_output()` is the result the
pipeline would make of it, with every element's box measured on that PDF, so page images,
marks and crops line up as they do for a real upload. What needs a person, as the review
policy would have it (docs/design/beta-architecture.md, "Review tiers"):

- decision 1, equation p1-e2: a bold omega (a vector) in the PDF, read as a plain omega
  (`style_mismatch`, required);
- decision 2, figure p1-e6: a diagram whose alt text the model was unsure of (required);
- decision 3, the heading group hg-p1-e3: "Teacher Support" three times as a bold line in
  the body text size (p1-e3, p1-e7 on page 1, p2-e0 on page 2);
- an optional note, equation p1-e5: a bold subscript zero read as a plain one
  (`style_cosmetic`, accepted);
- accepted with nothing to note: heading p1-e0 and equation p2-e2.

Document text carries no SECRET markers here: the privacy tests use fixtures.web.support.
"""

from __future__ import annotations

from functools import cache

import pymupdf

from a11y_stem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
    inline_marker,
)

PAGE = (612.0, 792.0)
BOLD_D0 = "\U0001d41d\U0001d7ce"  # MATHEMATICAL BOLD SMALL D, MATHEMATICAL BOLD DIGIT ZERO
GROUP = "hg-p1-e3"
FIGURE_BOX = (72.0, 300.0, 300.0, 440.0)


def _html(page: pymupdf.Page, rect: tuple[float, float, float, float], html: str) -> None:
    css = "p { font-family: serif; font-size: 11pt; margin: 0; }"
    page.insert_htmlbox(pymupdf.Rect(rect), html, css=css)


@cache
def review_pdf() -> bytes:
    doc = pymupdf.open()
    one = doc.new_page(width=PAGE[0], height=PAGE[1])
    one.insert_text((72, 84), "1 Vectors", fontname="hebo", fontsize=14)
    _html(
        one,
        (72, 100, 540, 140),
        "<p>A rigid body turning with angular velocity <b>ω</b> moves a point at "
        "position r with the speed it needs.</p>",
    )
    one.insert_text((72, 180), "Teacher Support", fontname="hebo", fontsize=10.5)
    _html(one, (72, 190, 540, 230), "<p>Discuss vectors and scalars with the class.</p>")
    _html(one, (260, 250, 360, 275), "<p><b>d</b><sub><b>0</b></sub> = 0</p>")
    one.draw_rect(pymupdf.Rect(FIGURE_BOX), color=(0.2, 0.3, 0.6), width=2)
    one.draw_circle((140, 380), 30, color=(0.7, 0.2, 0.2), fill=(0.9, 0.6, 0.6))
    one.draw_line((180, 400), (280, 330), color=(0.1, 0.1, 0.1), width=2)
    one.insert_text((72, 456), "Figure 1 A car moves from home to school.", fontsize=9)
    one.insert_text((72, 500), "Teacher Support", fontname="hebo", fontsize=10.5)
    _html(one, (72, 510, 540, 560), "<p>Ask students to describe a path of motion.</p>")
    two = doc.new_page(width=PAGE[0], height=PAGE[1])
    two.insert_text((72, 100), "Teacher Support", fontname="hebo", fontsize=10.5)
    _html(two, (72, 110, 540, 150), "<p>Average speed is distance over time.</p>")
    _html(two, (250, 170, 360, 195), "<p>v = d / t</p>")
    doc.subset_fonts()
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def _box(page: pymupdf.Page, needle: str, index: int = 0) -> tuple[float, float, float, float]:
    rect = page.search_for(needle)[index]
    return (round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2))


def _mathml(latex: str, display: str) -> str:
    from a11y_stem.pipeline.validate import safe_latex_to_mathml

    return safe_latex_to_mathml(latex, display)


def _crop(page: pymupdf.Page, box: tuple[float, float, float, float]) -> bytes:
    x0, y0, x1, y1 = box
    clip = pymupdf.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2)
    return page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False).tobytes("png")


def review_output() -> ConversionOutput:
    doc = pymupdf.open(stream=review_pdf(), filetype="pdf")
    one, two = doc[0], doc[1]
    boxes = {
        "p1-e0": _box(one, "1 Vectors"),
        "p1-e1": (72.0, 100.0, 540.0, 128.0),
        "p1-e2": _box(one, "ω"),
        "p1-e3": _box(one, "Teacher Support", 0),
        "p1-e4": (72.0, 190.0, 540.0, 205.0),
        "p1-e5": (260.0, 250.0, 290.0, 268.0),
        "p1-e6": FIGURE_BOX,
        "p1-e7": _box(one, "Teacher Support", 1),
        "p2-e0": _box(two, "Teacher Support"),
        "p2-e1": (72.0, 110.0, 540.0, 125.0),
        "p2-e2": (250.0, 170.0, 300.0, 186.0),
    }
    crops = {eid: _crop(one if eid.startswith("p1") else two, boxes[eid]) for eid in boxes}
    doc.close()

    def element(eid: str, kind: Kind, order: int, **kw) -> Element:
        page = 0 if eid.startswith("p1") else 1
        return Element(id=eid, kind=kind, page=page, bbox=boxes[eid], order=order, **kw)

    teacher = {"text": "Teacher Support", "font_size": 10.5, "bold": True}
    elements = [
        element("p1-e0", Kind.HEADING, 0, text="1 Vectors", font_size=14, bold=True),
        element("p1-e1", Kind.PARAGRAPH, 1, text="A rigid body turning with angular velocity "
                f"{inline_marker('p1-e2')} moves a point at position r with the speed it needs."),
        element("p1-e2", Kind.EQUATION, 2, text="𝛚", font_size=11, display="inline",
                parent_id="p1-e1"),
        element("p1-e3", Kind.HEADING, 3, reasons=["bold body-size line"], **teacher),
        element("p1-e4", Kind.PARAGRAPH, 4, text="Discuss vectors and scalars with the class."),
        element("p1-e5", Kind.EQUATION, 5, text=BOLD_D0 + " = 0", font_size=11, display="block"),
        element("p1-e6", Kind.FIGURE, 6, caption="Figure 1 A car moves from home to school."),
        element("p1-e7", Kind.HEADING, 7, reasons=["bold body-size line"], **teacher),
        element("p2-e0", Kind.HEADING, 8, reasons=["bold body-size line"], **teacher),
        element("p2-e1", Kind.PARAGRAPH, 9, text="Average speed is distance over time."),
        element("p2-e2", Kind.EQUATION, 10, text="v = d / t", font_size=11, display="block"),
    ]  # fmt: skip

    def equation(latex: str, display: str = "block") -> EquationFix:
        return EquationFix(latex=latex, mathml=_mathml(latex, display), speech=latex,
                           display=display)  # fmt: skip

    weak = Check(
        name="heading.evidence", passed=False, severity="warning", code="heading.weak_evidence"
    )

    def teacher_fix(eid: str) -> Fix:
        return Fix(element_id=eid, kind=Kind.HEADING, status=Status.NEEDS_REVIEW, confidence=0.6,
                   heading=HeadingFix(level=2, text="Teacher Support"), checks=[weak],
                   review="required", group_key=GROUP, group_size=3)  # fmt: skip

    fixes = [
        Fix(element_id="p1-e0", kind=Kind.HEADING, status=Status.ACCEPTED, confidence=0.95,
            heading=HeadingFix(level=1, text="1 Vectors")),
        Fix(element_id="p1-e2", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, confidence=0.59,
            equation=equation(r"\omega", "inline"), review="required",
            checks=[Check(name="latex.allowed_commands", passed=True),
                    Check(name="equation.text_layer_style", passed=False, severity="warning",
                          code="style_mismatch")]),
        teacher_fix("p1-e3"),
        Fix(element_id="p1-e5", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.92,
            equation=equation(r"\mathbf{d}_0 = 0"), review="optional",
            checks=[Check(name="equation.text_layer_style", passed=False, severity="warning",
                          code="style_cosmetic")]),
        Fix(element_id="p1-e6", kind=Kind.FIGURE, status=Status.NEEDS_REVIEW, confidence=0.62,
            figure=FigureFix(figure_type="diagram", alt="A car on a road between a house and "
                             "a school."), review="required"),
        teacher_fix("p1-e7"),
        teacher_fix("p2-e0"),
        Fix(element_id="p2-e2", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.97,
            equation=equation("v = d / t")),
    ]  # fmt: skip
    result = DocumentResult(
        pipeline_version="test",
        title="Vectors",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=2, cost_usd=0.01, latency_ms=5, model_calls=4),
        html="<!doctype html><html lang='en'><head><title>Vectors</title></head>"
        "<body><main><h1>Vectors</h1></main></body></html>",
        page_sizes={0: PAGE, 1: PAGE},
    )
    return ConversionOutput(result=result, crops={k: v for k, v in crops.items() if k in {
        "p1-e2", "p1-e5", "p1-e6", "p2-e2"}})  # fmt: skip
