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
from readablestem.core.types import Element, EquationFix, Fix, Kind, Status
from readablestem.pipeline import equation_twice
from readablestem.pipeline.assemble import render_html


def mathml(latex: str) -> str:
    inner = "".join(f"<mi>{c}</mi>" for c in latex if c.isalnum())
    return f'<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline">{inner}</math>'


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


def reading(number: int, latex: str, status: Status) -> Fix:
    return Fix(
        element_id=f"p1-e{number}",
        kind=Kind.EQUATION,
        status=status,
        equation=EquationFix(latex=latex, mathml=mathml(latex), speech=latex, display="inline"),
    )


ASKED, OK = Status.NEEDS_REVIEW, Status.ACCEPTED
WHOLE = "x = 2p + 3q"


def settled(elements: list[Element], readings: dict[int, tuple[str, Status]]):
    fixes = {f"p1-e{n}": reading(n, latex, status) for n, (latex, status) in readings.items()}
    elements, fixes, spent = equation_twice.settle(elements, fixes)
    html = render_html(
        title="T", lang="en", elements=elements, fixes=list(fixes.values()), crops={}
    )
    return elements, list(fixes.values()), spent, html


@pytest.mark.parametrize(
    ("first", "second", "maths", "decisions", "kept"),
    [
        # the same formula in both boxes, each holding a part: written once
        ((("x = 2p", WHOLE, OK)), (("+ 3q", WHOLE, OK)), 1, 0, "p1-e1"),
        # the accepted copy stays; a question about the other goes with it
        ((("x = 2p", WHOLE, ASKED)), (("+ 3q", WHOLE, OK)), 1, 0, "p1-e2"),
        # neither accepted: the question stays, once
        ((("x = 2p", WHOLE, ASKED)), (("+ 3q", WHOLE, ASKED)), 1, 1, "p1-e1"),
        # the second reading says only its own part, which the first holds
        ((("x = 2p", WHOLE, ASKED)), (("+ 3q", "3q", OK)), 1, 1, "p1-e1"),
        # a symbol printed twice: the letters of the boxes are not the letters of one reading
        ((("x", "x", OK)), (("x", "x", OK)), 2, 0, None),
        # boxes with no characters in the PDF (a handwritten page): nothing to compare with
        ((("", "x", OK)), (("", "x", OK)), 2, 0, None),
        # two formulas with the same letters
        ((("f(n) = g(n)", "f(n) = g(n)", OK)), (("g(n) = f(n)", "g(n) = f(n)", OK)), 2, 0, None),
        # two formulas, the letters of the second among the letters of the first (a reading that
        # goes past its box): in order they are not the same, so both stay
        ((("a + b", "a + b + c", OK)), (("c = b", "c = b", OK)), 2, 0, None),
    ],
)
def test_a_formula_cut_into_two_boxes_is_written_once(first, second, maths, decisions, kept):
    elements = [
        paragraph("so ⟦p1-e1⟧ ⟦p1-e2⟧ holds"),
        box(1, 100, first[0]),
        box(2, 133, second[0]),
    ]
    elements, fixes, spent, html = settled(
        elements, {1: (first[1], first[2]), 2: (second[1], second[2])}
    )
    assert html.count("<math") == maths
    assert triage.required_decisions(fixes) == decisions
    assert [e.id for e in elements if e.kind is Kind.EQUATION] == (
        ["p1-e1", "p1-e2"] if kept is None else [kept]
    )
    assert len(spent) == (0 if kept is None else 1)  # the dropped fix is handed back, for its cost
    assert "⟦" not in html  # no marker is left to show as text


def test_a_formula_cut_into_three_boxes_is_written_once():
    elements = [
        paragraph("so ⟦p1-e1⟧ ⟦p1-e2⟧ ⟦p1-e3⟧ holds"),
        box(1, 100, "x = 2"),
        box(2, 133, "p + 3"),
        box(3, 166, "q"),
    ]
    elements, _, spent, html = settled(elements, {n: (WHOLE, OK) for n in (1, 2, 3)})
    assert html.count("<math") == 1
    assert len(spent) == 2
    assert "⟦" not in html


def test_the_text_between_the_boxes_goes_with_them_and_the_space_before_stays():
    # the 'x' and the 't+1' of one formula, a box with no characters between them, and the
    # words around: the word keeps its space before the formula, the formula its full stop
    elements = [
        paragraph("the optimized decision ⟦p1-e1⟧⟦p1-e2⟧⟦p1-e3⟧. It"),
        box(1, 100, "x"),
        box(2, 100, ""),
        box(3, 130, "t+1"),
        paragraph("therefore, ⟦p1-e5⟧ = ⟦p1-e6⟧.", 4),
        box(5, 100, "x", "p1-e4", 200),
        box(6, 140, "y", "p1-e4", 200),
    ]
    elements, _, spent, _ = settled(
        elements,
        {
            1: ("x", OK),
            2: ("x_{t+1}", ASKED),
            3: ("x_{t+1}", ASKED),
            5: ("x", OK),
            6: ("x = y", ASKED),
        },
    )
    text = {e.id: e.text for e in elements if e.kind is Kind.PARAGRAPH}
    assert text["p1-e0"] == "the optimized decision ⟦p1-e3⟧. It"
    assert text["p1-e4"] == "therefore, ⟦p1-e6⟧."  # no "=" is left behind
    assert len(spent) == 3


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


def test_an_accent_cut_from_its_letter_is_written_once_where_the_boxes_lie_on_each_other():
    elements = [
        paragraph("so ⟦p1-e1⟧"),
        box(1, 100, "~"),
        paragraph("⟦p1-e3⟧ holds", 2),
        box(3, 100, "W", "p1-e2", 104),
    ]
    elements, _, spent, html = settled(elements, {1: ("W~", ASKED), 3: ("W~", OK)})
    assert [e.text for e in elements if e.kind is Kind.PARAGRAPH] == ["so", "⟦p1-e3⟧ holds"]
    assert len(spent) == 1
    assert html.count("<math") == 1


def test_the_run_writes_a_repeated_formula_once_and_keeps_what_it_cost(monkeypatch):
    import pymupdf

    from readablestem.config import Settings
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.models.fake import FakeVisionClient
    from readablestem.pipeline import equation_step, figure_step, layout, run
    from readablestem.pipeline.layout import Layout

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pixmap.set_rect(pixmap.irect, (30, 30, 30))
    crop = pixmap.tobytes("png")
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
        return reading(int(element.id[-1]), WHOLE, ASKED)

    monkeypatch.setattr(layout, "analyze", lambda pdf, **kw: document)
    monkeypatch.setattr(layout, "crop", lambda pdf, element, **kw: crop)
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
    assert "p1-e2" not in out.crops
