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
