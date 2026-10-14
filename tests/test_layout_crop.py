"""layout.crop: region rendering, size limits, rotation, the document cache, threads, and
the child-process probe that keeps a crashing PDF from taking the process down."""

from __future__ import annotations

import subprocess
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout
from readablestem.pipeline.layout import DOCUMENT_CACHE_SIZE, LayoutError, analyze, crop, release

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
    analyze(data)  # analysis opens and closes its own copy; it does not fill the cache
    assert len(opened) == 2 and len(layout._CACHE) == 1
    crop(data, element((100, 100, 200, 150)))
    assert len(opened) == 2
    release(data)
    crop(data, element((100, 100, 200, 150)))
    assert len(opened) == 3


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


def test_analysis_leaves_no_document_in_the_cache():
    analyze(block_pdf())
    assert len(layout._CACHE) == 0


def test_cached_documents_expire_after_a_while_without_use(monkeypatch):
    monkeypatch.setattr(layout, "DOCUMENT_CACHE_TTL_S", 0.2)
    crop(block_pdf(), element((100, 100, 200, 150)))
    assert len(layout._CACHE) == 1
    deadline = time.monotonic() + 10
    while len(layout._CACHE) and time.monotonic() < deadline:  # a daemon timer expires it
        time.sleep(0.05)
    assert len(layout._CACHE) == 0


# ---------------------------------------------------------------- crash isolation

FIXTURES = Path(__file__).parent / "fixtures" / "layout"


@pytest.fixture
def unprobed(monkeypatch):
    """Forget which documents passed the probe, so every test probes afresh."""
    monkeypatch.setattr(layout, "_PROBED", OrderedDict())


def test_a_pdf_that_kills_the_renderer_is_unreadable_not_fatal(monkeypatch, unprobed):
    # MuPDF dies of SIGABRT; SIGKILL is the same for the parent and skips the core dump.
    monkeypatch.setattr(
        layout, "_PROBE_SCRIPT", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    )
    data = block_pdf()
    with pytest.raises(LayoutError) as exc:
        analyze(data)
    assert exc.value.code == "unreadable"
    with pytest.raises(LayoutError) as exc:
        crop(data, element((100, 100, 200, 150)))
    assert exc.value.code == "unreadable"


def test_a_pdf_that_hangs_the_renderer_is_unreadable(monkeypatch, unprobed):
    monkeypatch.setattr(layout, "_PROBE_SCRIPT", "import time; time.sleep(60)")
    monkeypatch.setattr(layout, "PROBE_TIMEOUT_S", 0.5)
    started = time.monotonic()
    with pytest.raises(LayoutError) as exc:
        analyze(block_pdf())
    assert exc.value.code == "unreadable" and time.monotonic() - started < 10


def test_a_damaged_pdf_that_aborts_mupdf_does_not_kill_this_process(unprobed):
    """tests/fixtures/layout/damaged_render_abort.pdf is two_column.pdf with 19 bytes
    flipped. It parses, but rendering it makes MuPDF (PyMuPDF 1.28.2) abort the process
    with a glibc double free. Before the probe, this test killed pytest."""
    data = (FIXTURES / "damaged_render_abort.pdf").read_bytes()
    try:
        layout_ = analyze(data)
    except LayoutError as exc:
        assert exc.code == "unreadable"
    else:  # a MuPDF release that fixes the bug: then every crop must work too
        for item in layout_.elements:
            crop(data, item)


def test_pages_are_probed_once_and_crop_probes_pages_analysis_skipped(monkeypatch, unprobed):
    probed: list[list[int]] = []
    real = layout._probe_pages

    def recording(pdf_bytes: bytes, pages: list[int]) -> None:
        probed.append(list(pages))
        real(pdf_bytes, pages)

    monkeypatch.setattr(layout, "_probe_pages", recording)
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page().insert_text((72, 100), "Some text", fontsize=11)
    data = doc.tobytes()
    analyze(data, pages=[0, 2])
    analyze(data, pages=[0])
    crop(data, element((50, 80, 200, 110), page=0))
    crop(data, element((50, 80, 200, 110), page=1))
    crop(data, element((50, 80, 200, 110), page=1))
    assert probed == [[0, 2], [1]]


def test_the_probe_output_is_discarded(monkeypatch, unprobed, capfd):
    monkeypatch.setattr(
        layout, "_PROBE_SCRIPT", "import sys; print('SecretStudentName'); sys.exit(3)"
    )
    with pytest.raises(LayoutError):
        analyze(block_pdf())
    out, err = capfd.readouterr()
    assert "Secret" not in out + err


# Runs in a fresh interpreter, so RUSAGE_CHILDREN sees only the probe child it starts.
PROBE_PEAK = """
import resource, sys
import pymupdf
from readablestem.pipeline import layout
side, max_side, memory = float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
doc = pymupdf.open()
page = doc.new_page(width=200, height=200)
page.insert_text((20, 40), "Big page")
page.set_mediabox(pymupdf.Rect(0, 0, side, side))
layout.PROBE_MAX_SIDE_PX = max_side
layout.PROBE_MEMORY_BYTES = memory
layout._probe_pages(doc.tobytes(), [0])  # LayoutError if the child died
print(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
"""
MIB = 1024 * 1024


def probe_peak_mib(
    side: float,
    max_side: float = layout.PROBE_MAX_SIDE_PX,
    memory: int = layout.PROBE_MEMORY_BYTES,
) -> float:
    """Peak resident memory of the probe child for one page `side` points square."""
    done = subprocess.run(
        [sys.executable, "-c", PROBE_PEAK, str(side), str(max_side), str(memory)],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return int(done.stdout.strip()) / 1024  # ru_maxrss is in KiB on Linux


linux_only = pytest.mark.skipif(sys.platform != "linux", reason="ru_maxrss units, RLIMIT_AS")


@linux_only
def test_a_huge_page_box_is_probed_with_a_bounded_render():
    """MuPDF accepts page boxes far past the PDF maximum. The probe rendered whole pages
    at 72 dpi: an 18000 pt page was an 18000 x 18000 pixmap, about 1 GB. The upload probe
    refuses such pages, but the CLI and eval convert files that never went through it."""
    assert probe_peak_mib(18000) < 200


@linux_only
def test_the_probe_render_is_capped_in_pixels():
    capped = probe_peak_mib(9000)
    uncapped = probe_peak_mib(9000, max_side=10**6)  # the old behaviour: 9000 px square
    assert capped < 200 and uncapped > capped + 150, (capped, uncapped)


@linux_only
def test_the_probe_child_caps_its_own_address_space():
    """Without the pixel cap an 18000 pt page needs ~1 GB of pixels; under a 400 MB
    limit the allocation fails inside the child (a Python error there is ignored) and
    the child never grows past it."""
    assert probe_peak_mib(18000, max_side=10**6, memory=400 * MIB) < 450
