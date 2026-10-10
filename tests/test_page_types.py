"""Page types (`pipeline.page_type`): hidden text, handwriting, scans, the upload refusal.

The fixtures are synthetic (tests/fixtures/handwriting/make_fixtures.py): seeded ink in
the shapes note apps export, made-up text in the layers iPad notes apps and OCR programs
write, and the design review's failing cases rebuilt. No real handwriting or scan.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.core.types import PageType
from a11y_stem.pipeline import page_type

HANDWRITING = Path(__file__).parent / "fixtures" / "handwriting"
LAYOUT = Path(__file__).parent / "fixtures" / "layout"
DEMO = Path(__file__).parent.parent / "src" / "a11y_stem" / "web" / "static" / "demo" / "source.pdf"


def classify(path: Path, **kwargs) -> list[page_type.PageTypeResult]:
    with pymupdf.open(path) as doc:
        return [
            page_type.classify(
                page,
                page.get_text("rawdict", flags=page_type.TEXT_FLAGS)["blocks"],
                page.get_cdrawings(),
                **kwargs,
            )
            for page in doc
        ]


def refusal(path: Path, **kwargs) -> str | None:
    with pymupdf.open(path) as doc:
        return page_type.document_refusal(doc, **kwargs)


@pytest.mark.parametrize(
    ("name", "kinds", "kept", "refused"),
    [
        ("ios_notes.pdf", ["handwritten_ocr"], ["handwritten_page"], "handwritten"),
        ("ink_no_layer.pdf", ["handwritten"], ["handwritten_page"], "handwritten_no_text"),
        ("scan_ocr.pdf", ["scan_ocr"], ["scanned_page"], "scanned_ocr"),
        ("scan_text_under_image.pdf", ["scan_ocr"], ["scanned_page"], "scanned_ocr"),
        # Hidden-ratio rule: a visible stamp or a typed header does not make a page typed.
        ("scan_stamp_ocr.pdf", ["scan_ocr"], ["scanned_page"], "scanned_ocr"),
        ("ink_header_layer.pdf", ["handwritten_ocr"], ["handwritten_page"], "handwritten"),
        ("typed_with_ink.pdf", ["mixed_ink"], [None], None),
        ("typed_hidden.pdf", ["mixed_hidden_text"], [None], None),
        # See-through things over typed text never hide it (opacity checks).
        ("overlays.pdf", ["born_digital", "born_digital"], [None, None], None),
        # A short title over a big photo is a typed page with a figure, not a scan.
        ("photo_slides.pdf", ["vector_no_text"] * 3, [None] * 3, None),
        ("drawn_page.pdf", ["born_digital", "vector_no_text"], [None, None], None),
        (
            "mixed_course.pdf",
            ["born_digital", "handwritten_ocr", "handwritten", "scan_ocr"],
            [None, "handwritten_page", "handwritten_page", "scanned_page"],
            None,
        ),
        # The routing review's cases (2026-10-09). Boxes drawn with annotation tools hide
        # the line under them; a yellow highlight hides nothing.
        ("annot_redactions.pdf", ["mixed_hidden_text"] * 4 + ["born_digital"], [None] * 5, None),
        ("painted_over.pdf", ["mixed_hidden_text"] * 3, [None] * 3, None),
        # A CropBox that trims a scan's margins leaves a picture of the whole page. With a
        # short typed header and nothing hidden it is kept as an image (its header read,
        # not called a scan) and never refused; so is a title slide on a photo.
        ("cropped_scan.pdf", ["scan_no_text"], ["unread_page"], None),
        ("title_slide.pdf", ["scan_no_text"], ["unread_page"], None),
        # Square waves are drawings, not paper rules.
        ("timing_diagram.pdf", ["vector_no_text"], [None], None),
        # A row of five machine-drawn curves under a typed title is not handwriting.
        ("diagram_slides.pdf", ["vector_no_text"] * 2, [None] * 2, None),
        # A hidden OCR layer over machine-drawn vector content: drawings, not handwriting.
        ("vector_with_layer.pdf", ["vector_no_text"], [None], None),
        ("ink_titled.pdf", ["vector_no_text", "born_digital"], [None, None], None),
        ("big_drawings.pdf", ["born_digital", "born_digital", "vector_no_text"], [None] * 3, None),
        ("template_pages.pdf", ["vector_no_text"] * 2 + ["born_digital"], [None] * 3, None),
    ],
)
def test_each_fixture_gets_its_page_type_and_what_happens_to_it(name, kinds, kept, refused):
    results = classify(HANDWRITING / name)
    assert [r.kind for r in results] == kinds
    assert [page_type.kept_reason(r) for r in results] == kept
    assert refusal(HANDWRITING / name) == refused


def test_invisible_text_is_counted_never_visible():
    (ios,) = classify(HANDWRITING / "ios_notes.pdf")
    assert ios.text.visible_chars == 0 and ios.text.invisible_chars > 100
    assert {"invisible_text", "ocr_font", "ink_writing"} <= set(ios.reasons)
    assert ios.trust == "none" and ios.route == "vision_page"
    (hidden,) = classify(HANDWRITING / "typed_hidden.pdf")
    # Render mode 3 and alpha 0 (two phrases), and a line under a black box.
    assert hidden.text.invisible_chars == 80 and hidden.text.covered_chars == 32
    assert hidden.trust == "visible_only"


def _page_with(draw) -> page_type.PageTypeResult:
    doc = pymupdf.open()
    page = doc.new_page()
    draw(page)
    raw = page.get_text("rawdict", flags=page_type.TEXT_FLAGS)["blocks"]
    result = page_type.classify(page, raw, page.get_cdrawings())
    doc.close()
    return result


def test_a_box_over_one_word_of_a_paragraph_covers_that_word_only():
    def draw(page):
        lines = [
            "Work is done when a force moves an object through a distance.",
            "The secret answer is 42 and nobody should read it in the HTML.",
            "Power is the rate at which work is done, measured in watts.",
        ]
        page.insert_textbox(pymupdf.Rect(72, 72, 520, 200), "\n".join(lines), fontsize=11)
        word = page.search_for("answer is 42")[0]
        box = pymupdf.Rect(word.x0 - 1, word.y0 - 1, word.x1 + 1, word.y1 + 1)
        page.draw_rect(box, color=None, fill=(0, 0, 0))  # painted after the text

    result = _page_with(draw)
    assert result.text.covered_chars == 10  # "answer is 42", nothing else of its paragraph
    assert result.kind == "mixed_hidden_text"


def test_a_page_of_paper_rules_alone_is_blank():
    from fixtures.handwriting.make_fixtures import ruled

    result = _page_with(ruled)
    assert result.kind == "blank" and result.reasons == ["paper_only"]
    assert page_type.refusal_code([result.to_record(0)]) == "no_content"


def test_square_waves_and_short_lines_are_marks_not_paper_rules():
    (diagram,) = classify(HANDWRITING / "timing_diagram.pdf")
    assert diagram.ink.marks > 0 and diagram.kind == "vector_no_text"

    def ticks(page):  # short horizontal and vertical lines, each its own: a drawing
        shape = page.new_shape()
        for k in range(20):
            shape.draw_line((100 + 20 * k, 300), (100 + 20 * k, 310))
        shape.draw_line((100, 305), (480, 305))
        shape.finish(color=(0, 0, 0), width=1)
        shape.commit()

    assert _page_with(ticks).kind == "vector_no_text"


SECRET_CHARS = 63  # the hidden line's characters, spaces left out


def test_boxes_drawn_with_annotation_tools_cover_the_line_under_them():
    pages = classify(HANDWRITING / "annot_redactions.pdf")
    # A black box, a black highlight, a black text box and a white box (annotations):
    # the line under each is covered. A yellow highlight hides nothing.
    assert [p.text.covered_chars for p in pages] == [SECRET_CHARS] * 4 + [0]
    assert all("text_covered" in p.reasons for p in pages[:4])


def test_clip_text_nothing_shows_through_and_text_under_a_shading_or_thick_stroke():
    clip, shading, stroke = classify(HANDWRITING / "painted_over.pdf")
    assert clip.text.invisible_chars == SECRET_CHARS and clip.text.covered_chars == 0
    assert shading.text.covered_chars == stroke.text.covered_chars == SECRET_CHARS
    # The clip-mode title over coloured bands shows: it is still read.
    (title,) = classify(HANDWRITING / "typed_hidden.pdf")
    assert title.text.invisible_chars == 80 and title.text.covered_chars == 32


def test_a_scan_cropped_to_its_picture_covers_the_whole_visible_page():
    (page,) = classify(HANDWRITING / "cropped_scan.pdf")
    assert page.image_coverage == 1.0 and page.text.visible_chars == 19


def test_a_few_strokes_under_a_typed_title_need_more_ink_to_be_handwriting():
    coils, waves = classify(HANDWRITING / "diagram_slides.pdf")
    for page in (coils, waves):
        assert (page.ink.strokes, page.ink.rows) == (5, 1) and 0 < page.text.visible_chars < 40
        assert page.kind == "vector_no_text"
    # The same strokes with no typed text at all are a short handwritten note.
    alone = page_type.decide(page_type.TextStats(), coils.ink, 0.0)
    assert alone.kind == "handwritten" and "ink_few" in alone.reasons


def test_a_hidden_layer_over_machine_drawn_vector_content_is_not_handwriting():
    (page,) = classify(HANDWRITING / "vector_with_layer.pdf")
    assert {"ink_rejected", "invisible_without_image"} <= set(page.reasons)
    assert page.ink.rows < page_type.INK_PAGE_ROWS  # repeated shapes make no rows of writing
    assert page.kind == "vector_no_text" and page.text.invisible_chars > 100


def test_ink_not_analysed_under_a_short_typed_title_is_kept_as_an_image():
    short, long = classify(HANDWRITING / "ink_titled.pdf", budget=page_type.InkBudget(0))
    assert "ink_not_analysed" in short.reasons and short.text.visible_chars == 14
    assert page_type.kept_reason(short) == "unread_page"
    # A line of typed text or more: read as a typed page (layout's safety net decides).
    assert long.kind == "born_digital" and page_type.kept_reason(long) is None


def test_text_painted_under_an_opaque_image_is_covered_also_on_a_turned_page():
    with pymupdf.open(HANDWRITING / "scan_text_under_image.pdf") as doc:
        doc[0].set_rotation(90)
        page = doc[0]
        raw = page.get_text("rawdict", flags=page_type.TEXT_FLAGS)["blocks"]
        result = page_type.classify(page, raw, page.get_cdrawings())
    assert result.text.covered_chars == 413 and result.text.visible_chars == 0
    assert result.kind == "scan_ocr" and "text_covered" in result.reasons


@pytest.mark.parametrize(
    ("span", "hidden"),
    [
        ({"char_flags": 0, "alpha": 255}, True),  # render mode 3: neither filled nor stroked
        ({"char_flags": 16, "alpha": 0}, True),  # filled at opacity 0 (iPad notes)
        ({"char_flags": 16, "alpha": 13}, True),  # 5 %
        ({"char_flags": 16, "alpha": 14}, False),
        ({"char_flags": 32, "alpha": 255}, False),  # stroked only
        ({"char_flags": 80, "alpha": 0}, False),  # text used as a clip (render mode 7)
        ({"char_flags": 64, "alpha": 0}, True),  # a clip that paints nothing itself
        ({}, False),  # older MuPDF without the keys: visible
    ],
)
def test_which_spans_are_hidden(span, hidden):
    assert page_type.span_hidden(span) is hidden


def _record(kind: str, visible: int = 0, invisible: int = 0, reasons=()) -> PageType:
    return PageType(
        page=0, kind=kind, visible_chars=visible, invisible_chars=invisible, reasons=list(reasons)
    )


@pytest.mark.parametrize(
    ("pages", "code"),
    [
        ([_record("handwritten_ocr", 0, 300), _record("blank")], "handwritten"),
        ([_record("handwritten")], "handwritten_no_text"),
        ([_record("scan_no_text")], "scanned"),
        ([_record("scan_ocr", 0, 400), _record("scan_no_text")], "scanned_ocr"),
        ([_record("handwritten"), _record("scan_ocr", 0, 400)], "handwritten"),
        ([_record("scan_no_text"), _record("scan_no_text"), _record("handwritten")], "scanned"),
        ([_record("blank")], "no_content"),
        # Anything convertible, or not sure, never refuses.
        ([_record("handwritten"), _record("born_digital", 500)], None),
        ([_record("scan_no_text", 12)], None),  # a photo with a short title: maybe a slide
        # Drawings under a typed title may be machine-drawn (a few waves, a grid of
        # sparklines): kept as images when converted, never refused. A hidden layer, or
        # no visible text at all, is sure.
        ([_record("handwritten", 16, reasons=["ink_few"])], None),
        ([_record("handwritten", 16, reasons=["ink_some"])], None),
        ([_record("handwritten", 0, reasons=["ink_few"])], "handwritten_no_text"),
        ([_record("handwritten_ocr", 16, 300, reasons=["ink_some"])], "handwritten"),
        ([_record("vector_no_text", reasons=["ink_not_analysed"])], None),
        ([_record("vector_no_text")], None),
        ([], None),
    ],
)
def test_refusal_codes(pages, code):
    assert page_type.refusal_code(pages) == code


def test_ink_over_the_time_budget_is_not_analysed_and_the_page_kept_as_an_image():
    (page,) = classify(HANDWRITING / "ink_no_layer.pdf", budget=page_type.InkBudget(0))
    assert not page.ink.analysed and "ink_not_analysed" in page.reasons
    assert page.kind == "vector_no_text" and page_type.kept_reason(page) == "unread_page"
    # Not sure what it is, so the upload is not refused: the conversion keeps the page.
    assert refusal(HANDWRITING / "ink_no_layer.pdf", budget_s=0) is None
    assert refusal(HANDWRITING / "ink_no_layer.pdf") == "handwritten_no_text"


def test_a_page_with_too_many_path_items_is_not_analysed(monkeypatch):
    monkeypatch.setattr(page_type, "INK_MAX_ITEMS", 100)
    (page,) = classify(HANDWRITING / "ios_notes.pdf")
    assert not page.ink.analysed and page.ink.strokes == 0
    # The hidden layer alone, with no picture of a page, still says handwriting.
    assert page.kind == "handwritten_ocr"


def test_handwriting_regions_on_a_typed_page_stay_clear_of_the_text():
    (page,) = classify(HANDWRITING / "typed_with_ink.pdf")
    regions = page_type.ink_regions(page)
    assert len(regions) == 1
    x0, y0, _, y1 = regions[0]
    assert y0 > 430 and y1 < 560 and x0 > 60  # the two lines written under the text
    assert page_type.ink_regions(classify(HANDWRITING / "ios_notes.pdf")[0]) == []


def test_the_stored_record_is_content_free():
    (page,) = classify(HANDWRITING / "typed_hidden.pdf")
    record = page.to_record(0).model_dump(mode="json")
    text = json.dumps(record)
    for word in ("Momentum", "phrase", "Answer", "velocity", "Displacement", "index"):
        assert word.lower() not in text.lower()
    assert record["kind"] == "mixed_hidden_text" and record["covered_chars"] == 32
    assert record["classifier_version"] == page_type.CLASSIFIER_VERSION


def _born_digital_fixtures() -> list[Path]:
    paths = sorted(LAYOUT.glob("*.pdf")) + sorted((LAYOUT / "latex").glob("*.pdf"))
    return [p for p in paths if "damaged" not in p.name] + [DEMO]


@pytest.mark.parametrize("path", _born_digital_fixtures(), ids=lambda p: p.name)
def test_every_committed_born_digital_fixture_has_no_hidden_text_and_no_kept_page(path):
    for page in classify(path):
        assert page.text.untrusted == 0
        assert page_type.kept_reason(page) is None
        assert page.kind in {"born_digital", "vector_no_text", "blank"}
        assert page.kind != "mixed_ink"
    assert refusal(path) is None
