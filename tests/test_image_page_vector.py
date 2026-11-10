"""What the pen strokes of a handwritten vector page add to the image page pass
(`pipeline.image_page_vector`): clean pictures drawn from the strokes, the case check, the script
check and the leftover-ink check. The strokes are generated (a glyph is one diagonal stroke of a
known height at a known place) and the readings are written by hand: no model is asked.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from image_page_support import block
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import (
    IMAGE_PAGE_REASON,
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import image_page_vector as iv
from readablestem.pipeline import run
from readablestem.pipeline.ink_strokes import Stroke

HANDWRITING = Path(__file__).parent / "fixtures" / "handwriting"
FRAME = (0.0, 0.0, 600.0, 800.0)
BASE = 100.0  # the baseline of the generated lines


def stroke(x0: float, y0: float, x1: float, y1: float, colour: str = "#101020") -> Stroke:
    """A pen stroke from one corner of a box to the other (a glyph of that size)."""
    pts = tuple((x0 + (x1 - x0) * i / 8, y0 + (y1 - y0) * i / 8) for i in range(9))
    return Stroke(pts, colour, 1.0, 1.2, 0, "path")


def glyph(x: float, height: float, bottom: float = BASE, width: float = 6.0, **kw: Any) -> Stroke:
    return stroke(x, bottom - height, x + width, bottom, **kw)


def line(spec: str, *, y: float = BASE) -> list[Stroke]:
    """One glyph per character: a small letter is 10 tall, a capital, a digit or an ascender 16;
    '^' before a character raises it as a script (6 tall, its bottom 9 above the baseline), '_'
    lowers it (bottom 4 below)."""
    out: list[Stroke] = []
    x = 100.0
    mode = ""
    for ch in spec:
        if ch in "^_":
            mode = ch
            continue
        if mode == "^":
            out.append(glyph(x, 6, y - 9))
        elif mode == "_":
            out.append(glyph(x, 6, y + 4))
        else:
            tall = ch.isupper() or ch.isdigit() or ch in iv.ASCENDERS
            out.append(glyph(x, 16 if tall else 10, y))
        mode = ""
        x += 14
    return out


def glyphs_of(strokes: list[Stroke]) -> list[iv.Glyph]:
    found = iv.glyphs(strokes)
    assert found is not None
    return found


def doubts(spec: str, latex: str) -> list[int]:
    read = iv.reading(latex)
    assert read is not None
    return iv.case_doubts(read, glyphs_of(line(spec)))


def events(spec: str, latex: str) -> set[str]:
    read = iv.reading(latex)
    assert read is not None
    return iv.script_events(read, glyphs_of(line(spec)))


# ------------------------------------------------------------------------ the picture


def test_a_crop_takes_the_ink_in_the_box_the_reader_gave_and_nothing_else():
    page = iv.VectorPage(0, tuple([*line("ab", y=100), *line("cd", y=300)]), FRAME)
    near = page.crop((90, 80, 140, 110))
    assert near is not None
    with Image.open(io.BytesIO(near)) as image:
        assert image.height < 60 * iv.CROP_DPI / 72  # one line, not two
    assert page.crop((400, 400, 450, 450)) is None  # no ink there: the ordinary crop stands


# ------------------------------------------------------------------------- the glyphs


def test_grouping_gives_up_on_a_page_it_cannot_afford():
    many = [stroke(i % 40, (i // 40) * 0.01, i % 40 + 30, (i // 40) * 0.01 + 1) for i in range(400)]
    assert iv.glyphs(many) is not None
    old = iv.MAX_GLYPH_PAIRS
    iv.MAX_GLYPH_PAIRS = 10
    try:
        assert iv.glyphs(many) is None
    finally:
        iv.MAX_GLYPH_PAIRS = old


# ------------------------------------------------------------------------- the reading


def levels(latex: str) -> list[tuple[str, int]]:
    read = iv.reading(latex)
    assert read is not None
    return [(a.ch, a.level) for a in read.atoms]


def test_a_reading_is_symbols_with_the_level_each_is_written_at():
    assert levels("x^{2}+y_i") == [("x", 0), ("2", 1), ("+", 0), ("y", 0), ("i", -1)]
    assert levels(r"\alpha_{n}\leq\sin x") == [
        ("alpha", 0),
        ("n", -1),
        ("leq", 0),
        ("s", 0),
        ("i", 0),
        ("n", 0),
        ("x", 0),
    ]
    assert levels(r"a\ldots b") == [("a", 0), (".", 0), (".", 0), (".", 0), ("b", 0)]
    assert levels(r"\hat{x}\,y") == [("x", 0), ("y", 0)]


@pytest.mark.parametrize(
    "latex",
    [
        r"\frac{a}{b}",
        r"\sqrt{x}",
        r"\sum_{i}x_i",
        r"\int f",
        r"\overline{xy}",
        r"\begin{matrix}a\end{matrix}",
        "a\\\\b",
        "a & b",
        "x^",
        "x^}",
    ],
)
def test_a_reading_that_stacks_or_encloses_symbols_is_not_lined_up(latex: str):
    assert iv.reading(latex) is None


# --------------------------------------------------------------------------- the case


# -------------------------------------------------------------------------- the script


def test_a_power_the_reading_has_on_a_symbol_written_full_size_on_the_line_is_added():
    assert events("abc2", "abc^{2}") == {iv.SCRIPT_ADDED}
    assert events("abc2", "abc_{2}") == {iv.SCRIPT_ADDED}


def test_a_power_read_as_a_subscript_or_the_reverse_is_moved():
    assert events("abc^2", "abc_{2}") == {iv.SCRIPT_MOVED}
    assert events("abc_2", "abc^{2}") == {iv.SCRIPT_MOVED}


# ----------------------------------------------------------------------------- leftover


def page_of(*lines: list[Stroke]) -> iv.VectorPage:
    return iv.VectorPage(0, tuple(s for ln in lines for s in ln), FRAME)


def covers(page: iv.VectorPage, *boxes: tuple[float, float, float, float]):
    return iv.leftover(page, list(boxes))


def test_a_stray_mark_a_few_glyphs_and_a_page_number_are_not_a_leftover():
    assert covers(page_of(line("ab", y=400)), (90, 80, 190, 110)) == []
    assert covers(page_of(line("abcdef", y=790)), (90, 80, 190, 110)) == []  # page number strip
    assert covers(page_of(line("abcdef", y=30)), (90, 380, 190, 410)) == []  # running header


# ---------------------------------------------------------------------------- checks


def equation(page: int = 0, *, bbox=(90, 80, 200, 112), parent: str | None = None, id_="eq"):
    return Element(
        id=id_,
        kind=Kind.EQUATION,
        page=page,
        bbox=bbox,
        order=0,
        reasons=[IMAGE_PAGE_REASON],
        parent_id=parent,
    )


def fix_of(latex: str, id_: str = "eq", review: str = "required") -> Fix:
    return Fix(
        element_id=id_,
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        confidence=0.9,
        equation=EquationFix(latex=latex, mathml="<math/>", speech="s"),
        review=review,  # type: ignore[arg-type]
    )


def codes(fix: Fix) -> set[tuple[str, str | None]]:
    return {(c.name, c.code) for c in fix.checks if not c.passed}


def test_script_checks_name_what_the_strokes_show():
    page = page_of(line("abc^2", y=100))
    fix = fix_of("abc2")
    iv.add_checks([equation()], {"eq": fix}, {0: page})
    assert codes(fix) == {(iv.SCRIPT_CHECK, iv.SCRIPT_LOST)}


def test_the_hands_heights_on_the_page_stand_in_for_a_line_with_nothing_to_measure_against():
    # the first two lines show an x-height of 10 and a capital height of 16; the third is a
    # lone x written 16 tall
    page = page_of(
        line("abc", y=100),
        line("abc", y=200),
        [glyph(100, 16, 300)],
    )
    equations = [
        equation(bbox=(90, 80, 200, 112), id_="e1"),
        equation(bbox=(90, 180, 200, 212), id_="e2"),
        equation(bbox=(90, 280, 200, 312), id_="e3"),
    ]
    fixes = {
        "e1": fix_of("abc", "e1"),
        "e2": fix_of("abc", "e2"),
        "e3": fix_of("x", "e3"),
    }
    iv.add_checks(equations, fixes, {0: page})
    assert codes(fixes["e3"]) == {(iv.CASE_CHECK, iv.CASE_MISMATCH)}
    assert not fixes["e1"].checks and not fixes["e2"].checks


# ------------------------------------------------------------------ in the image pass


# -------------------------------------------------------------------------- the pages


def ink_pdf() -> bytes:
    return (HANDWRITING / "ink_no_layer.pdf").read_bytes()


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, feature_image_pages=True, **kw)  # type: ignore[call-arg]


def convert(*, blocks: list[dict[str, Any]], latex: str = "x", **config: Any):
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = FakeVisionClient(
        responders={
            "image_page": lambda r: {"blocks": blocks},
            "equation": lambda r: {"is_equation": True, "latex": latex, "confidence": 0.95},
        },
        budget=budget,
    )
    result = run.convert_pdf(ink_pdf(), settings=settings(**config), client=fake).result
    return result, fake


EXPRESSION = block("equation", latex="x", x0=130, y0=90, x1=920, y1=220)


def equation_picture(fake: FakeVisionClient) -> Image.Image:
    request = next(r for r in fake.requests if r.step == "equation")
    return Image.open(io.BytesIO(request.images[0]))


def test_the_model_is_sent_the_picture_drawn_from_the_strokes():
    _, fake = convert(blocks=[EXPRESSION], image_pages_vector_crops=True)
    with equation_picture(fake) as image:
        assert image.mode == "L"
    _, fake = convert(blocks=[EXPRESSION])  # off by default: the ordinary crop
    with equation_picture(fake) as image:
        assert image.mode == "RGB"


def test_ink_the_reading_left_out_is_an_omission_decision_found_from_the_strokes():
    top_left = block("equation", latex="x", x0=130, y0=90, x1=260, y1=130)
    result, _ = convert(blocks=[top_left])
    omitted = [f for f in result.fixes if any(c.code == "omitted_region" for c in c_(f))]
    assert omitted and all(f.review == "required" for f in omitted)
    assert result.image_pages is not None and result.image_pages.per_page[0].omitted >= 1


def c_(fix: Fix):
    return [c for c in fix.checks if not c.passed]
