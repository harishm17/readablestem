"""PDF parse stub on a tiny PDF generated in the test (no real course material)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.pipeline.parse import (
    parse_pdf,
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
