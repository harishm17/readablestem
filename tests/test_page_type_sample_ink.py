"""Pen strokes drawn as one small filled shape per pen sample (a note-app export style) are
handwriting to `page_type`, by the same rule `ink_strokes` reads them with; dot-grid paper,
scatter-plot markers and stippled shading made of the same small discs are not."""

from __future__ import annotations

import math
import random

import pymupdf

from readablestem.pipeline import page_type


def _kind(draw) -> page_type.PageTypeResult:
    doc = pymupdf.open()
    page = doc.new_page()
    draw(page)
    raw = page.get_text("rawdict", flags=page_type.TEXT_FLAGS)["blocks"]
    result = page_type.classify(page, raw, page.get_cdrawings())
    doc.close()
    return result


def _write(page: pymupdf.Page, *, step: float = 0.9, radius: float = 0.9) -> None:
    """Rows of looping pen strokes, each drawn as a disc per sample."""
    rnd = random.Random(7)
    shape = page.new_shape()
    for row in range(8):
        for k in range(9):
            cx, cy = 45 + 55 * k + rnd.uniform(-3, 3), 90 + 70 * row
            r, sweep = rnd.uniform(9, 16), rnd.uniform(4.3, 5.6)
            a0 = rnd.uniform(0, 6)
            wob = rnd.uniform(0.04, 0.09)
            n = int(sweep * r / step)
            for t in range(n):
                a = a0 + sweep * t / n
                f = 1 + wob * math.sin(5 * a) + 0.04 * math.sin(11 * a)
                shape.draw_circle(
                    (cx + 0.8 * r * f * math.cos(a), cy + r * f * math.sin(a)), radius
                )
                shape.finish(fill=(0.1, 0.1, 0.3), color=None)
    shape.commit()


def test_a_page_of_per_sample_pen_strokes_is_handwritten():
    result = _kind(_write)
    assert result.kind == "handwritten", (result.kind, result.reasons, result.ink)
    assert result.ink.strokes >= page_type.INK_PAGE_STROKES


def test_a_dot_grid_template_is_not_handwriting():
    def draw(page):
        shape = page.new_shape()
        for spacing in (1.0,):  # dots that touch: the densest template there is
            for y in range(20, 800, 12):
                for x in range(20, 580, 12):
                    for dx in range(3):
                        shape.draw_circle((x + dx * spacing, y), 0.6)
        shape.finish(fill=(0.6, 0.6, 0.6), color=None)
        shape.commit()

    assert _kind(draw).kind != "handwritten"

    def plain(page):
        shape = page.new_shape()
        for y in range(20, 820, 14):
            for x in range(20, 580, 14):
                shape.draw_circle((x, y), 0.7)
                shape.finish(fill=(0.5, 0.5, 0.5), color=None)
        shape.commit()

    result = _kind(plain)
    assert result.kind != "handwritten" and result.ink.strokes == 0


def test_a_scatter_plot_figure_is_not_handwriting():
    rnd = random.Random(3)

    def draw(page):
        shape = page.new_shape()
        shape.draw_rect((80, 100, 520, 500))
        shape.finish(color=(0, 0, 0), width=1)
        for _ in range(400):  # markers: discs, some overlapping their neighbours
            shape.draw_circle((rnd.uniform(90, 510), rnd.uniform(110, 490)), 3.0)
            shape.finish(fill=(0.2, 0.4, 0.8), color=None)
        shape.commit()

    result = _kind(draw)
    assert result.kind != "handwritten", (result.kind, result.reasons)


def test_a_scatter_plot_drawn_series_by_series_in_a_dense_sweep_is_not_handwriting():
    def draw(page):
        shape = page.new_shape()
        for series, colour in enumerate([(0.8, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.3, 0.8)]):
            for i in range(240):  # markers painted in x order, neighbours touching
                x = 60 + 2.2 * i
                y = 200 + 120 * series + 60 * math.sin(i / 20 + series)
                shape.draw_circle((x, y), 2.0)
                shape.finish(fill=colour, color=None)
        shape.commit()

    assert _kind(draw).kind != "handwritten"


def test_stippled_shading_is_not_handwriting():
    rnd = random.Random(5)

    def draw(page):
        shape = page.new_shape()
        for patch in range(6):  # patches of dots painted row by row, touching neighbours
            x0, y0 = 60 + 170 * (patch % 3), 120 + 250 * (patch // 3)
            for row in range(40):
                for col in range(60):
                    shape.draw_circle(
                        (
                            x0 + col * 1.9 + rnd.uniform(-0.3, 0.3),
                            y0 + row * 1.9 + rnd.uniform(-0.3, 0.3),
                        ),
                        0.8,
                    )
                    shape.finish(fill=(0.3, 0.3, 0.3), color=None)
        shape.commit()

    result = _kind(draw)
    assert result.kind != "handwritten", (result.kind, result.reasons, result.ink)
