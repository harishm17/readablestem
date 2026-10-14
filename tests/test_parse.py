"""PDF parse stub on a tiny PDF generated in the test (no real course material)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.pipeline.parse import (
    equation_score,
    parse_pdf,
    render_region,
    transcribe_equation,
)


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "2.2 Speed and Velocity", fontsize=18)
    page.insert_text(
        (72, 110),
        "Average speed is the distance traveled divided by the elapsed time overall.",
        fontsize=11,
    )
    page.insert_text((72, 150), "v = d / t", fontsize=11)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pixmap.set_rect(pixmap.irect, (200, 30, 30))
    page.insert_image(pymupdf.Rect(72, 200, 272, 300), pixmap=pixmap)
    path = tmp_path / "tiny.pdf"
    doc.save(path)
    doc.close()
    return path


def test_parse_finds_text_image_and_equation_candidate(tiny_pdf):
    elements = parse_pdf(tiny_pdf)
    kinds = [e.kind for e in elements]
    assert kinds.count("image") == 1
    assert kinds.count("equation_candidate") == 1
    assert kinds.count("text") >= 2

    eq = next(e for e in elements if e.kind == "equation_candidate")
    assert eq.text == "v = d / t"
    assert eq.score >= 0.5
    assert "relation symbol" in eq.reasons

    texts = [e.text for e in elements if e.kind == "text"]
    assert any("Speed and Velocity" in t for t in texts)
    assert not any("v = d / t" in t for t in texts)

    heading = next(e for e in elements if e.kind == "text" and "Speed and Velocity" in e.text)
    body = next(e for e in elements if e.kind == "text" and "Average speed" in e.text)
    assert heading.font_size > body.font_size  # input for heading detection later

    image = next(e for e in elements if e.kind == "image")
    assert (image.width_px, image.height_px) == (40, 20)
    x0, _, x1, _ = image.bbox
    assert x0 == pytest.approx(72, abs=1) and x1 == pytest.approx(272, abs=1)


def test_text_bbox_excludes_equation_lines_from_the_same_block(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    # One insert_text call -> one PyMuPDF block with three lines: prose, maths, prose.
    page.insert_text(
        (72, 72),
        "Average speed is the distance traveled divided by the elapsed time.\n"
        "v = d / t\n"
        "This holds for any trip that we take along the road today.",
        fontsize=11,
    )
    path = tmp_path / "mixed.pdf"
    doc.save(path)
    doc.close()

    elements = parse_pdf(path)
    assert [e.kind for e in elements] == ["text", "equation_candidate", "text"]
    first, eq, last = elements
    assert first.text.startswith("Average speed") and last.text.startswith("This holds")

    def overlaps(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    assert not overlaps(first.bbox, eq.bbox)
    assert not overlaps(last.bbox, eq.bbox)
    assert first.bbox[3] <= eq.bbox[1] + 0.01 and eq.bbox[3] <= last.bbox[1] + 0.01


def test_page_filter(tiny_pdf):
    assert parse_pdf(tiny_pdf, pages=[0])
    with pytest.raises(IndexError):
        parse_pdf(tiny_pdf, pages=[5])


def test_render_region_returns_png(tiny_pdf):
    png = render_region(tiny_pdf, 0, (72, 130, 200, 160), dpi=72)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    ("text", "fonts", "is_math"),
    [
        ("E = mc^2", [], True),
        ("x^2 + y^2", ["CMMI10"], True),
        ("F = ma", [], True),
        ("The quick brown fox jumps over the lazy sleeping dog today.", [], False),
        ("We know that x = 5 in this particular example because the earlier result", [], False),
        ("Chapter 3", [], False),
    ],
)
def test_equation_score(text, fonts, is_math):
    score, _ = equation_score(text, fonts, has_superscript=False)
    assert (score >= 0.5) is is_math


def test_vision_step_is_a_clear_todo():
    with pytest.raises(NotImplementedError):
        transcribe_equation(b"")
