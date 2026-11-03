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
    IMAGE_PAGE_OMISSION_REASON,
    IMAGE_PAGE_ORIGINAL_REASON,
    IMAGE_PAGE_REASON,
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_scale, image_page, run
from readablestem.pipeline import image_page_vector as iv
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


def test_a_picture_is_drawn_from_the_strokes_alone_clean_and_cut_to_the_ink():
    pale = line("ab", y=300)  # strokes that a threshold on a page picture would not see
    pale = [Stroke(s.points, "#f4e8ff", 0.4, s.width, s.order, s.source) for s in pale]
    png = iv.render(pale)
    with Image.open(io.BytesIO(png)) as image:
        assert image.mode == "L"
        assert image.getextrema()[0] < 40  # the pen is drawn black whatever its colour
        w, h = image.size
    assert w == pytest.approx((28 - 8 + 2 * iv.CROP_MARGIN_PT) * iv.CROP_DPI / 72, abs=4)
    assert h == pytest.approx((16 + 2 * iv.CROP_MARGIN_PT) * iv.CROP_DPI / 72, abs=4)


def test_the_picture_says_its_resolution_when_asked():
    png = iv.render(line("a"), dpi=216, tag_dpi=True)
    assert equation_scale.png_dpi(png) == pytest.approx(216, abs=1)
    assert equation_scale.png_dpi(iv.render(line("a"))) is None


def test_a_long_page_of_ink_is_capped_at_the_side_the_crops_are():
    wide = [stroke(0, 0, 3000, 10)]
    with Image.open(io.BytesIO(iv.render(wide))) as image:
        assert max(image.size) <= iv.CROP_MAX_SIDE_PX


def test_a_crop_takes_the_ink_in_the_box_the_reader_gave_and_nothing_else():
    page = iv.VectorPage(0, tuple([*line("ab", y=100), *line("cd", y=300)]), FRAME)
    near = page.crop((90, 80, 140, 110))
    assert near is not None
    with Image.open(io.BytesIO(near)) as image:
        assert image.height < 60 * iv.CROP_DPI / 72  # one line, not two
    assert page.crop((400, 400, 450, 450)) is None  # no ink there: the ordinary crop stands


def test_only_the_elements_of_an_image_page_are_drawn_from_strokes():
    def element(kind: Kind, *reasons: str, bbox: Any = (90, 80, 140, 110)) -> Element:
        return Element(id="e", kind=kind, page=0, bbox=bbox, order=0, reasons=list(reasons))

    assert iv.wants_picture(element(Kind.EQUATION, IMAGE_PAGE_REASON))
    assert iv.wants_picture(element(Kind.PARAGRAPH, IMAGE_PAGE_REASON))
    assert iv.wants_picture(element(Kind.FIGURE, IMAGE_PAGE_OMISSION_REASON))
    assert not iv.wants_picture(element(Kind.FIGURE, IMAGE_PAGE_ORIGINAL_REASON))
    assert not iv.wants_picture(element(Kind.FIGURE, IMAGE_PAGE_REASON))  # a described figure
    assert not iv.wants_picture(element(Kind.TABLE, IMAGE_PAGE_REASON))
    assert not iv.wants_picture(element(Kind.EQUATION))  # a typed page's equation


# ------------------------------------------------------------------------- the glyphs


def test_strokes_that_overlap_sideways_are_one_glyph_and_the_rest_are_apart():
    equals = [stroke(0, 0, 10, 0.5), stroke(0, 4, 10, 4.5)]  # the bars of "="
    cross = [stroke(30, 0, 38, 12), stroke(30, 12, 38, 0)]  # "x"
    apart = [stroke(60, 0, 66, 12)]
    found = glyphs_of([*equals, *cross, *apart])
    assert [g.members for g in found] == [(0, 1), (2, 3), (4,)]
    assert [round(g.h, 1) for g in found] == [4.5, 12, 12]


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


def test_which_letters_can_be_told_by_their_case_is_in_the_reading():
    read = iv.reading(r"x\mathrm{d}y\text{if}")
    assert read is not None
    assert [a.plain for a in read.atoms] == [True, False, True, False, False]


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


def test_a_script_over_a_script_is_marked_stacked_and_too_long_a_reading_is_not_read():
    stacked = iv.reading("x_i^2")
    assert stacked is not None and stacked.stacked
    plain = iv.reading("x^2_i")
    assert plain is not None and plain.stacked
    assert iv.reading("x^{2}y_{i}") is not None and not iv.reading("x^{2}y_{i}").stacked  # type: ignore[union-attr]
    assert iv.reading("a" * (iv.MAX_ATOMS + 1)) is None


# --------------------------------------------------------------------------- the case


def test_a_small_letter_written_as_tall_as_a_capital_is_a_doubt():
    # a (small), b (tall), x written 16 tall, c small
    assert doubts("abXc", "abxc") == [2]
    assert doubts("abXc", "abXc") == []


def test_a_capital_written_as_short_as_a_small_letter_is_a_doubt():
    assert doubts("abxc", "abXc") == [2]
    assert doubts("abxc", "abxc") == []


def test_every_letter_whose_forms_differ_in_height_is_measured_and_k_is_not():
    for letter in "cosuvwxz":
        assert doubts(f"ab{letter.upper()}", f"ab{letter}") == [2], letter
        assert doubts(f"ab{letter}", f"ab{letter.upper()}") == [2], letter
    # k stands as tall as K: ink cannot tell them apart
    assert doubts("abK", "abk") == [] and doubts("abK", "abK") == []


def test_a_letter_with_a_tail_is_not_judged_by_its_height():
    # p and y: the tail hides the height of the letter, so the ink says nothing about the case
    for spec, reading in [("abp", "abP"), ("abp", "abp"), ("abY", "aby")]:
        read = iv.reading(reading)
        assert read is not None
        tall = [*line("ab"), glyph(128, 22, BASE + 6)]
        assert iv.case_doubts(read, glyphs_of(tall)) == [], spec


def test_a_letter_with_no_letter_to_measure_against_is_not_judged_unless_the_page_says():
    read = iv.reading("x")
    assert read is not None
    one = glyphs_of([glyph(100, 16)])
    assert iv.case_doubts(read, one) == []
    assert iv.case_doubts(read, one, iv.Heights(10, 16)) == [0]  # the hand's heights on the page
    assert iv.case_doubts(read, one, iv.Heights(16, 16)) == []


def test_symbols_and_glyphs_that_do_not_pair_one_to_one_are_not_judged():
    read = iv.reading("abXc")
    assert read is not None
    assert iv.case_doubts(read, glyphs_of(line("abX"))) == []  # a glyph missing
    assert iv.case_doubts(read, glyphs_of(line("abXcd"))) == []  # one too many


def test_a_letter_in_a_script_or_a_word_is_not_a_case_doubt():
    assert doubts("abXc", r"ab\text{x}c") == []
    assert doubts("abc^X", "abc^{x}") == []


# -------------------------------------------------------------------------- the script


def test_a_reading_that_agrees_with_the_ink_has_nothing_to_say():
    assert events("abc^2", "abc^{2}") == set()
    assert events("abc_2", "abc_{2}") == set()
    assert events("abcd", "abcd") == set()


def test_a_small_raised_mark_the_reading_has_on_the_line_is_a_lost_script():
    assert events("abc^2", "abc2") == {iv.SCRIPT_LOST}
    assert events("abc_2", "abc2") == {iv.SCRIPT_LOST}


def test_a_small_raised_mark_the_reading_leaves_out_is_a_lost_script():
    assert events("abc^2", "abc") == {iv.SCRIPT_LOST}


def test_a_power_the_reading_has_on_a_symbol_written_full_size_on_the_line_is_added():
    assert events("abc2", "abc^{2}") == {iv.SCRIPT_ADDED}
    assert events("abc2", "abc_{2}") == {iv.SCRIPT_ADDED}


def test_a_power_read_as_a_subscript_or_the_reverse_is_moved():
    assert events("abc^2", "abc_{2}") == {iv.SCRIPT_MOVED}
    assert events("abc_2", "abc^{2}") == {iv.SCRIPT_MOVED}


def test_a_sloped_line_does_not_make_scripts():
    # the baseline drops 0.06 a pt: the last letters sit 3 pt lower than the first
    slope = [glyph(100 + 14 * i, 10, BASE + 0.8 * i) for i in range(6)]
    read = iv.reading("abcdef")
    assert read is not None
    assert iv.script_events(read, glyphs_of(slope)) == set()


def test_what_a_check_cannot_judge_it_leaves_alone():
    read = iv.reading("abc^{2}")
    assert read is not None
    assert iv.script_events(read, glyphs_of(line("a"))) == set()  # one glyph: no line
    stacked = iv.reading("a_b^c")
    assert stacked is not None
    assert iv.script_events(stacked, glyphs_of(line("a^b_c"))) == set()  # written one over other
    scattered = [glyph(100 + 14 * i, 10, BASE + (i % 3) * 9) for i in range(6)]
    plain = iv.reading("abcdef")
    assert plain is not None
    assert iv.script_events(plain, glyphs_of(scattered)) == set()  # no baseline to measure from


def test_a_dot_a_comma_and_a_sign_say_nothing_about_scripts():
    strokes = [*line("abc"), stroke(150, 98, 152, 100), stroke(160, 70, 172, 71)]
    read = iv.reading("abc,-")
    assert read is not None
    assert iv.script_events(read, glyphs_of(strokes)) == set()


# ----------------------------------------------------------------------------- leftover


def page_of(*lines: list[Stroke]) -> iv.VectorPage:
    return iv.VectorPage(0, tuple(s for ln in lines for s in ln), FRAME)


def covers(page: iv.VectorPage, *boxes: tuple[float, float, float, float]):
    return iv.leftover(page, list(boxes))


def test_writing_no_block_is_near_is_a_leftover_region():
    page = page_of(line("abcdef", y=100), line("ghijkl", y=400))
    found = covers(page, (90, 80, 190, 110))
    assert len(found) == 1
    r = found[0]
    assert 400 / 800 * 1000 > r.y0 > 350 and r.y1 > 500 and r.cells >= 6
    assert covers(page, (90, 80, 190, 110), (90, 380, 190, 410)) == []


def test_pale_ink_is_ink():
    pale = [Stroke(s.points, "#f6eeff", 0.3, s.width, s.order, s.source) for s in line("abcdef")]
    assert len(covers(page_of(pale), (400, 600, 500, 700))) == 1


def test_a_boxes_slop_is_allowed_for_but_not_a_line_away():
    page = page_of(line("abcdef", y=100))
    assert covers(page, (90, 55, 190, 80)) == []  # box 12 pt above the ink (a glyph is 16)
    assert len(covers(page, (90, 300, 190, 330))) == 1


def test_a_stray_mark_a_few_glyphs_and_a_page_number_are_not_a_leftover():
    assert covers(page_of(line("ab", y=400)), (90, 80, 190, 110)) == []
    assert covers(page_of(line("abcdef", y=790)), (90, 80, 190, 110)) == []  # page number strip
    assert covers(page_of(line("abcdef", y=30)), (90, 380, 190, 410)) == []  # running header


def test_with_no_box_to_compare_with_there_is_no_leftover():
    assert covers(page_of(line("abcdef", y=400))) == []


def test_stretches_of_leftover_close_together_are_one_region():
    page = page_of(line("abcdef", y=300), line("ghijkl", y=330), line("mnopqr", y=360))
    found = covers(page, (90, 80, 190, 110))
    assert len(found) == 1 and found[0].y1 - found[0].y0 > 60


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


def test_checks_are_added_to_the_fix_and_the_review_stays_as_it_was():
    page = page_of(line("abXc", y=100))
    fix = fix_of("abxc")
    assert iv.add_checks([equation()], {"eq": fix}, {0: page}) == 1
    assert codes(fix) == {(iv.CASE_CHECK, iv.CASE_MISMATCH)}
    assert fix.review == "required" and fix.status is Status.NEEDS_REVIEW
    failed = [c for c in fix.checks if not c.passed]
    assert all(c.severity == "warning" for c in failed)


def test_script_checks_name_what_the_strokes_show():
    page = page_of(line("abc^2", y=100))
    fix = fix_of("abc2")
    iv.add_checks([equation()], {"eq": fix}, {0: page})
    assert codes(fix) == {(iv.SCRIPT_CHECK, iv.SCRIPT_LOST)}


def test_an_agreeing_reading_gets_no_check_and_a_check_is_not_added_twice():
    page = page_of(line("abXc", y=100))
    good, bad = fix_of("abXc"), fix_of("abxc")
    assert iv.add_checks([equation()], {"eq": good}, {0: page}) == 0 and not good.checks
    iv.add_checks([equation()], {"eq": bad}, {0: page})
    assert iv.add_checks([equation()], {"eq": bad}, {0: page}) == 0
    assert len(bad.checks) == 1


def test_a_page_without_strokes_an_inline_formula_and_a_fix_without_a_reading_are_left_alone():
    page = page_of(line("abXc", y=100))
    fix = fix_of("abxc")
    assert iv.add_checks([equation(page=1)], {"eq": fix}, {0: page}) == 0
    assert iv.add_checks([equation(parent="p1")], {"eq": fix}, {0: page}) == 0
    empty = fix.model_copy(update={"equation": None})
    assert iv.add_checks([equation()], {"eq": empty}, {0: page}) == 0
    assert iv.add_checks([equation()], {}, {0: page}) == 0
    assert not fix.checks


def test_a_structure_the_check_does_not_read_is_left_alone():
    page = page_of(line("abXc", y=100))
    fix = fix_of(r"\frac{a}{b}")
    assert iv.add_checks([equation()], {"eq": fix}, {0: page}) == 0


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


def test_every_check_has_the_review_screen_a_sentence():
    from readablestem.web import messages

    for name, code in [
        (iv.CASE_CHECK, iv.CASE_MISMATCH),
        (iv.SCRIPT_CHECK, iv.SCRIPT_LOST),
        (iv.SCRIPT_CHECK, iv.SCRIPT_ADDED),
        (iv.SCRIPT_CHECK, iv.SCRIPT_MOVED),
    ]:
        assert messages.CHECK_MESSAGES[(name, code)]


# ------------------------------------------------------------------ in the image pass


def test_the_omission_check_reads_the_strokes_of_a_vector_page_even_pale_ones():
    pale = [
        Stroke(s.points, "#f6eeff", 0.3, s.width, s.order, s.source)
        for s in line("abcdefgh", y=400)
    ]
    page = page_of(line("abcdef", y=100), pale)
    blocks = [image_page.Block(**block("paragraph", x0=100, y0=100, x1=400, y1=160))]
    with_strokes = image_page.omitted_regions(blocks, None, page)
    assert len(with_strokes) == 1
    assert image_page.omitted_regions(blocks, None) == []  # no page picture, no strokes: none


def test_build_makes_an_omission_element_from_the_strokes():
    page = page_of(line("abcdef", y=100), line("ghijkl", y=400))
    output = image_page.ImagePageOutput(
        blocks=[image_page.Block(**block("paragraph", runs=[{"kind": "text", "value": "ab"}]))]
    )
    output.blocks[0] = output.blocks[0].model_copy(
        update={"x0": 100, "x1": 330, "y0": 90, "y1": 150}
    )
    built = image_page.build(0, output, box=FRAME, png=None, page_ink=page)
    assert built is not None
    assert built.record.omitted == 1
    omitted = [e for e in built.elements if IMAGE_PAGE_OMISSION_REASON in e.reasons]
    assert len(omitted) == 1 and omitted[0].bbox is not None and omitted[0].bbox[1] > 300


def test_a_picture_of_a_line_is_drawn_from_the_strokes_for_the_second_reading():
    page = page_of(line("abcdef", y=100))
    png = Image.new("RGB", (600, 800), "white")
    out = io.BytesIO()
    png.save(out, format="PNG")
    ink = image_page.Ink(out.getvalue())
    drawn = image_page._picture(page, out.getvalue(), ink, (90, 80, 200, 112))
    assert drawn is not None
    with Image.open(io.BytesIO(drawn)) as image:
        assert image.mode == "L"
    plain = image_page._picture(None, out.getvalue(), ink, (90, 80, 200, 112))
    assert plain is not None
    with Image.open(io.BytesIO(plain)) as image:
        assert image.mode == "RGB"
    empty = image_page._picture(page, out.getvalue(), ink, (400, 400, 450, 450))
    assert empty is not None  # no stroke there: the page picture's piece is used
    with Image.open(io.BytesIO(empty)) as image:
        assert image.mode == "RGB"


# -------------------------------------------------------------------------- the pages


def ink_pdf() -> bytes:
    return (HANDWRITING / "ink_no_layer.pdf").read_bytes()


def test_a_handwritten_page_with_vector_ink_has_its_strokes_read():
    found = iv.analyse(ink_pdf(), [0], {0: "handwritten"})
    assert set(found) == {0} and len(found[0].strokes) > 100
    assert found[0].frame[2] > 500
    # only a page `page_type` calls handwritten; a scan or a typed page is read as before
    assert iv.analyse(ink_pdf(), [0], {0: "scan_no_text"}) == {}
    assert iv.analyse(ink_pdf(), [0], {}) == {}
    assert iv.analyse(ink_pdf(), [3], {3: "handwritten"}) == {}


def test_a_page_the_ink_budget_has_no_time_for_is_not_read():
    from readablestem.pipeline.page_type import InkBudget

    spent = InkBudget(seconds=0.0)
    assert iv.analyse(ink_pdf(), [0], {0: "handwritten"}, budget=spent) == {}


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
    _, fake = convert(blocks=[EXPRESSION])
    with equation_picture(fake) as image:
        assert image.mode == "L"
    _, fake = convert(blocks=[EXPRESSION], image_pages_vector_crops=False)
    with equation_picture(fake) as image:
        assert image.mode == "RGB"


def test_the_review_screen_gets_the_same_sharp_picture():
    from readablestem.pipeline.run import convert_pdf

    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = FakeVisionClient(
        responders={
            "image_page": lambda r: {"blocks": [EXPRESSION]},
            "equation": lambda r: {"is_equation": True, "latex": "x", "confidence": 0.95},
        },
        budget=budget,
    )
    out = convert_pdf(ink_pdf(), settings=settings(), client=fake)
    eq = next(e for e in out.result.elements if e.kind is Kind.EQUATION)
    with Image.open(io.BytesIO(out.crops[eq.id])) as image:
        assert image.mode == "L"
    assert equation_scale.png_dpi(out.crops[eq.id]) is not None


def test_ink_the_reading_left_out_is_an_omission_decision_found_from_the_strokes():
    top_left = block("equation", latex="x", x0=130, y0=90, x1=260, y1=130)
    result, _ = convert(blocks=[top_left])
    omitted = [f for f in result.fixes if any(c.code == "omitted_region" for c in c_(f))]
    assert omitted and all(f.review == "required" for f in omitted)
    assert result.image_pages is not None and result.image_pages.per_page[0].omitted >= 1


def c_(fix: Fix):
    return [c for c in fix.checks if not c.passed]


def test_the_checks_land_on_the_equation_fix_and_maths_stays_required():
    result, _ = convert(blocks=[EXPRESSION], latex="x_{2}")
    [eq] = [e for e in result.elements if e.kind is Kind.EQUATION]
    fix = result.fix_for(eq.id)
    assert fix.review == "required" and fix.status is Status.NEEDS_REVIEW
    off, _ = convert(blocks=[EXPRESSION], latex="x_{2}", image_pages_vector_checks=False)
    [eq_off] = [e for e in off.elements if e.kind is Kind.EQUATION]
    assert not any(c.name == iv.SCRIPT_CHECK for c in off.fix_for(eq_off.id).checks)
    assert off.fix_for(eq_off.id).review == "required"


def test_a_scan_is_read_exactly_as_before():
    from image_page_support import scan_pdf

    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = FakeVisionClient(
        responders={
            "image_page": lambda r: {"blocks": [block("equation", latex="x")]},
            "equation": lambda r: {"is_equation": True, "latex": "x", "confidence": 0.95},
        },
        budget=budget,
    )
    run.convert_pdf(scan_pdf(), settings=settings(), client=fake)
    with equation_picture(fake) as image:
        assert image.mode == "RGB"


def test_recrop_draws_a_stored_vector_page_the_way_a_conversion_did():
    result, _ = convert(blocks=[EXPRESSION])
    [eq] = [e for e in result.elements if e.kind is Kind.EQUATION]
    kinds = {p.page: p.kind for p in result.page_types}
    drawn = run.recrop(ink_pdf(), result.elements, kinds)
    with Image.open(io.BytesIO(drawn[eq.id])) as image:
        assert image.mode == "L"
    plain = run.recrop(ink_pdf(), result.elements)
    with Image.open(io.BytesIO(plain[eq.id])) as image:
        assert image.mode == "RGB"
