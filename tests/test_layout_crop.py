"""layout.crop: region rendering, size limits, rotation, the document cache, threads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pymupdf
import pytest

from a11y_stem.core.types import Element, Kind
from a11y_stem.pipeline import layout
from a11y_stem.pipeline.layout import DOCUMENT_CACHE_SIZE, LayoutError, analyze, crop, release

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def empty_cache():
    release()
    yield
    release()


def element(bbox, page: int = 0) -> Element:
    return Element(id="p1-e0", kind=Kind.FIGURE, page=page, bbox=bbox, order=0)


def block_pdf(label: str = "") -> bytes:
    """A black 100 x 50 pt block at (100, 100); `label` makes the bytes unique."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(pymupdf.Rect(100, 100, 200, 150), color=(0, 0, 0), fill=(0, 0, 0))
    page.insert_text((72, 400), f"Some text {label}", fontsize=11)
    return doc.tobytes()


def decode(png: bytes) -> pymupdf.Pixmap:
    assert png.startswith(PNG_SIGNATURE)
    return pymupdf.Pixmap(png)


def dark_share(pixmap: pymupdf.Pixmap) -> float:
    samples, n = pixmap.samples, pixmap.n
    pixels = len(samples) // n
    return sum(samples[i] < 80 for i in range(0, len(samples), n)) / pixels


def test_crop_size_follows_dpi_and_padding():
    pixmap = decode(crop(block_pdf(), element((100, 100, 200, 150)), dpi=144, pad=2.0))
    assert (pixmap.width, pixmap.height) == (208, 108)  # (100 + 4) x (50 + 4) pt at 2x
    assert pixmap.alpha == 0  # white background, no transparency


def test_crop_renders_the_region():
    inside = decode(crop(block_pdf(), element((110, 110, 190, 140)), dpi=72, pad=0))
    outside = decode(crop(block_pdf(), element((300, 100, 400, 150)), dpi=72, pad=0))
    assert dark_share(inside) == 1.0
    assert dark_share(outside) == 0.0


def test_crop_caps_the_long_side_and_keeps_the_aspect_ratio():
    pixmap = decode(crop(block_pdf(), element((0, 0, 612, 792)), dpi=600, pad=0, max_side_px=500))
    assert 495 <= max(pixmap.width, pixmap.height) <= 500
    assert pixmap.width / pixmap.height == pytest.approx(612 / 792, rel=0.01)


def test_crop_is_clipped_to_the_page():
    pixmap = decode(crop(block_pdf(), element((550, 700, 700, 900)), dpi=72, pad=0))
    assert (pixmap.width, pixmap.height) == (62, 92)


def test_crop_follows_page_rotation():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.set_rotation(90)
    page.insert_text((100, 500), "Upright on a landscape page", fontsize=14, rotate=90)
    data = doc.tobytes()
    layout_ = analyze(data)
    assert layout_.page_sizes == [(792.0, 612.0)]
    (paragraph,) = layout_.elements
    assert paragraph.text == "Upright on a landscape page"
    x0, y0, x1, y1 = paragraph.bbox
    assert x1 - x0 > 5 * (y1 - y0)  # horizontal in display space
    pixmap = decode(crop(data, paragraph, dpi=72))
    assert pixmap.width > pixmap.height and dark_share(pixmap) > 0.05


def test_drawings_on_a_rotated_page_are_found_in_display_space():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    # Unrotated drawing (100, 100)-(200, 190); /Rotate 90 shows it at x 602-692, y 100-200.
    page.draw_rect(pymupdf.Rect(100, 100, 200, 150), color=(0, 0, 0), fill=(0, 0, 0))
    page.draw_line((100, 152), (200, 190), color=(0, 0, 0))
    page.set_rotation(90)
    data = doc.tobytes()
    (figure,) = analyze(data).elements
    assert figure.kind == Kind.FIGURE
    assert figure.bbox == pytest.approx((602.0, 100.0, 692.0, 200.0), abs=1.0)
    assert dark_share(decode(crop(data, figure, dpi=72, pad=0))) > 0.4


def test_repeated_crops_open_the_document_once(monkeypatch):
    opened = []
    real_open = layout._open

    def counting_open(data: bytes):
        opened.append(len(data))
        return real_open(data)

    monkeypatch.setattr(layout, "_open", counting_open)
    data = block_pdf()
    for _ in range(5):
        crop(data, element((100, 100, 200, 150)))
    assert len(opened) == 1
    crop(bytes(bytearray(data)), element((100, 100, 200, 150)))  # equal bytes, new object
    assert len(opened) == 1
    analyze(data)  # analysis shares the cache
    assert len(opened) == 1
    release(data)
    crop(data, element((100, 100, 200, 150)))
    assert len(opened) == 2


def test_the_cache_is_bounded_and_can_be_emptied():
    for index in range(DOCUMENT_CACHE_SIZE + 2):
        crop(block_pdf(str(index)), element((100, 100, 200, 150)))
    assert len(layout._CACHE) == DOCUMENT_CACHE_SIZE
    release()
    assert not layout._CACHE


def test_concurrent_crops_are_safe_and_identical_to_serial_ones():
    data = block_pdf()
    boxes = [(100, 100, 200, 150), (72, 380, 200, 405), (0, 0, 300, 300), (90, 90, 210, 160)]
    serial = [crop(data, element(b)) for b in boxes]
    release()

    def work(_: int) -> list[bytes]:
        return [crop(data, element(b)) for b in boxes]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(24)))
    assert all(result == serial for result in results)


@pytest.mark.parametrize(
    "kwargs",
    [{"dpi": 0}, {"pad": -1.0}, {"max_side_px": 8}],
)
def test_crop_rejects_bad_arguments(kwargs):
    with pytest.raises(ValueError):
        crop(block_pdf(), element((100, 100, 200, 150)), **kwargs)


def test_crop_rejects_elements_outside_the_document():
    data = block_pdf()
    with pytest.raises(ValueError, match="page"):
        crop(data, element((100, 100, 200, 150), page=3))
    with pytest.raises(ValueError, match="outside the page"):
        crop(data, element((700, 900, 800, 1000)), pad=0)


def test_crop_reports_unreadable_and_encrypted_pdfs_like_analyze():
    with pytest.raises(LayoutError) as exc:
        crop(b"not a pdf", element((0, 0, 10, 10)))
    assert exc.value.code == "unreadable"
    doc = pymupdf.open()
    doc.new_page()
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(LayoutError) as exc:
        crop(locked, element((0, 0, 10, 10)))
    assert exc.value.code == "encrypted"
