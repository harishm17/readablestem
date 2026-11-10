"""A text layer makes a page typed only when it covers the page's content.

Found by an experiment on real photographed notebook pages: three of five carried a
watermark text layer (a diagonal "channel" name, a header, a page number), so the classifier
called them typed, they skipped the image page pass and came out "Not converted" with only
the watermark as text. The fixtures are synthetic: seeded pen scribbles as a picture or as
vector strokes, and made-up text layers.
"""

from __future__ import annotations

import io
import random
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image, ImageDraw

from image_page_support import block, math, text
from layout_synth import _append, _strokes
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import layout, page_type, run

WORDS = "The quick brown fox jumps over the lazy dog while a small cat watches quietly. "
TITLE = "Chapter 3: Linear maps and their matrices"
HEADER = "Telegram: https://example.org/notes channel for lecture notes"


def notebook_jpeg(seed: int = 1) -> bytes:
    """A photographed notebook page: paper tone, sixteen lines of pen scribble."""
    rnd = random.Random(seed)
    image = Image.new("RGB", (1000, 1400), (235, 232, 222))
    draw = ImageDraw.Draw(image)
    for row in range(16):
        y, x = 120 + row * 75, 90
        while x < 900:
            w = rnd.randint(30, 120)
            draw.line(
                [(x + i * w / 8, y + rnd.randint(-22, 22)) for i in range(9)],
                fill=(25, 30, 90),
                width=5,
            )
            x += w + rnd.randint(15, 40)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()


def watermark(page: pymupdf.Page, header: bool = True) -> None:
    """What the real pages carried: a large translucent diagonal name and a header line."""
    at = pymupdf.Point(120, 600)
    page.insert_text(
        at,
        "Notes Watermark",
        fontsize=64,
        fill=(0.6, 0.6, 0.6),
        fill_opacity=0.15,
        morph=(at, pymupdf.Matrix(40)),
    )
    if header:
        page.insert_text((40, 20), HEADER, fontsize=10)


def typed_lines(page: pymupdf.Page, lines: int, top: float = 70) -> None:
    for i in range(lines):
        page.insert_text((60, top + i * 14), WORDS * 2, fontsize=10)


def page_of(
    *,
    picture: bool = False,
    strokes: int = 0,
    mark: str = "",
    ocr: str = "",
    typed: int = 0,
) -> bytes:
    """One A4 page. `picture`: the notebook photo over the page; `strokes`: that many vector
    pen strokes; `mark`: "watermark", "number" or "title" (the text layer over it); `ocr`:
    "hidden" (render mode 3) or "visible", a text layer of the page's own lines; `typed`:
    lines of typed text instead."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    if picture:
        page.insert_image(page.rect, stream=notebook_jpeg())
    if strokes:
        _append(doc, page, _strokes(strokes, 3, points=8))
    if mark == "watermark":
        watermark(page)
    elif mark == "diagonal":
        watermark(page, header=False)
    elif mark == "number":
        page.insert_text((290, 825), "[ 12 ]", fontsize=11)
    elif mark == "title":
        page.insert_text((60, 50), TITLE, fontsize=18)
    if ocr:
        for row in range(16):  # the lines the photo shows, as an OCR program read them
            page.insert_text(
                (50, 80 + row * 45),
                f"ocr guess of the written line number {row} " + "and more words " * 4,
                fontsize=12,
                render_mode=3 if ocr == "hidden" else 0,
            )
    if typed:
        typed_lines(page, typed)
    data = doc.tobytes()
    doc.close()
    return data


def classify(data: bytes) -> page_type.PageTypeResult:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        page = doc[0]
        raw = page.get_text("rawdict", flags=page_type.TEXT_FLAGS)["blocks"]
        return page_type.classify(page, raw, page.get_cdrawings())


# ----------------------------------------------------------------- the classifier


@pytest.mark.parametrize("mark", ["watermark", "number", "title"])
def test_a_photographed_page_under_a_thin_text_layer_is_scanned_not_typed(mark):
    result = classify(page_of(picture=True, mark=mark))
    assert result.kind == "scan_no_text"
    assert page_type.TEXT_NOT_COVERING in result.reasons
    assert page_type.kept_reason(result) == page_type.UNREAD_PAGE  # kept, its text read ...
    assert page_type.reads_image(result)  # ... and the image page pass reads it as a scan


@pytest.mark.parametrize("mark", ["number", "title"])
def test_a_page_of_vector_handwriting_under_a_number_or_title_is_handwritten(mark):
    result = classify(page_of(strokes=400, mark=mark))
    assert result.kind == "handwritten"
    assert page_type.kept_reason(result) == page_type.HANDWRITTEN_PAGE


def test_vector_handwriting_under_a_watermark_is_read_by_the_pass():
    result, fake = convert(page_of(strokes=400, mark="diagonal"))
    assert "image_page" in [r.step for r in fake.requests]
    assert result.image_pages is not None and result.image_pages.converted == 1
    assert not any("Watermark" in (e.text or "") for e in result.elements)


def test_a_cover_page_with_body_text_over_a_picture_stays_typed():
    """Text in the middle of the page, opaque and upright, is body text: a designed cover or
    a slide on a photo is typed, whatever the picture shows (the labelled corpus's cases)."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=notebook_jpeg())
    page.insert_text(
        (60, 400), "Annual review of the science and technology programme", fontsize=18
    )
    result = classify(doc.tobytes())
    assert page_type.TEXT_NOT_COVERING not in result.reasons


def test_typed_text_over_a_blank_picture_stays_typed():
    """A picture the size of the page with nothing on it (a scanned-paper background, a
    template) behind a thin typed page: nothing in the picture is left unread."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    blank = Image.new("RGB", (300, 400), (250, 250, 250))
    out = io.BytesIO()
    blank.save(out, "PNG")
    page.insert_image(page.rect, stream=out.getvalue())
    typed_lines(page, 6)
    result = classify(doc.tobytes())
    assert page_type.TEXT_NOT_COVERING not in result.reasons
    assert page_type.kept_reason(result) in (None, page_type.UNREAD_PAGE)


def test_a_scan_whose_ocr_layer_is_hidden_is_read_from_its_image():
    """A scan with an OCR layer that covers the content: the layer is a program's guess
    (and for maths a poor one) and nothing can check it, so the page is read from its
    image, as before this change. The decision stands; the test fixes it."""
    result = classify(page_of(picture=True, ocr="hidden"))
    assert result.kind == "scan_ocr"
    assert page_type.kept_reason(result) == page_type.SCANNED_PAGE


# --------------------------------------------------------------------- layout and run


def test_a_title_slide_on_a_repeated_background_is_still_typed():
    """The same picture behind a title on every page is the template's background."""
    pdf = pymupdf.open()
    picture = notebook_jpeg()
    for number in range(3):
        page = pdf.new_page(width=720, height=405)
        page.insert_image(page.rect, stream=picture, keep_proportion=False)
        page.insert_text(
            (40, 60), f"Slide {number} of the lecture on matrices and their uses", fontsize=14
        )
    doc = layout.analyze(pdf.tobytes())
    assert not any(page_type.TEXT_NOT_COVERING in doc.page_types[n].reasons for n in range(3))
    assert "scanned_page" not in doc.kept_pages.values()
    assert any("Slide 1" in (e.text or "") for e in doc.elements)


PAGE: dict[str, Any] = {
    "blocks": [
        block("heading", level=1, runs=[text("Hermitian matrices")], y0=40, y1=80),
        block(
            "paragraph", runs=[text("A matrix "), math("A"), text(" is hermitian.")], y0=100, y1=160
        ),
    ]
}


def convert(pdf: bytes, *, on: bool = True):
    settings = Settings(_env_file=None, feature_image_pages=on)  # type: ignore[call-arg]
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = FakeVisionClient(
        responders={"image_page": lambda r: PAGE, "equation": lambda r: None}, budget=budget
    )
    return run.convert_pdf(pdf, settings=settings, client=fake).result, fake


def test_the_review_screen_says_why_a_page_that_was_tried_stayed_an_image():
    from readablestem.web.routes.review import reading_note

    settings = Settings(_env_file=None, feature_image_pages=True)  # type: ignore[call-arg]
    fake = FakeVisionClient(
        responders={"image_page": lambda r: None},
        fail_models={settings.main_model: "refused"},
    )
    result = run.convert_pdf(
        page_of(picture=True, mark="watermark"), settings=settings, client=fake
    ).result
    assert result.image_only_pages == [0]
    assert reading_note(result, [1]) == (
        "The tool tried to read page 1 from an image, but the model did not give an answer."
    )
    off = convert(page_of(picture=True, mark="watermark"), on=False)[0]
    assert reading_note(off, [1]) == ""


# ------------------------------------------------------------ the labelled corpus

CORPUS = Path("/home/harish/code/a11y-research/handwriting/detect/corpus/labels.json")


@pytest.mark.skipif(not CORPUS.exists(), reason="the labelled page-type corpus is not here")
def test_no_page_of_the_labelled_corpus_gets_worse_against_its_label():
    """The calibration set for these rules (846 pages on the author's machine, not in the
    repository; `scripts/page_type_corpus_compare.py`): switching the rules on changes no
    page's kind against its label for the worse."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "page_type_corpus_compare",
        Path(__file__).parent.parent / "scripts" / "page_type_corpus_compare.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import sys

    argv = sys.argv
    sys.argv = ["compare", "--labels", str(CORPUS)]
    try:
        assert module.main() == 0
    finally:
        sys.argv = argv
