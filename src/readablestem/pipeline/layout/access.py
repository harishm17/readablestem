"""Reading the PDF: opening it under the one lock PyMuPDF needs, the small cache of opened
documents, the child-process probe that runs before a page is parsed in this process, the
content budget, page selection, and the public readers that cut a region or a line out of a
page (`crop`, `crop_line`, `line_across`, `render_page`, `page_ink`, `release`).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pymupdf

from readablestem.core import content_budget
from readablestem.core.childenv import child_env
from readablestem.core.types import BBox, Element
from readablestem.pipeline import page_type
from readablestem.pipeline.layout.context import LayoutError

DOCUMENT_CACHE_SIZE = 4
DOCUMENT_CACHE_TTL_S = 120.0  # a cached document unused for this long is closed
PROBE_TIMEOUT_S = 60.0  # longest a child process may take to open and render the pages ...

# ... plus this for each page after the first: a page of 86,000 strokes takes about a second
# (30 of them 30 s on a fast core), a page of text a few hundredths. Over the limit the
# error is "too_complex", not "unreadable": the file is fine, it asks for too much.
PROBE_PER_PAGE_S = 2.0

# When the probe of all the pages fails, the search for the failing pages (`probe_failures`)
# starts at most this many more probes and runs at most this many seconds, whichever ends
# first; past either the whole file fails, as it did before the search existed. 40 probes
# find one bad page of a file of any size up to 1,000,000 pages (2 per halving, so 2 *
# ceil(log2 n)), two bad pages in 100, and every bad page of a file of up to 20 pages; a
# page that hangs costs its whole time limit each time it is in a probe, so the clock is
# the bound that matters there.
PROBE_SEARCH_MAX = 40
PROBE_SEARCH_S = 300.0
PROBE_ZOOM = 1.0  # trial render at 72 dpi: the crash is in building the page display list

# ... but never more than this many pixels on the long side (crop's own cap), so a huge
# page box (MuPDF accepts 18000 pt) does not need a gigabyte of pixels: the display list,
# where the crash is, is built whatever the zoom.
PROBE_MAX_SIDE_PX = 2048

# Address space the probe child may use (768 MB, like the upload probe in
# web/pdf_probe.py), plus room for the PDF itself, which it holds twice.
PROBE_MEMORY_BYTES = 768 * 1024 * 1024
CROP_PAD = 2.0  # crop's default padding around an element box (pt)
TEXT_FLAGS = page_type.TEXT_FLAGS  # whitespace kept, clipped to the MediaBox, glyph boxes
_LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$")
_LOCK = threading.RLock()  # PyMuPDF is not thread-safe: one PyMuPDF call at a time


def _zoom(zoom: float, turn: int) -> pymupdf.Matrix:
    """The scale of a crop, and the turn clockwise (0, 90 or 270 degrees) that reads it."""
    matrix = pymupdf.Matrix(zoom, zoom)
    return matrix.prerotate(turn) if turn else matrix


def crop(
    pdf_bytes: bytes,
    element: Element,
    *,
    dpi: int = 200,
    pad: float = CROP_PAD,
    max_side_px: int = 2048,
    tag_dpi: bool = False,
) -> bytes:
    """PNG of the element's region (padded), long side capped at `max_side_px`.

    The region is `element.bbox` grown by `pad` points on every side and clipped to the
    page, rendered at `dpi` on a white background, then scaled down if its long side would
    exceed `max_side_px`. Opened documents are cached (see `release`), so cropping every
    element of one PDF parses it once. A page is only rendered here after it rendered in a
    child process (`analyze` already did that for the pages it analysed). Safe to call from
    several threads, but calls are serialised by the module lock, so there is nothing to
    gain from a thread pool here. `tag_dpi`: write the resolution it was cut at into the PNG
    header (pHYs), so whoever shows it can size it (`pipeline.equation_scale.png_dpi`); the
    default leaves the header as MuPDF writes it, so other crops are byte for byte as before.
    """
    if dpi <= 0 or pad < 0 or max_side_px < 16:
        raise ValueError("crop needs dpi > 0, pad >= 0 and max_side_px >= 16")
    with _LOCK:
        _quiet_mupdf()
        try:
            key, doc = _CACHE.document(pdf_bytes)
            if not 0 <= element.page < doc.page_count:
                raise ValueError(f"element page {element.page} is not in the PDF")
            _ensure_probed(pdf_bytes, key, [element.page])
            page = _CACHE.page(key, element.page)
            x0, y0, x1, y1 = element.bbox
            clip = pymupdf.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & page.rect
            if clip.is_empty or clip.width < 0.5 or clip.height < 0.5:
                raise ValueError("element region is empty or outside the page")
            # MuPDF rounds the clip outwards to whole pixels, so leave one pixel of headroom.
            zoom = min(dpi / 72.0, (max_side_px - 1) / max(clip.width, clip.height))
            # A table set sideways on an upright page is cut turned, so it reads upright.
            turn = element.table.turned if element.table is not None else 0
            pixmap = page.get_pixmap(matrix=_zoom(zoom, turn), clip=clip, alpha=False)
            if max(pixmap.width, pixmap.height) > max_side_px:  # defensive; rounding only
                zoom *= (max_side_px - 1) / max(pixmap.width, pixmap.height)
                pixmap = page.get_pixmap(matrix=_zoom(zoom, turn), clip=clip, alpha=False)
            if tag_dpi:
                pixmap.set_dpi(round(zoom * 72), round(zoom * 72))
            return pixmap.tobytes("png")
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


LINE_BAND = 0.8  # x the span's height: how far above and below the span the line crop reaches
LINE_SIDE = 150.0  # pt either side of the span when it has no paragraph to take the width from
LINE_MARK = (0xD0, 0x10, 0x10)  # the box drawn round the span, in sRGB


def _line_clip(
    page: pymupdf.Page, element: Element, across: tuple[float, float] | None
) -> pymupdf.Rect:
    """The region of the line an inline equation sits in: the extent `crop_line` shows the
    model and `line_glyphs` reads the glyphs of (so the two never disagree about what the
    model could see). From `across` (the paragraph's x-range; LINE_SIDE either side of the
    span without one), and LINE_BAND span heights above and below the span."""
    x0, y0, x1, y1 = element.bbox
    band = LINE_BAND * max(y1 - y0, 4.0)
    left, right = across if across is not None else (x0 - LINE_SIDE, x1 + LINE_SIDE)
    clip = pymupdf.Rect(min(left, x0) - 2, y0 - band, max(right, x1) + 2, y1 + band)
    clip &= page.rect
    if clip.is_empty or clip.width < 0.5 or clip.height < 0.5:
        raise ValueError("line region is empty or outside the page")
    return clip


def line_across(parent: Element | None, element: Element) -> tuple[float, float] | None:
    """The x-range the line of an inline equation is cut from (`crop_line`, `line_glyphs`): its
    paragraph's. A cell equation of a table is cut to its own cell's columns, not the table's
    width: the next column's text ("(5)", a reference mark) is not part of its formula and the
    model read it into it. None without a parent."""
    if parent is None:
        return None
    if parent.table is not None:
        for cell in parent.table.cells:
            if cell.equation_id == element.id and cell.bbox is not None:
                return (cell.bbox[0], cell.bbox[2])
    return (parent.bbox[0], parent.bbox[2])


def crop_line(
    pdf_bytes: bytes,
    element: Element,
    across: tuple[float, float] | None = None,
    *,
    dpi: int = 150,
    max_side_px: int = 2048,
) -> bytes:
    """PNG of the line an inline equation sits in, with the span boxed: the region from
    `across` (the paragraph's x-range; LINE_SIDE either side of the span without one) and
    from LINE_BAND span heights above it to as many below, so a radical, an integral sign or
    a stacked fraction that the text layer puts on a line of its own is in it. The box is two
    pixels wide in LINE_MARK. The model reads the formula the box is part of
    (`equation_step`, `Settings.equation_line_context`): the text layer's span ends where its
    characters do, the formula where the author's maths does."""
    with _LOCK:
        _quiet_mupdf()
        try:
            key, doc = _CACHE.document(pdf_bytes)
            if not 0 <= element.page < doc.page_count:
                raise ValueError(f"element page {element.page} is not in the PDF")
            _ensure_probed(pdf_bytes, key, [element.page])
            page = _CACHE.page(key, element.page)
            x0, y0, x1, y1 = element.bbox
            clip = _line_clip(page, element, across)
            zoom = min(dpi / 72.0, (max_side_px - 1) / max(clip.width, clip.height))
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
            ox, oy = pixmap.x, pixmap.y  # the clip's origin in pixels at this zoom
            bx0 = round((x0 - 1) * zoom) - ox
            by0 = round((y0 - 1) * zoom) - oy
            bx1 = round((x1 + 1) * zoom) - ox
            by1 = round((y1 + 1) * zoom) - oy
            w, h = pixmap.width, pixmap.height
            for rect in (
                (bx0, by0, bx1, by0 + 2),
                (bx0, by1 - 2, bx1, by1),
                (bx0, by0, bx0 + 2, by1),
                (bx1 - 2, by0, bx1, by1),
            ):
                left, top = max(0, rect[0]), max(0, rect[1])
                right, bottom = min(w, rect[2]), min(h, rect[3])
                if right > left and bottom > top:  # set_rect takes the pixmap's own coordinates
                    box = pymupdf.IRect(left + ox, top + oy, right + ox, bottom + oy)
                    pixmap.set_rect(box, LINE_MARK)
            return pixmap.tobytes("png")
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


def render_page(pdf_bytes: bytes, page: int, *, dpi: int = 150, max_side_px: int = 2048) -> bytes:
    """PNG of the whole page at `dpi` on white (long side capped at `max_side_px`), the picture
    the page math pass reads. Same document cache and lock as `crop`."""
    if dpi <= 0 or max_side_px < 16:
        raise ValueError("render_page needs dpi > 0 and max_side_px >= 16")
    with _LOCK:
        _quiet_mupdf()
        try:
            key, doc = _CACHE.document(pdf_bytes)
            if not 0 <= page < doc.page_count:
                raise ValueError(f"page {page} is not in the PDF")
            _ensure_probed(pdf_bytes, key, [page])
            sheet = _CACHE.page(key, page)
            rect = sheet.rect
            zoom = min(dpi / 72.0, (max_side_px - 1) / max(rect.width, rect.height))
            pixmap = sheet.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return pixmap.tobytes("png")
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


def page_ink(pdf_bytes: bytes, page: int, *, budget: Any = None) -> tuple[Any, BBox]:
    """The pen strokes of a page (`ink_strokes.PageInk`) and the page's box in points, read
    within the ink `budget` (`page_type.InkBudget`). Same document cache and lock as
    `crop`."""
    from readablestem.pipeline import ink_strokes

    with _LOCK:
        _quiet_mupdf()
        try:
            key, doc = _CACHE.document(pdf_bytes)
            if not 0 <= page < doc.page_count:
                raise ValueError(f"page {page} is not in the PDF")
            _ensure_probed(pdf_bytes, key, [page])
            sheet = _CACHE.page(key, page)
            rect = sheet.rect
            return ink_strokes.extract_ink(sheet, budget=budget), (
                rect.x0,
                rect.y0,
                rect.x1,
                rect.y1,
            )
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


def release(pdf_bytes: bytes | None = None) -> None:
    """Drop a cached document (or all of them with no argument).

    The cache holds the PDF bytes and the parsed document of recent `crop` calls. Entries
    also expire on their own after `DOCUMENT_CACHE_TTL_S` without use, but the pipeline
    should release a document as soon as its job finishes (in a `finally`), so user
    content does not linger in process memory.
    """
    with _LOCK:
        _CACHE.release(pdf_bytes)


def _quiet_mupdf() -> None:
    """Stop MuPDF printing parser errors and warnings to stdout. Hold `_LOCK`.

    They quote content-stream tokens and resource names from the PDF ("unknown keyword:
    'Jane...'"), and stdout goes to the service logs (privacy rule 1). The switch is
    process-wide, so it is set again on every call in case other code turned it back on.
    """
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)


@dataclass
class _Cached:
    data: bytes
    doc: pymupdf.Document
    used: float  # time.monotonic() of the last use
    turned: set[int] = field(default_factory=set)  # pages `page_type.turn_page` has seen


class _DocumentCache:
    """Opened documents for `crop`, at most `DOCUMENT_CACHE_SIZE`, each closed after
    `DOCUMENT_CACHE_TTL_S` without use. Every method expects the caller to hold `_LOCK`.

    The expiry runs on a daemon timer, so a long-lived worker that goes idle after a job
    does not keep that job's PDF in memory until the next request comes along.
    """

    def __init__(self) -> None:
        self.entries: OrderedDict[str, _Cached] = OrderedDict()
        self.timer: threading.Timer | None = None
        self.due = 0.0  # time.monotonic() at which the pending timer fires

    def __len__(self) -> int:
        return len(self.entries)

    def document(self, pdf_bytes: bytes) -> tuple[str, pymupdf.Document]:
        """(SHA-256 key, opened document) for these bytes, opening them if needed.

        Identity is checked first (the pipeline passes the same bytes object to every
        crop, so no hashing is needed); equal bytes from another object are found by hash.
        """
        now = time.monotonic()
        self.expire(now)
        key = next((k for k, e in self.entries.items() if e.data is pdf_bytes), None)
        if key is None:
            key = _digest(pdf_bytes)
        entry = self.entries.get(key)
        if entry is None:
            entry = _Cached(pdf_bytes, _open(pdf_bytes), now)
            self.entries[key] = entry
            while len(self.entries) > DOCUMENT_CACHE_SIZE:
                self.entries.popitem(last=False)[1].doc.close()
        entry.used = now
        self.entries.move_to_end(key)
        self.schedule()
        return key, entry.doc

    def page(self, key: str, number: int) -> pymupdf.Page:
        """A page of the cached document `key`, set upright if it was saved sideways
        (`page_type.turn_page`: the turn `analyze` read it with)."""
        entry = self.entries[key]
        page = entry.doc[number]
        if number not in entry.turned:
            entry.turned.add(number)
            page_type.turn_page(page)
        return page

    def release(self, pdf_bytes: bytes | None) -> None:
        if pdf_bytes is None:
            keys = list(self.entries)
        else:
            keys = [k for k, e in self.entries.items() if e.data is pdf_bytes]
            keys = keys or [k for k in [_digest(pdf_bytes)] if k in self.entries]
        for key in keys:
            self.entries.pop(key).doc.close()

    def expire(self, now: float) -> None:
        for key in [k for k, e in self.entries.items() if now - e.used >= DOCUMENT_CACHE_TTL_S]:
            self.entries.pop(key).doc.close()

    def schedule(self) -> None:
        """Make sure a timer fires when the least recently used entry expires."""
        if not self.entries:
            return
        due = min(e.used for e in self.entries.values()) + DOCUMENT_CACHE_TTL_S + 0.05
        if self.timer is not None:
            if self.due <= due:
                return  # it fires in time; anything not yet expired is rescheduled then
            self.timer.cancel()
        self.due = due
        self.timer = threading.Timer(max(0.0, due - time.monotonic()), self._on_timer)
        self.timer.daemon = True
        self.timer.start()

    def _on_timer(self) -> None:
        with _LOCK:
            if self.timer is threading.current_thread():  # not a timer cancelled too late
                self.timer = None
            self.expire(time.monotonic())
            self.schedule()


_CACHE = _DocumentCache()


def _digest(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


# Pages that rendered in a child process, by document hash (hashes only, no content).
_PROBED: OrderedDict[str, set[int]] = OrderedDict()
PROBED_MEMORY = 64  # documents remembered

# Runs in a fresh interpreter: open the PDF from stdin, then extract and render each page
# the way analyze and crop will. Python errors are fine (the parent handles them); only a
# crash (MuPDF aborting the process) or a hang matters, so they are swallowed per page.
_PROBE_SCRIPT = """
import sys
try:  # cap this process's address space first (Linux, macOS; best effort elsewhere)
    import resource
    limit = int(sys.argv[5])
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    if soft == resource.RLIM_INFINITY or soft > limit:
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
except (ImportError, ValueError, OSError):
    pass
import pymupdf
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)
doc = pymupdf.open(stream=sys.stdin.buffer.read(), filetype="pdf")
zoom, max_side = float(sys.argv[2]), float(sys.argv[4])
starved = False  # a page that ran out of the memory cap above: reported, not swallowed
for number in map(int, sys.argv[1].split(",")):
    try:
        page = doc[number]
        page.get_text("rawdict", flags=int(sys.argv[3]))
        page.get_cdrawings()
        page.get_image_info()
        side = max(page.rect.width, page.rect.height, 1.0)
        scale = min(zoom, max_side / side)
        page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    except Exception as exc:
        text = str(exc).lower()
        if isinstance(exc, MemoryError | SystemError) or "alloc" in text or "out of memory" in text:
            starved = True
sys.stdout.write("memory" if starved else "ok")
"""


def _ensure_probed(pdf_bytes: bytes, key: str, pages: list[int]) -> None:
    """Probe the pages of this document that have not passed a probe yet. Hold `_LOCK`."""
    done = _PROBED.get(key, set())
    todo = [page for page in pages if page not in done]
    if todo:
        _probe_pages(pdf_bytes, todo)
        _PROBED[key] = done | set(todo)
    if key in _PROBED:
        _PROBED.move_to_end(key)
    while len(_PROBED) > PROBED_MEMORY:
        _PROBED.popitem(last=False)


def probe_failures(pdf_bytes: bytes, key: str, pages: list[int]) -> dict[int, LayoutError]:
    """Probe the pages of this document that have not passed a probe yet, and find the
    pages that fail it: page -> the error of its own probe. Hold `_LOCK`.

    All the pages go to one child first. Only when that fails are they probed again in
    halves, each half that fails split again, until single pages are left: a page that
    cannot be drawn is that page's problem, and the others are read (docs/layout.md "A page
    that cannot be drawn"). Both halves are probed, so a failure that comes and goes (a
    time limit on a busy machine) is not pinned on a page it is not the fault of. Raises the
    first error, as a probe of the whole document always did, when every page fails, when
    the halves pass although the whole did not, or when the search used `PROBE_SEARCH_MAX`
    probes or `PROBE_SEARCH_S` seconds without finishing."""
    done = _PROBED.get(key, set())
    todo = [page for page in pages if page not in done]
    failed: dict[int, LayoutError] = {}
    if todo:
        try:
            _probe_pages(pdf_bytes, todo)
        except LayoutError as first:
            failed = _halve(pdf_bytes, todo, first)
        _PROBED[key] = done | (set(todo) - set(failed))
    if key in _PROBED:
        _PROBED.move_to_end(key)
    while len(_PROBED) > PROBED_MEMORY:
        _PROBED.popitem(last=False)
    return failed


def _halve(pdf_bytes: bytes, pages: list[int], first: LayoutError) -> dict[int, LayoutError]:
    """The failing pages of `pages`, which are known to fail together with the error `first`."""
    failed: dict[int, LayoutError] = {}
    probes = 0
    started = time.monotonic()

    def probe(group: list[int]) -> LayoutError | None:
        nonlocal probes
        if probes >= PROBE_SEARCH_MAX or time.monotonic() - started > PROBE_SEARCH_S:
            raise first  # a search that cannot finish is the failure of the whole file
        probes += 1
        try:
            _probe_pages(pdf_bytes, group)
        except LayoutError as exc:
            return exc
        return None

    def split(group: list[int], error: LayoutError) -> None:
        if len(group) == 1:
            failed[group[0]] = error
            return
        middle = len(group) // 2
        for half in (group[:middle], group[middle:]):
            if (found := probe(half)) is not None:
                split(half, found)

    split(pages, first)
    if not failed or len(failed) == len(pages):  # nothing found, or nothing to read
        raise first
    return failed


def _probe_pages(pdf_bytes: bytes, pages: list[int]) -> None:
    """Extract and render `pages` in a child process; LayoutError("unreadable") if it dies.

    Some damaged PDFs make MuPDF abort the whole process (a glibc double free while
    building a page's display list) although they open and parse without an error. In the
    worker, that would take the web server down with it, and again on every retry of the
    job. A page that rendered in a child renders here too: MuPDF is deterministic. The
    child's output is discarded (its error messages may quote the PDF). The child caps
    its own memory and renders at most `PROBE_MAX_SIDE_PX` on a page's long side, so a
    huge page box costs megabytes, not a gigabyte.
    """
    command = [
        sys.executable,
        "-I",  # isolated: no PYTHON* variables, user site or current directory on the path
        "-c",
        _PROBE_SCRIPT,
        ",".join(map(str, pages)),
        str(PROBE_ZOOM),
        str(TEXT_FLAGS),
        str(PROBE_MAX_SIDE_PX),
        str(PROBE_MEMORY_BYTES + 2 * len(pdf_bytes)),
    ]
    try:
        result = subprocess.run(
            command,
            input=pdf_bytes,
            capture_output=True,
            timeout=PROBE_TIMEOUT_S + PROBE_PER_PAGE_S * max(0, len(pages) - 1),
            check=False,
            env=child_env(),  # no keys or secrets for the process that reads the PDF
        )
    except subprocess.TimeoutExpired:
        raise LayoutError("too_complex", "the PDF took too long to render") from None
    if result.returncode == 0 and result.stdout == b"memory":
        raise LayoutError("too_complex", "the PDF needs more memory than a page may use")
    if result.returncode != 0 or result.stdout != b"ok":
        raise LayoutError("unreadable", "the PDF could not be rendered")


def _open(pdf_bytes: bytes) -> pymupdf.Document:
    if not pdf_bytes:
        raise LayoutError("unreadable", "the file is empty")
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # MuPDF raises several types for damaged or non-PDF input
        raise LayoutError("unreadable", "the file could not be opened as a PDF") from exc
    if not doc.is_pdf:  # MuPDF sniffs content and happily opens a PNG as a 1-page document
        doc.close()
        raise LayoutError("unreadable", "the file is not a PDF")
    if doc.needs_pass:
        doc.close()
        raise LayoutError("encrypted", "the PDF needs a password to open")
    return doc


def _check_content(check: Callable[[], None]) -> None:
    """Run a check of the content budget (`core.content_budget`) and refuse the document
    when it is over: the object streams before the page tree is read, the selected pages
    before any step parses them (each step parses all of their content again). Every PDF
    converted comes here (the worker, `readablestem convert`, the evals), whether or not it
    passed the upload probe."""
    try:
        check()
    except content_budget.ContentTooLarge as exc:
        raise LayoutError("content_too_large", str(exc)) from None


def _select_pages(page_count: int, pages: list[int] | None, max_pages: int | None) -> list[int]:
    if page_count == 0:
        raise LayoutError("no_pages", "the PDF has no pages")
    if pages is None:
        selected = list(range(page_count))
    else:
        selected = sorted(set(pages))
        if not selected:
            raise LayoutError("no_pages", "no pages were requested")
        if selected[0] < 0 or selected[-1] >= page_count:
            raise LayoutError(
                "page_out_of_range", f"page index out of range; the PDF has {page_count} pages"
            )
    if max_pages is not None and len(selected) > max_pages:
        raise LayoutError(
            "too_many_pages", f"{len(selected)} pages requested; the limit is {max_pages}"
        )
    return selected


def _document_lang(doc: pymupdf.Document) -> str:
    try:
        kind, value = doc.xref_get_key(doc.pdf_catalog(), "Lang")
    except Exception:  # malformed catalog: fall back like a missing key
        return "en"
    value = value.strip().lstrip("/")
    return value if kind in ("string", "name") and _LANG.match(value) else "en"
