"""Inline equations read with their line (`Settings.equation_line_context`): the line crop
boxes the span and reaches past it, and the equation step sends both pictures under the
line prompt; without the line nothing changes."""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.models.fake import FakeVisionClient
from readablestem.models.prompts import load_prompt
from readablestem.pipeline import layout
from readablestem.pipeline.equation_step import PROMPT_VERSION, transcribe
from test_steps_support import equation_element, make_settings, use_fallback_router

PNG = b"\x89PNG equation crop"
LINE = b"\x89PNG line crop"


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def line_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 400), "where T = 2 pi sqrt(m/k) is the period", fontsize=11)
    return doc.tobytes()


def red(pixmap: pymupdf.Pixmap) -> int:
    s, n = pixmap.samples, pixmap.n
    return sum(1 for i in range(0, len(s), n) if s[i] > 180 and s[i + 1] < 60 and s[i + 2] < 60)


def test_line_crop_boxes_the_span_and_reaches_past_it():
    layout.release()
    pdf = line_pdf()
    span = Element(
        id="p1-e1",
        kind=Kind.EQUATION,
        page=0,
        bbox=(110, 390, 150, 402),
        order=0,
        display="inline",
    )
    line = pymupdf.Pixmap(layout.crop_line(pdf, span, (72, 540)))
    alone = pymupdf.Pixmap(layout.crop(pdf, span))
    assert red(line) > 0  # the box is drawn
    assert line.width > 4 * alone.width  # the whole line, not only the span
    assert line.height > alone.height  # and some height above and below it
    assert red(pymupdf.Pixmap(layout.crop(pdf, span))) == 0  # the span's own crop is unmarked
    layout.release()


def answer(req):
    return {"is_equation": True, "latex": r"T = 2\pi\sqrt{m/k}", "confidence": 0.95}


def test_the_line_goes_with_the_crop_under_the_line_prompt():
    client = FakeVisionClient(responders={"equation": answer})
    transcribe(equation_element(), PNG, client=client, settings=make_settings(), line_png=LINE)
    request = client.requests[0]
    assert request.images == [PNG, LINE]
    line = load_prompt("equation_line")
    assert request.prompt_version == f"{PROMPT_VERSION}+{line.version}"
    assert request.instructions.endswith(line.text)


def test_without_the_line_the_request_is_as_before():
    client = FakeVisionClient(responders={"equation": answer})
    transcribe(equation_element(), PNG, client=client, settings=make_settings())
    request = client.requests[0]
    assert request.images == [PNG]
    assert request.prompt_version == PROMPT_VERSION
