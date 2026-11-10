"""layout.crop: region rendering, size limits, rotation, the document cache, threads, and
the child-process probe that keeps a crashing PDF from taking the process down."""

from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout
from readablestem.pipeline.layout import LayoutError, analyze, crop, release

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


def test_a_pdf_that_hangs_the_renderer_is_too_complex_not_a_hang(monkeypatch, unprobed):
    monkeypatch.setattr(layout, "_PROBE_SCRIPT", "import time; time.sleep(60)")
    monkeypatch.setattr(layout, "PROBE_TIMEOUT_S", 0.5)
    started = time.monotonic()
    with pytest.raises(LayoutError) as exc:
        analyze(block_pdf())
    assert exc.value.code == "too_complex" and time.monotonic() - started < 10


def test_a_page_that_runs_out_of_the_childs_memory_is_too_complex(monkeypatch, unprobed):
    monkeypatch.setattr(
        layout, "_PROBE_SCRIPT", "import sys; sys.stdout.write('memory')"
    )  # what the real script writes after a page raised MemoryError
    with pytest.raises(LayoutError) as exc:
        analyze(block_pdf())
    assert exc.value.code == "too_complex"
