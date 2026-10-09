"""Page images for the review screen, rendered on demand from the stored upload.

The review screen shows each page of the PDF with the fixes marked on it, and a wider
crop around an equation for the side-by-side compare. Both are rendered here when the
browser asks, from the job's own upload, and never stored: the route answers with
`Cache-Control: private, no-store` (like the crops), and nothing is cached in this
process either, so a deleted or expired job has no page images anywhere.

MuPDF runs in the upload probe's child process (`web.pdf_probe`, with its 768 MB address
space cap), never in the web process: a page that crashes or hangs MuPDF kills a child,
and the request gets a `RenderFailed` instead. At most `MAX_CONCURRENT_RENDERS` children
run at once per instance, so a burst of image requests cannot hold several copies of a
20 MB PDF and its pixmaps in memory together; a request that waits longer than
`SLOT_WAIT_S` for a slot gets `RenderBusy` (the route answers 503).

The functions here are the seam the web tests replace with fakes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Iterable

from a11y_stem.web.pdf_probe import PNG_MAGIC

BBox = tuple[float, float, float, float]

PAGE_ZOOM = 2.0  # 144 dpi: sharp on a 2x screen at the width the review screen shows
PAGE_MAX_SIDE_PX = 2000
REGION_MAX_SIDE_PX = 1600
RENDER_TIMEOUT_S = 20.0
SIZES_TIMEOUT_S = 15.0
MAX_CONCURRENT_RENDERS = 2
SLOT_WAIT_S = 10.0

_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_RENDERS)


class RenderFailed(Exception):
    """No image: the child crashed, timed out, or refused the request. `code` is a
    category ("render_failed", "busy"), safe to log."""

    def __init__(self, code: str = "render_failed") -> None:
        super().__init__(code)
        self.code = code


class RenderBusy(RenderFailed):
    def __init__(self) -> None:
        super().__init__("busy")


def _child(args: list[str], data: bytes, timeout_s: float) -> bytes:
    """Run `python -m a11y_stem.web.pdf_probe ARGS` with the PDF on stdin; its stdout, or
    RenderFailed. MuPDF's own messages (stderr) are discarded, never logged: they can
    quote the PDF."""
    if not _SLOTS.acquire(timeout=SLOT_WAIT_S):
        raise RenderBusy()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "a11y_stem.web.pdf_probe", *args],
            input=data,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        raise RenderFailed() from None
    finally:
        _SLOTS.release()
    if completed.returncode != 0:
        raise RenderFailed()
    return completed.stdout


def _numbers(box: BBox) -> str:
    return ",".join(f"{v:.2f}" for v in box)


def render_region(
    pdf: bytes,
    page: int,
    *,
    zoom: float = PAGE_ZOOM,
    max_side: int = PAGE_MAX_SIDE_PX,
    clip: BBox | None = None,
    mark: BBox | None = None,
    chars: Iterable[str] = (),
    box: BBox | None = None,
) -> bytes:
    """PNG of page `page` (0-based), or of `clip` on it; glyphs of `chars` inside `mark`
    are outlined, and so is `box` as a whole. Raises RenderFailed."""
    args = ["render", str(page), f"{zoom:.4f}", str(max_side)]
    if clip is not None:
        args += ["--clip", _numbers(clip)]
    hexes = ",".join(f"{ord(c):x}" for c in sorted(set(chars)) if len(c) == 1)
    if mark is not None and hexes:
        args += ["--mark", _numbers(mark), "--chars", hexes]
    if box is not None:
        args += ["--box", _numbers(box)]
    png = _child(args, pdf, RENDER_TIMEOUT_S)
    if not png.startswith(PNG_MAGIC):
        raise RenderFailed()
    return png


def render_page(pdf: bytes, page: int) -> bytes:
    """The whole page at `PAGE_ZOOM`."""
    return render_region(pdf, page)


def page_sizes(pdf: bytes) -> dict[int, tuple[float, float]]:
    """0-based page -> (width, height) in points, for results stored before they recorded
    page sizes. Raises RenderFailed."""
    out = _child(["sizes"], pdf, SIZES_TIMEOUT_S)
    try:
        answer = json.loads(out.decode("utf-8").strip().splitlines()[-1])
        sizes = answer["sizes"]
        return {i: (float(w), float(h)) for i, (w, h) in enumerate(sizes)}
    except (ValueError, KeyError, IndexError, TypeError):
        raise RenderFailed() from None
