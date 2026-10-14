"""Equations kept as pictures are shown at the size of the file's text, cut at three image
pixels per CSS pixel (T3-15)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Fix, Kind, Status
from readablestem.pipeline import equation_scale as es
from readablestem.pipeline.assemble import render_html


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


def test_without_text_to_measure_by_the_scale_is_one():
    only = equation("p1-e1", size=11.0)
    assert es.scaler([only])(only) == 1.0


def test_the_crop_is_cut_at_three_pixels_per_css_pixel():
    assert es.crop_dpi(1.0) == 288  # 72 dpi x 4/3 css px a point x 3
    assert es.crop_dpi(1.125) == 324
    assert es.crop_dpi(0.5) == 144


# --------------------------------------------------------------------------- the picture


def test_a_crops_pixels_and_resolution_are_read_from_its_header():
    tagged = png(300, 90, 288)
    assert es.png_size(tagged) == (300, 90)
    assert es.png_dpi(tagged) == pytest.approx(288, abs=0.1)
    assert es.png_dpi(png(300, 90, None)) is None  # the default 96 says nothing
    assert es.png_size(b"not a png") is None and es.png_dpi(b"not a png") is None
    assert es.png_dpi(b"\x89PNG\r\n\x1a\n") is None


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


def test_an_absurd_size_is_not_written():
    assert es.display_size(png(40, 40, 288), 1.0, None) == (13, 13)
    assert es.display_size(png(10, 10, None), 1.0, (9000, 10)) is None


# --------------------------------------------------------------------------- in the file


def flagged_file(crop: bytes, *, body: float) -> str:
    elements = [
        text("p1-e1", body, 400),
        equation("p1-e2"),
        text("p1-e3", body, 50),
        equation("p1-e4", parent="p1-e3"),
    ]
    elements[2].text = "see ⟦p1-e4⟧ here"
    fixes = [
        Fix(element_id="p1-e2", kind=Kind.EQUATION, status=Status.ABSTAINED, reason="abstained"),
        Fix(element_id="p1-e4", kind=Kind.EQUATION, status=Status.FAILED, reason="api_error"),
    ]
    return render_html(
        title="T",
        lang="en",
        elements=elements,
        fixes=fixes,
        crops={"p1-e2": crop, "p1-e4": crop},
    )


def sizes(html: str) -> list[tuple[int, int]]:
    import re

    return [
        (int(w), int(h)) for w, h in re.findall(r'<img [^>]*width="(\d+)" height="(\d+)"', html)
    ]


def test_unconverted_equations_in_the_file_follow_the_text_size():
    crop = png(300, 90, 288)  # 100 x 30 css px at its printed size
    twelve = sizes(flagged_file(crop, body=12.0))
    assert twelve == [(112, 34), (112, 34)]  # x 1.125
    twenty_four = sizes(flagged_file(crop, body=24.0))
    assert twenty_four == [(56, 17), (56, 17)]  # x 0.5625


def test_an_old_crop_is_scaled_too():
    old = png(278, 83, None)
    shown = sizes(flagged_file(old, body=10.0))
    assert shown == [(180, 54), (180, 54)]  # the box, 100 x 30 pt = 133 x 40 css px, x 1.35


def test_the_picture_keeps_its_shape():
    crop = png(300, 90, 288)
    ((width, height), *_) = sizes(flagged_file(crop, body=12.0))
    assert width / height == pytest.approx(300 / 90, rel=0.05)


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


def test_equation_crops_are_cut_for_the_text_size_and_the_model_sees_the_usual_one():
    from readablestem.pipeline import layout

    output, client = converted("problem_set")
    result = output.result
    scale = es.scaler(result.elements)
    equations = [e for e in result.elements if e.kind is Kind.EQUATION]
    assert equations
    for element in equations:
        crop = output.crops[element.id]
        assert es.png_dpi(crop) == pytest.approx(es.crop_dpi(scale(element)), abs=1)
    sent = {request.images[0] for request in client.requests if request.step == "equation"}
    assert sent  # the model was asked about equations
    pdf = (FIXTURES / "problem_set.pdf").read_bytes()
    usual = {layout.crop(pdf, e) for e in equations}
    layout.release(pdf)
    assert sent <= usual  # the 200 dpi crop, as before: nothing the model sees changed
    assert all(es.png_dpi(png) is None for png in sent)


def test_figure_crops_are_as_they_were():
    from readablestem.pipeline import layout

    output, _ = converted("kinematics_slides")
    figures = [e for e in output.result.elements if e.kind is Kind.FIGURE]
    assert figures
    pdf = (FIXTURES / "kinematics_slides.pdf").read_bytes()
    for element in figures:
        assert output.crops[element.id] == layout.crop(pdf, element)
    layout.release(pdf)
