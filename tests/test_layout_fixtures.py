"""layout.analyze on realistic PDFs printed by Chromium from tests/fixtures/layout/*.html.

The PDFs are committed (regenerate with tests/fixtures/layout/make_fixtures.py). Chromium
renders MathML with a maths font and math-italic code points, draws fraction bars and
radical overbars as thin filled rectangles, balances columns on its own, and prints
running headers/footers from templates, so these pages exercise the same paths real
course PDFs do.
"""

from __future__ import annotations

import importlib.util
import sys
import unicodedata
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind, inline_marker
from readablestem.pipeline.layout import Layout, analyze, crop, heading_levels

FIXTURES = Path(__file__).parent / "fixtures" / "layout"
NAMES = ("lecture_notes", "two_column", "worked_example")


def load(name: str) -> Layout:
    return analyze((FIXTURES / f"{name}.pdf").read_bytes())


def plain(text: str | None) -> str:
    """NFKC (math-italic letters become plain ones) with all whitespace removed."""
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def outline(layout: Layout) -> list[tuple[int, str]]:
    levels = heading_levels(layout.elements)
    return [(levels[e.id], e.text or "") for e in layout.elements if e.kind == Kind.HEADING]


def children(layout: Layout, parent: Element) -> list[Element]:
    return [e for e in layout.elements if e.parent_id == parent.id]


def paragraph_starting(layout: Layout, start: str) -> Element:
    return next(
        e for e in layout.elements if e.kind == Kind.PARAGRAPH and (e.text or "").startswith(start)
    )


def assert_well_formed(layout: Layout) -> None:
    """Ids unique and per page, order global, every inline marker present exactly once."""
    elements = layout.elements
    assert [e.order for e in elements] == list(range(len(elements)))
    assert len({e.id for e in elements}) == len(elements)
    for page in {e.page for e in elements}:
        ids = [e.id for e in elements if e.page == page]
        assert ids == [f"p{page + 1}-e{i}" for i in range(len(ids))]
    for e in elements:
        if e.kind == Kind.EQUATION and e.display == "inline":
            parent = next(p for p in elements if p.id == e.parent_id)
            assert parent.kind == Kind.PARAGRAPH and parent.order < e.order
            assert (parent.text or "").count(inline_marker(e.id)) == 1
        elif e.kind == Kind.EQUATION:
            assert e.display == "block" and e.parent_id is None


# ---------------------------------------------------------------- lecture notes


def test_lecture_notes_outline_title_and_language():
    layout = load("lecture_notes")
    assert_well_formed(layout)
    assert layout.title == "Motion in One Dimension: Lecture Notes"  # <title> via metadata
    assert layout.lang == "en"  # Chromium's tagged PDF writes /Lang from <html lang>
    assert (layout.page_count, layout.pages) == (2, [0, 1])
    assert outline(layout) == [
        (1, "Chapter 2: Motion in One Dimension"),
        (2, "2.1 Position and Displacement"),
        (3, "Units"),
        (2, "2.2 Speed and Velocity"),
        (2, "2.3 Acceleration"),
        (3, "Free fall"),
    ]


def test_lecture_notes_running_header_and_page_footer_are_gone():
    layout = load("lecture_notes")
    for element in layout.elements:
        text = element.text or ""
        assert "PHYS 101" not in text and "Motion notes" not in text
        assert not text.startswith("Page ")


def test_lecture_notes_display_equations_with_fraction_bars_and_a_radical():
    layout = load("lecture_notes")
    blocks = [e for e in layout.elements if e.kind == Kind.EQUATION and e.display == "block"]
    assert [(e.page, plain(e.text)) for e in blocks] == [
        (0, "\u0394x=xf\u2212x0"),
        (0, "v=\u0394x\u0394t"),
        (0, "v=\u221a2Km"),
        (1, "a=\u0394v\u0394t"),
    ]
    reasons = [set(e.reasons) for e in blocks]
    assert "fraction bar" not in reasons[0]
    assert {"fraction bar", "2 lines merged"} <= reasons[1]
    assert {"fraction bar", "radical sign", "2 lines merged"} <= reasons[2]
    assert {"fraction bar", "2 lines merged"} <= reasons[3]
    assert all("math font" in r for r in reasons)


def test_lecture_notes_inline_equations_and_paragraph_markers():
    layout = load("lecture_notes")
    inline = [e for e in layout.elements if e.kind == Kind.EQUATION and e.display == "inline"]
    assert [plain(e.text) for e in inline] == [
        "x0",
        "xf",
        "\u22123m",
        "\u0394t",
        "v=\u0394x/\u0394t",
        "g",
    ]
    position = paragraph_starting(layout, "Position is measured")
    x0, xf = children(layout, position)
    assert position.text == (
        "Position is measured from a chosen origin. When a cart moves from an initial position "
        f"{inline_marker(x0.id)} to a final position {inline_marker(xf.id)}, its displacement "
        "is the change in position:"
    )
    units = paragraph_starting(layout, "Positions and displacements")
    # "3 m" typed as <mn>3</mn><mtext>m</mtext> has no maths font or symbol: it stays text.
    assert "A displacement of 3 m to the left" in (units.text or "")
    acceleration = paragraph_starting(layout, "Acceleration is the rate")
    # "3 m/s2" has no maths font: a unit with a raised 2 is text, written with <sup> (T2-4).
    assert (acceleration.text or "").endswith("average acceleration of 3 m / s2 .")
    assert [
        ((acceleration.text or "")[r.start : r.end], r.kind) for r in acceleration.script_runs
    ] == [("2", "sup")]


def test_lecture_notes_figure_and_reading_order():
    layout = load("lecture_notes")
    kinds = [(e.page, e.kind.value) for e in layout.elements if e.display != "inline"]
    assert kinds == [
        (0, "heading"),
        (0, "paragraph"),
        (0, "heading"),
        (0, "paragraph"),
        (0, "equation"),
        (0, "paragraph"),
        (0, "heading"),
        (0, "paragraph"),
        (0, "heading"),
        (0, "paragraph"),
        (0, "equation"),
        (0, "paragraph"),
        (0, "equation"),
        (0, "figure"),
        (1, "heading"),
        (1, "paragraph"),
        (1, "equation"),
        (1, "paragraph"),
        (1, "heading"),
        (1, "paragraph"),
    ]
    (figure,) = [e for e in layout.elements if e.kind == Kind.FIGURE]
    assert figure.caption == (
        "Figure 2.1 Position against time for a cart moving at constant velocity."
    )
    assert figure.reasons == ["vector drawing (5 paths)"]
    x0, y0, x1, y1 = figure.bbox
    assert (round(x1 - x0), round(y1 - y0)) == (195, 90)  # drawn part of the SVG, in pt


def test_lecture_notes_equation_crop_matches_its_box():
    data = (FIXTURES / "lecture_notes.pdf").read_bytes()
    equation = next(e for e in load("lecture_notes").elements if e.kind == Kind.EQUATION)
    pixmap = pymupdf.Pixmap(crop(data, equation, dpi=200, pad=2.0))
    x0, y0, x1, y1 = equation.bbox
    scale = 200 / 72
    assert abs(pixmap.width - (x1 - x0 + 4) * scale) <= 2
    assert abs(pixmap.height - (y1 - y0 + 4) * scale) <= 2


# ---------------------------------------------------------------- two columns


def test_two_column_reading_order_and_a_paragraph_split_by_the_column_break():
    layout = load("two_column")
    assert_well_formed(layout)
    sequence = [
        "figure" if e.kind == Kind.FIGURE else (e.text or "").split(".")[0]
        for e in layout.elements
        if e.display != "inline"
    ]
    assert sequence == [
        "2",  # the "2.4 Free Fall" heading
        "Introduction",
        "Paragraph A",
        "Paragraph B",
        "Paragraph C",
        "Paragraph D",
        "figure",
        "Paragraph E",
        "Summary",
    ]
    assert outline(layout) == [(1, "2.4 Free Fall")]
    # Chromium put the start of D at the bottom of the left column: it is one paragraph.
    assert paragraph_starting(layout, "Paragraph D").text == (
        "Paragraph D. A simple experiment drops a ball past two light gates and records the "
        "time between them. Repeating the drop several times reduces random error."
    )
    (figure,) = [e for e in layout.elements if e.kind == Kind.FIGURE]
    assert figure.caption == "Figure 2.4 Distance fallen against time for a dropped ball."
    assert figure.bbox[0] > 306  # in the right column
    b = paragraph_starting(layout, "Paragraph B")
    assert [plain(e.text) for e in children(layout, b)] == ["t", "v=gt"]


# ---------------------------------------------------------------- worked example


def test_worked_example_structure():
    layout = load("worked_example")
    assert_well_formed(layout)
    assert layout.title == "Kinematics Review"  # metadata says "Untitled document"
    assert outline(layout) == [
        (1, "Kinematics Review"),
        (3, "Constant Acceleration"),  # raw level; heading_step repairs h1 -> h3
        (4, "Worked example"),
        (2, "Summary"),
    ]
    simplest = paragraph_starting(layout, "With a constant acceleration")
    assert children(layout, simplest) == []  # the italic word "simplest" is not maths
    (derivation,) = [e for e in layout.elements if e.kind == Kind.EQUATION and e.display == "block"]
    assert plain(derivation.text) == "vf=v0+at=0+2\u00d73=6"
    assert "2 lines merged" in derivation.reasons


def test_worked_example_raster_figure_with_caption_above():
    layout = load("worked_example")
    (figure,) = [e for e in layout.elements if e.kind == Kind.FIGURE]
    assert figure.caption == "Figure 3 A cart on a ramp, drawn as a simple block on a slope."
    assert figure.reasons == ["raster image"]
    x0, y0, x1, y1 = figure.bbox
    assert (round(x1 - x0), round(y1 - y0)) == (216, 126)  # the CSS size of the <img>
    assert not any("Figure 3" in (e.text or "") for e in layout.elements)


def test_worked_example_plain_italic_maths_is_inline():
    layout = load("worked_example")
    ramp = paragraph_starting(layout, "The ramp in the figure")
    first, second = children(layout, ramp)
    assert ramp.text == (
        f"The ramp in the figure is smooth, so friction can be ignored. Here "
        f"{inline_marker(first.id)} and {inline_marker(second.id)}, typed as ordinary text "
        "rather than as an equation."
    )
    assert (first.text, second.text) == ("a = 2 m/s2", "t = 3 s")
    assert first.reasons == second.reasons == ["italic variable with operator"]


# ---------------------------------------------------------------- the files themselves


@pytest.mark.parametrize("name", NAMES)
def test_fixture_files_are_small_and_have_their_sources(name):
    assert (FIXTURES / f"{name}.pdf").stat().st_size < 300_000
    assert (FIXTURES / f"{name}.html").is_file()


def _make_fixtures_module():
    spec = importlib.util.spec_from_file_location("make_fixtures", FIXTURES / "make_fixtures.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.mark.browser
def test_regenerated_fixtures_give_the_same_structure(tmp_path):
    """Re-print the HTML with Chromium; fonts may move text, the structure must not."""
    pytest.importorskip("playwright")
    written = _make_fixtures_module().build(tmp_path)
    assert sorted(p.name for p in written) == sorted(f"{n}.pdf" for n in NAMES)
    for name in NAMES:
        fresh = analyze((tmp_path / f"{name}.pdf").read_bytes())
        committed = load(name)
        assert outline(fresh) == outline(committed)
        assert [(e.kind, e.display) for e in fresh.elements] == [
            (e.kind, e.display) for e in committed.elements
        ]
        assert [e.caption for e in fresh.elements] == [e.caption for e in committed.elements]
