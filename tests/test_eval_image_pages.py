"""The image page numbers of a document-task item and report (`eval.image_pages`) and the
scanned media of the eval (`eval.scan_media`)."""

from __future__ import annotations

from typing import Any

import pytest

from image_page_support import block, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.core.types import Kind
from readablestem.eval import image_pages, scan_media
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import run

PAGE = {
    "blocks": [
        block("paragraph", runs=[text("Speed"), math("v"), text("is small.")], y0=100, y1=160),
        block("equation", latex="v = \\frac{d}{t}", y0=200, y1=260),
        block("paragraph", confidence="low", runs=[text("Mass [illegible].")], y0=300, y1=340),
        block("paragraph", confidence="low", runs=[text("Also"), math("x"), text("there.")]),
    ]
}


def converted(equation: str = "v = \\frac{d}{t}"):
    client = FakeVisionClient(
        responders={
            "image_page": lambda r: PAGE,
            "equation": lambda r: {"is_equation": True, "latex": equation, "confidence": 0.9},
        }
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, feature_image_pages=True, image_pages_recover_omissions=False
    )
    return run.convert_pdf(scan_pdf(), settings=settings, client=client).result


def pair(element_id: str, *, right: bool = True, symbols: bool = True, display: str = "block"):
    return {
        "gold_id": f"g-{element_id}",
        "element_id": element_id,
        "pred_display": display,
        "exact_lenient": right,
        "exact_symbols": symbols,
    }


def ids(result) -> dict[str, str]:
    inline = [e for e in result.elements if e.kind is Kind.EQUATION and e.display == "inline"]
    [display] = [e for e in result.elements if e.kind is Kind.EQUATION and e.display == "block"]
    return {"block": display.id, "inline_ok": inline[0].id, "inline_asked": inline[1].id}


def test_no_record_without_the_pass():
    result = converted()
    result.image_pages = None
    assert image_pages.item_for(result, []) is None


def test_an_item_has_the_pass_record_and_a_row_per_matched_equation():
    result = converted()
    names = ids(result)
    pairs = [
        pair(names["block"], right=False, symbols=False),
        pair(names["inline_ok"], display="inline"),
        pair(names["inline_asked"], right=False, symbols=False, display="inline"),
    ]
    item: Any = image_pages.item_for(result, pairs)
    assert item is not None
    assert (item["pages"], item["converted"], item["fell_back"]) == (1, 1, 0)
    assert item["by_kind"]["paragraph"] == 3 and item["illegible"] == 1
    assert item["cost_usd"] > 0 and item["models"] == [Settings(_env_file=None).main_model]  # type: ignore[call-arg]
    by = {f["gold_id"]: f for f in item["flags"]}
    # A display equation is always asked about; an inline one when its paragraph is.
    assert by[f"g-{names['block']}"]["asked"] is True
    assert by[f"g-{names['inline_ok']}"]["asked"] is False  # paragraph 1: confident, optional
    assert by[f"g-{names['inline_asked']}"]["asked"] is True  # paragraph 3: low confidence
    assert by[f"g-{names['block']}"]["agreed"] is True  # the crop read what the page did


def test_a_disagreement_between_the_readings_is_recorded():
    result = converted(equation="v = d t")
    names = ids(result)
    item: Any = image_pages.item_for(result, [pair(names["block"])])
    assert item["flags"][0]["agreed"] is False


def test_the_pooled_numbers_count_flags_and_the_second_reading():
    result = converted()
    names = ids(result)
    pairs = [
        pair(names["block"], right=False, symbols=False),
        pair(names["inline_ok"], right=False, symbols=False, display="inline"),
        pair(names["inline_asked"], right=False, symbols=False, display="inline"),
    ]
    item = {"image_pages": image_pages.item_for(result, pairs)}
    metrics: Any = image_pages.metrics([item, item], gold_equations=10)
    assert metrics["pages"] == 2 and metrics["converted"] == 2
    assert metrics["cost_per_page_usd"] == pytest.approx(item["image_pages"]["cost_usd"])
    flags = metrics["equation_flags"]
    assert flags["all"]["matched"] == 6 and flags["all"]["wrong"] == 6
    assert flags["all"]["asked"] == 4 and flags["all"]["recall"] == pytest.approx(4 / 6)
    assert flags["all"]["wrong_not_asked"] == 2
    assert flags["all"]["wrong_not_asked_per_100_gold"] == pytest.approx(20.0)
    assert flags["inline"]["asked"] == 2 and flags["display"]["recall"] == 1.0
    assert metrics["second_reading"]["agreed"] == 2 and metrics["second_reading"]["disagreed"] == 0
    assert image_pages.metrics([{"id": "typed"}], 5) is None


# --------------------------------------------------------------------- scanned media


class Inner:
    """What `scan_media.ScannedMedia` needs of the media it wraps."""

    def __init__(self) -> None:
        self.closed = False

    def page_pdf(self, records: list[dict]) -> bytes:
        import pymupdf

        doc = pymupdf.open()
        doc.new_page(width=612, height=792).insert_text(
            (72, 100), "A typed page of text", fontsize=12
        )
        return doc.tobytes()

    def equation_png(self, record: dict) -> bytes:
        return b"png"

    def equation_text(self, record: dict) -> str | None:
        return "text"

    def equation_context(self, record: dict) -> str | None:
        return "context"

    def figure_png(self, url: str) -> bytes:
        return b"fig"

    def close(self) -> None:
        self.closed = True


def test_scanned_media_prints_pages_as_scans_and_delegates_the_rest():
    import pymupdf

    inner = Inner()
    with scan_media.ScannedMedia(inner, seed=3) as media:
        pdf = media.page_pdf([{"book_slug": "b", "page_slug": "p"}])
        assert media.equation_png({}) == b"png" and media.equation_context({}) == "context"
        assert media.figure_png("u") == b"fig" and media.equation_text({}) == "text"
        assert list(media.degradations) == ["b/p"]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert doc[0].get_text().strip() == "" and len(doc[0].get_images()) == 1
    assert inner.closed


def test_an_authored_document_has_no_prose_answer_key():
    media = scan_media.ScannedMedia(Inner())
    assert media.gold_words([{"source": "authored", "book_slug": "x", "page_slug": "y"}]) is None
