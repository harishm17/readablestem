"""Equations kept as pictures are shown at the size of the file's text, cut at three image
pixels per CSS pixel (T3-15)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline import equation_scale as es


def text(eid: str, size: float | None, chars: int = 100, kind=Kind.PARAGRAPH, **kw) -> Element:
    return Element(
        id=eid,
        kind=kind,
        page=0,
        bbox=(0, 0, 10, 10),
        order=int(eid.split("e")[1]),
        text="x" * chars,
        font_size=size,
        **kw,
    )


def equation(eid: str, *, size=None, parent=None, display="block") -> Element:
    return Element(
        id=eid,
        kind=Kind.EQUATION,
        page=0,
        bbox=(10, 10, 110, 40),
        order=int(eid.split("e")[1]),
        font_size=size,
        parent_id=parent,
        display="inline" if parent else display,
    )


def png(width: int, height: int, dpi: int | None) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (255, 255, 255))
    if dpi is not None:
        pixmap.set_dpi(dpi, dpi)
    return pixmap.tobytes("png")


# --------------------------------------------------------------------------- the scale


def test_the_body_size_is_the_one_most_text_is_set_in():
    elements = [
        text("p1-e1", 12.0, 500),
        text("p1-e2", 9.0, 80),
        text("p1-e3", 14.0, 300, Kind.LIST_ITEM),
    ]
    assert es.body_size(elements) == 12.0
    assert es.body_size([text("p1-e1", 12.0, 5, preformatted=True)]) is None  # code is not text
    assert es.body_size([text("p1-e1", None)]) is None
    assert es.body_size([]) is None


@pytest.mark.parametrize(
    ("body", "scale"),
    [(12.0, 1.125), (10.0, 1.35), (13.5, 1.0), (24.0, 0.5625), (6.0, 2.0), (60.0, 0.5)],
)
def test_the_picture_is_scaled_so_its_text_is_the_size_of_the_files_text(body, scale):
    """18 CSS px of file text over the PDF's text at 4/3 CSS px a point, within 0.5 to 2."""
    equations = equation("p1-e9")
    assert es.scaler([text("p1-e1", body), equations])(equations) == pytest.approx(scale)


def test_an_inline_equation_follows_its_paragraph_not_the_document():
    small = text("p1-e1", 8.0, 60)
    body = text("p1-e2", 12.0, 600)
    inline = equation("p1-e3", parent="p1-e1")
    assert es.scaler([small, body, inline])(inline) == pytest.approx(18 / (8 * 4 / 3))


def test_a_display_equation_uses_its_own_size_when_near_the_body_size():
    body = text("p1-e1", 12.0, 600)
    near = equation("p1-e2", size=14.0)
    far = equation("p1-e3", size=40.0)  # a drawn symbol, not the text size
    scale = es.scaler([body, near, far])
    assert scale(near) == pytest.approx(18 / (14 * 4 / 3))
    assert scale(far) == pytest.approx(18 / (12 * 4 / 3))


def test_the_crop_is_cut_at_three_pixels_per_css_pixel():
    assert es.crop_dpi(1.0) == 288  # 72 dpi x 4/3 css px a point x 3
    assert es.crop_dpi(1.125) == 324
    assert es.crop_dpi(0.5) == 144


# --------------------------------------------------------------------------- the picture


def test_the_picture_is_shown_at_its_pixels_over_the_resolution_times_the_scale():
    tagged = png(300, 90, 288)  # 300 px at 288 dpi: 100 pt, 133 css px printed
    assert es.display_size(tagged, 1.0, None) == (100, 30)
    assert es.display_size(tagged, 1.35, None) == (135, 40)
    # Three pixels to each css pixel of the scaled picture, whatever the scale.
    for scale in (0.6, 1.0, 1.5):
        sharp = png(round(100 * es.crop_dpi(scale) / 72), 60, es.crop_dpi(scale))
        shown = es.display_size(sharp, scale, None)
        assert shown is not None and shown[0] * 3 == pytest.approx(sharp_width(sharp), abs=3)


def sharp_width(data: bytes) -> int:
    size = es.png_size(data)
    assert size is not None
    return size[0]


def test_a_crop_cut_the_old_way_is_shown_at_its_printed_size_times_the_scale():
    old = png(278, 83, None)
    assert es.display_size(old, 1.0, (100, 30)) == (100, 30)
    assert es.display_size(old, 1.35, (100, 30)) == (135, 40)
    assert es.display_size(old, 1.0, None) is None  # nothing to go by


# --------------------------------------------------------------------------- in the file


def sizes(html: str) -> list[tuple[int, int]]:
    import re

    return [
        (int(w), int(h)) for w, h in re.findall(r'<img [^>]*width="(\d+)" height="(\d+)"', html)
    ]


# --------------------------------------------------------------------------- the pipeline

FIXTURES = Path(__file__).parent / "fixtures" / "layout" / "latex"


def converted(name: str):
    from readablestem.config import Settings
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.core.types import ConversionOptions
    from readablestem.models.fake import FakeVisionClient
    from readablestem.pipeline.run import convert_pdf

    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    client = FakeVisionClient(budget=budget)
    output = convert_pdf(
        (FIXTURES / f"{name}.pdf").read_bytes(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=client,
        options=ConversionOptions(),
    )
    return output, client
