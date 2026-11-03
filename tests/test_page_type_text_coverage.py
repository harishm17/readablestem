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
from typing import Any

import pymupdf
import pytest
from PIL import Image, ImageDraw

from image_page_support import block, math, text
from layout_synth import _append, _strokes
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Kind
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


def watermark(page: pymupdf.Page) -> None:
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
    assert page_type.kept_reason(result) == page_type.SCANNED_PAGE  # the image page pass reads it


@pytest.mark.parametrize("mark", ["watermark", "number", "title"])
def test_a_page_of_vector_handwriting_under_a_thin_text_layer_is_handwritten(mark):
    result = classify(page_of(strokes=400, mark=mark))
    assert result.kind == "handwritten"
    assert page_type.kept_reason(result) == page_type.HANDWRITTEN_PAGE


def test_the_reason_is_recorded_when_a_text_layer_over_ink_is_not_the_content():
    result = classify(page_of(strokes=400, mark="watermark"))
    assert page_type.TEXT_NOT_COVERING in result.reasons
    record = result.to_record(0)
    assert page_type.keeps_text(record)  # its visible text is still read when the page is kept


def test_a_genuinely_typed_page_still_takes_the_typed_path():
    result = classify(page_of(typed=30))
    assert result.kind == "born_digital"
    assert page_type.kept_reason(result) is None
    assert page_type.TEXT_NOT_COVERING not in result.reasons


def test_typed_text_with_a_few_annotation_strokes_is_still_typed():
    result = classify(page_of(typed=40, strokes=60))
    assert result.kind in ("mixed_ink", "born_digital")
    assert page_type.kept_reason(result) is None


def test_a_typed_page_with_a_half_page_figure_is_typed():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    typed_lines(page, 30, top=450)
    page.insert_image(pymupdf.Rect(60, 40, 535, 420), stream=notebook_jpeg())
    result = classify(doc.tobytes())
    assert result.kind == "born_digital" and page_type.kept_reason(result) is None


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


def test_a_page_whose_text_layer_is_visible_and_covers_the_picture_is_typed():
    """The text is painted and sits where the content is (16 lines over the photo): the
    layer is the page's text, whatever the picture behind it."""
    result = classify(page_of(picture=True, ocr="visible"))
    assert page_type.TEXT_NOT_COVERING not in result.reasons
    assert page_type.kept_reason(result) in (None, page_type.UNREAD_PAGE)


def test_the_probes_count_the_page_and_do_not_stop_at_it():
    data = page_of(picture=True, mark="watermark")
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        assert page_type.image_page_count(doc) == 1
        assert page_type.document_refusal(doc) is None  # a watermark never refuses the upload
    vector = page_of(strokes=400, mark="watermark")
    with pymupdf.open(stream=vector, filetype="pdf") as doc:
        assert page_type.image_page_count(doc) == 1


# --------------------------------------------------------------------- layout and run


def test_layout_keeps_the_page_and_still_reads_its_text():
    doc = layout.analyze(page_of(picture=True, mark="watermark"))
    assert doc.kept_pages == {0: "scanned_page"}
    assert any("Telegram" in (e.text or "") for e in doc.elements)  # not silently dropped


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


@pytest.mark.parametrize(
    "pdf", [page_of(picture=True, mark="watermark"), page_of(strokes=400, mark="watermark")]
)
def test_the_page_goes_through_the_image_page_pass_and_the_watermark_is_not_repeated(pdf):
    result, fake = convert(pdf)
    assert "image_page" in [r.step for r in fake.requests]
    assert result.image_pages is not None and result.image_pages.converted == 1
    texts = [e.text or "" for e in result.elements if e.kind is not Kind.EQUATION]
    assert not any("Telegram" in t for t in texts)  # read again from the image, not twice
    assert any("hermitian" in t for t in texts)
    assert result.image_only_pages == []


def test_with_the_option_off_the_page_is_kept_with_a_reason_and_its_text():
    result, fake = convert(page_of(picture=True, mark="watermark"), on=False)
    assert "image_page" not in [r.step for r in fake.requests]
    assert result.image_only_pages == [0]
    assert [f.reason for f in result.fixes if f.pending] == ["scanned_page"]
    assert any("Telegram" in (e.text or "") for e in result.elements)


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
