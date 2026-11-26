"""One formula that layout cut into two boxes is written once (`pipeline.equation_twice`).

Each box is read with its line and the reading goes on past the box, so more than one box comes
back as the whole formula. The settle step takes the repeat out where the readings are put into the
page, on the evidence of the file's characters in order: a symbol the paper prints twice, or two
formulas that have the same letters, stay twice. Hand-made elements and fixes, as in
`test_table_math.py`.
"""

from __future__ import annotations

import pytest

from readablestem.core import triage
from readablestem.core.types import Check, Element, EquationFix, Fix, Kind, ModelCall, Status
from readablestem.pipeline import equation_twice
from readablestem.pipeline.assemble import render_html

MATH = '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline">'


def mathml(latex: str) -> str:
    if latex.startswith("<"):
        return f"{MATH}{latex}</math>"
    inner = "".join(
        f"<mi>{c}</mi>" if c.isalnum() else f"<mo>{c}</mo>" for c in latex if not c.isspace()
    )
    return f"{MATH}{inner}</math>"


def box(number: int, x0: float, text: str, parent: str = "p1-e0", y0: float = 100) -> Element:
    return Element(
        id=f"p1-e{number}",
        kind=Kind.EQUATION,
        page=0,
        bbox=(x0, y0, x0 + 30, y0 + 10),
        order=number,
        text=text,
        display="inline",
        parent_id=parent,
    )


def paragraph(text: str, number: int = 0) -> Element:
    return Element(
        id=f"p1-e{number}",
        kind=Kind.PARAGRAPH,
        page=0,
        bbox=(72, 98, 300, 112),
        order=number,
        text=text,
    )


def reading(number: int, latex: str, status: Status, failed: int = 0) -> Fix:
    return Fix(
        element_id=f"p1-e{number}",
        kind=Kind.EQUATION,
        status=status,
        equation=EquationFix(latex=latex, mathml=mathml(latex), speech=latex, display="inline"),
        checks=[
            Check(name=f"equation.c{i}", passed=False, severity="warning") for i in range(failed)
        ],
    )


ASKED, OK = Status.NEEDS_REVIEW, Status.ACCEPTED
WHOLE = "x = 2p + 3q"
SUB = "<msub><mi>x</mi><mi>t</mi></msub>"
SUP = "<msup><mi>x</mi><mi>t</mi></msup>"


def settled(elements: list[Element], readings: dict[int, tuple]):
    fixes = {f"p1-e{n}": reading(n, *given) for n, given in readings.items()}
    elements, fixes, spent, _ = equation_twice.settle(elements, fixes)
    html = render_html(
        title="T", lang="en", elements=elements, fixes=list(fixes.values()), crops={}
    )
    return elements, list(fixes.values()), spent, html


@pytest.mark.parametrize(
    ("boxes", "readings", "decisions", "kept"),
    [
        # the same formula in both boxes, each holding a part: written once
        (["x = 2p", "+ 3q"], [(WHOLE, OK), (WHOLE, OK)], 0, ["p1-e1"]),
        # three boxes
        (["x = 2", "p + 3", "q"], [(WHOLE, OK)] * 3, 0, ["p1-e1"]),
        # the accepted copy stays; a question about the other goes with it
        (["x = 2p", "+ 3q"], [(WHOLE, ASKED), (WHOLE, OK)], 0, ["p1-e2"]),
        # neither accepted: asked once, about the copy with fewer failed checks
        (["x = 2p", "+ 3q"], [(WHOLE, ASKED, 2), (WHOLE, ASKED, 1)], 1, ["p1-e2"]),
        # the second reading says only its own part, which the first holds
        (["x = 2p", "+ 3q"], [(WHOLE, ASKED), ("3q", OK)], 1, ["p1-e1"]),
        # the second reading says its part another way (a sign the first does not have): both stay
        (["x = 2p", "+ 3q"], [(WHOLE, OK), ("3 - q", OK)], 0, None),
        # a symbol printed twice: the letters of the boxes are not the letters of one reading
        (["x", "x"], [("x", OK), ("x", OK)], 0, None),
        # boxes with no characters in the PDF (a handwritten page): nothing to compare with
        (["", ""], [("+", OK), ("+", OK)], 0, None),
        # the second box stands on the next line
        (["x = 2p", ("+ 3q", 114)], [(WHOLE, OK), (WHOLE, OK)], 0, None),
        # two accepted readings of the whole that say it two ways: nobody would see the other
        (["x", "t"], [(SUB, OK), (SUP, OK)], 0, None),
        # the same two when the one that stays is asked: once, with its question
        (["x", "t"], [(SUB, ASKED), (SUP, ASKED)], 1, ["p1-e1"]),
        # an "=" boxed alone that the reading says at that place goes with the window
        (["x", "=", "y"], [("x = y", OK), ("=", OK), ("y", OK)], 0, ["p1-e1"]),
        # the bars of |v| in one box and its letter in the next: the bars' reading says the whole
        (["| |", "v"], [("|v|", OK), ("v", OK)], 0, ["p1-e1"]),
        # one the reading does not say there is a sign the paper prints: it stays
        (["x = y", "=", "z"], [("x = y", OK), ("=", OK), ("z", OK)], 0, None),
        # five boxes: longer than any real repeat, not looked at
        (["x", "= 2", "p +", "3", "q"], [(WHOLE, OK)] * 5, 0, None),
    ],
)
def test_a_formula_cut_into_boxes_on_one_line_is_written_once(boxes, readings, decisions, kept):
    ids = [f"p1-e{n}" for n in range(1, len(boxes) + 1)]
    elements = [paragraph("so " + " ".join(f"⟦{i}⟧" for i in ids) + " holds")]
    for n, given in enumerate(boxes, start=1):
        text, y0 = given if isinstance(given, tuple) else (given, 100)
        elements.append(box(n, 67 + 33 * n, text, y0=y0))
    elements, fixes, spent, html = settled(elements, dict(enumerate(readings, start=1)))
    assert [e.id for e in elements if e.kind is Kind.EQUATION] == (kept or ids)
    assert html.count("<math") == len(kept or ids)
    assert triage.required_decisions(fixes) == decisions
    # each dropped fix is handed back, for its cost
    assert len(spent) == (len(ids) - 1 if kept else 0)
    assert "⟦" not in html  # no marker is left to show as text


def test_the_copy_that_stays_takes_the_whole_formula_and_the_text_around_stays():
    # the 'x' and the 't+1' of one formula, a box with no characters between them, and the
    # words around: the word keeps its space before the formula, the formula its full stop
    elements = [
        paragraph("the optimized decision ⟦p1-e1⟧⟦p1-e2⟧⟦p1-e3⟧. It"),
        box(1, 100, "x"),
        box(2, 100, ""),
        box(3, 130, "t+1"),
        paragraph("therefore, ⟦p1-e5⟧ = ⟦p1-e6⟧, and ⟦p1-e12⟧ ≤ ⟦p1-e13⟧ too.", 4),
        box(5, 100, "x", "p1-e4", 200),
        box(6, 140, "y", "p1-e4", 200),
        box(12, 200, "x", "p1-e4", 200),
        box(13, 240, "y", "p1-e4", 200),
        # the reading closes a bracket the first box opened; layout left its end in the text
        paragraph("with ⟦p1-e8⟧ ⟦p1-e9⟧ ), and (see ⟦p1-e10⟧ ⟦p1-e11⟧) so", 7),
        box(8, 100, "t = log(C", "p1-e7", 300),
        box(9, 140, "n", "p1-e7", 300),
        box(10, 200, "a + ", "p1-e7", 300),
        box(11, 240, "b", "p1-e7", 300),
    ]
    elements, _, spent, _ = settled(
        elements,
        {
            1: ("x", OK),
            2: ("x_{t+1}", ASKED),
            3: ("x_{t+1}", ASKED),
            5: ("x", OK),
            6: ("x = y", ASKED),
            12: ("xy", OK),
            13: ("y", OK),
            8: ("t = log(Cn)", ASKED),
            9: ("n", OK),
            10: ("(a + b)", OK),
            11: ("b", OK),
        },
    )
    by_id = {e.id: e for e in elements}
    assert by_id["p1-e0"].text == "the optimized decision ⟦p1-e3⟧. It"
    # no "=" is left behind; a sign the reading does not say stays, with both its formulas
    assert by_id["p1-e4"].text == "therefore, ⟦p1-e6⟧, and ⟦p1-e12⟧ ≤ ⟦p1-e13⟧ too."
    # a bracket the window opened goes with the copy; one the sentence opened stays
    assert by_id["p1-e7"].text == "with ⟦p1-e8⟧, and (see ⟦p1-e10⟧) so"
    assert len(spent) == 5
    # the copy that stays holds the whole formula: what a kept original shows and says
    assert (by_id["p1-e3"].bbox, by_id["p1-e3"].text) == ((100, 100, 160, 110), "xt+1")
    # (with room for the bracket, which stood outside both boxes, after a space)
    assert (by_id["p1-e8"].bbox, by_id["p1-e8"].text) == ((100, 300, 173.5, 310), "t = log(C n )")


def test_a_formula_inside_a_sentence_is_not_taken_out_for_a_display_over_it():
    display = box(2, 90, "OP", y0=96).model_copy(
        update={"display": "block", "bbox": (90, 96, 250, 118), "parent_id": None}
    )
    elements = [
        paragraph("After dividing by ⟦p1-e1⟧, the equation says"),
        box(1, 150, "OP"),
        display,
    ]
    elements, _, spent, html = settled(elements, {1: ("OP", OK), 2: ("OP = 1", OK)})
    assert spent == []
    assert elements[0].text == "After dividing by ⟦p1-e1⟧, the equation says"
    assert html.count("<math") == 2


def test_the_run_writes_a_repeated_formula_once_with_its_whole_picture_and_what_it_cost(
    monkeypatch,
):
    import pymupdf

    from readablestem.config import Settings
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.models.fake import FakeVisionClient
    from readablestem.pipeline import equation_step, figure_step, layout, run
    from readablestem.pipeline.layout import Layout

    opened: list[str] = []  # what layout was asked for, in order

    def crop(pdf, element, **kw) -> bytes:  # a picture as wide as the element's box
        opened.append("crop")
        width = round(element.bbox[2] - element.bbox[0])
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, 20), False)
        pixmap.set_rect(pixmap.irect, (30, 30, 30))
        return pixmap.tobytes("png")

    paragraph = Element(
        id="p1-e0",
        kind=Kind.PARAGRAPH,
        page=0,
        bbox=(72, 98, 300, 112),
        order=0,
        text="so ⟦p1-e1⟧ ⟦p1-e2⟧ holds",
    )
    document = Layout(
        elements=[paragraph, box(1, 100, "x = 2p"), box(2, 133, "+ 3q")],
        page_count=1,
        pages=[0],
        title="T",
        lang="en",
    )

    def transcribe(element, png, *, client, settings, context_text=None, **kw) -> Fix:
        call = ModelCall(step="equation", model="m", prompt_version="v", cost_usd=0.01)
        return reading(int(element.id[-1]), WHOLE, ASKED).model_copy(update={"calls": [call]})

    monkeypatch.setattr(layout, "analyze", lambda pdf, **kw: document)
    monkeypatch.setattr(layout, "crop", crop)
    monkeypatch.setattr(layout, "release", lambda pdf=None: opened.append("release"))
    monkeypatch.setattr(equation_step, "transcribe", transcribe)
    monkeypatch.setattr(figure_step, "describe", lambda *a, **k: None)
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    out = run.convert_pdf(
        b"%PDF-",
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=FakeVisionClient(budget=budget),
    )
    result = out.result
    assert result.html.count("<math") == 1
    assert [f.element_id for f in result.fixes] == ["p1-e1"]
    assert result.stats.required_decisions == 1  # asked once, not twice
    # the picture of the copy that stays is cut again, as wide as both boxes
    assert set(out.crops) == {"p1-e1"}
    assert pymupdf.Pixmap(out.crops["p1-e1"]).width == 63
    assert opened[-1] == "release"  # the second cut does not leave the file in layout's cache
    assert (result.stats.model_calls, result.stats.cost_usd) == (2, 0.02)  # both were paid for
