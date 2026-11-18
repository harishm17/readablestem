"""Page images for the review screen, rendered on demand from the stored upload.

The review screen shows each page of the PDF with the fixes marked on it, and a wider
crop around an equation for the side-by-side compare. Both are rendered here when the
browser asks, from the job's own upload, and never stored on disk: the route answers with
`Cache-Control: private, no-store` (like the crops). The web process keeps the pictures
it just made in a small memory cache (`RenderCache`), so a page asked for again, or the
review screen opened a second time, does not start MuPDF again (F-284). The cache holds at
most `Settings.page_image_cache_mb` of picture bytes, lives in this process only, and a
job's pictures leave it when the job is deleted or expires (`forget_job`, called by
`ops.retention.delete_jobs`). It sits behind the routes' access check: a hit is only ever
answered to a request that has already been allowed to see the job.

MuPDF runs in the upload probe's child process (`web.pdf_probe`, with its 768 MB address
space cap), never in the web process: a page that crashes or hangs MuPDF kills a child,
and the request gets a `RenderFailed` instead.

A render needs a slot (`render_slot`), taken *before* the route loads the upload, so a
burst of image requests cannot park threads each holding a copy of a large PDF:

- each user has at most `MAX_RENDERS_PER_USER` renders in progress; one more is refused
  at once (`RenderLimited`, the route answers 429). A review page asks for at most four
  images at a time (the page, and the item's region or the three copies a group of
  repeated headings shows: T1-18), so a person never meets this; a script firing a
  hundred requests does;
- at most `MAX_CONCURRENT_RENDERS` children run at once per instance; a request waits
  at most `SLOT_WAIT_S` for one (`RenderBusy`, 503). With the per-user cap, one user can
  keep at most four threads waiting.

The functions here are the seam the web tests replace with fakes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Generator, Iterable
from contextlib import AbstractContextManager, contextmanager

from readablestem.core.childenv import child_env
from readablestem.web.pdf_probe import PNG_MAGIC

BBox = tuple[float, float, float, float]

PAGE_ZOOM = 2.0  # 144 dpi: sharp on a 2x screen at the width the review screen shows
PAGE_MAX_SIDE_PX = 2000
REGION_MAX_SIDE_PX = 1600
RENDER_TIMEOUT_S = 20.0
SIZES_TIMEOUT_S = 15.0
MAX_CONCURRENT_RENDERS = 2
MAX_RENDERS_PER_USER = 4
SLOT_WAIT_S = 3.0


class RenderFailed(Exception):
    """No image: the child crashed, timed out, or refused the request. `code` is a
    category ("render_failed", "busy", "user_busy"), safe to log."""

    def __init__(self, code: str = "render_failed") -> None:
        super().__init__(code)
        self.code = code


class RenderBusy(RenderFailed):
    """Every render slot of this instance stayed taken for `SLOT_WAIT_S`."""

    def __init__(self) -> None:
        super().__init__("busy")


class RenderLimited(RenderFailed):
    """This user already has `MAX_RENDERS_PER_USER` renders in progress."""

    def __init__(self) -> None:
        super().__init__("user_busy")


class RenderGate:
    """Render slots: `per_user` at a time for each user (refused at once beyond that),
    `total` at a time for the instance (waited for, `wait_s` at most). Per process."""

    def __init__(self, total: int, per_user: int, wait_s: float) -> None:
        self.per_user = per_user
        self.wait_s = wait_s
        self._slots = threading.BoundedSemaphore(total)
        self._users: dict[str, int] = {}
        self._lock = threading.Lock()

    @contextmanager
    def slot(self, user_id: str) -> Generator[None]:
        with self._lock:
            if self._users.get(user_id, 0) >= self.per_user:
                raise RenderLimited()
            self._users[user_id] = self._users.get(user_id, 0) + 1
        try:
            if not self._slots.acquire(timeout=self.wait_s):
                raise RenderBusy()
            try:
                yield
            finally:
                self._slots.release()
        finally:
            with self._lock:
                left = self._users.get(user_id, 1) - 1
                if left:
                    self._users[user_id] = left
                else:
                    self._users.pop(user_id, None)


class RenderCache:
    """Pictures already made, by (job, upload, what was drawn): a least-recently-used cache
    bounded by the bytes of the pictures it holds (and by `MAX_ENTRIES`, so that many tiny
    ones cannot fill it). A picture bigger than a quarter of the bound is not kept. A bound
    of 0 keeps nothing. Per process, thread safe."""

    MAX_ENTRIES = 512

    def __init__(self, limit_bytes: int = 0) -> None:
        self._limit = limit_bytes
        self._items: OrderedDict[tuple[str, str, tuple[str, ...]], bytes] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def configure(self, limit_bytes: int) -> None:
        """Set the bound (megabytes in `Settings.page_image_cache_mb`) and empty the cache."""
        with self._lock:
            self._limit = max(0, limit_bytes)
            self._items.clear()
            self._bytes = 0

    def get(self, job_id: str, upload_id: str, what: tuple[str, ...]) -> bytes | None:
        with self._lock:
            png = self._items.get((job_id, upload_id, what))
            if png is not None:
                self._items.move_to_end((job_id, upload_id, what))
            return png

    def put(self, job_id: str, upload_id: str, what: tuple[str, ...], png: bytes) -> None:
        if len(png) * 4 > self._limit:
            return
        key = (job_id, upload_id, what)
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= len(old)
            self._items[key] = png
            self._bytes += len(png)
            while self._items and (
                self._bytes > self._limit or len(self._items) > self.MAX_ENTRIES
            ):
                _, gone = self._items.popitem(last=False)
                self._bytes -= len(gone)

    def forget_job(self, job_ids: Iterable[str]) -> None:
        ids = set(job_ids)
        with self._lock:
            for key in [k for k in self._items if k[0] in ids]:
                self._bytes -= len(self._items.pop(key))

    def size(self) -> tuple[int, int]:
        """(entries, bytes)."""
        with self._lock:
            return len(self._items), self._bytes


CACHE = RenderCache()


def forget_job(job_ids: Iterable[str]) -> None:
    """Drop every picture of these jobs (they were deleted or expired)."""
    CACHE.forget_job(job_ids)


GATE = RenderGate(MAX_CONCURRENT_RENDERS, MAX_RENDERS_PER_USER, SLOT_WAIT_S)


def render_slot(user_id: str) -> AbstractContextManager[None]:
    """Hold a render slot for `user_id` while loading the PDF and rendering from it.
    Entering it raises RenderLimited or RenderBusy."""
    return GATE.slot(user_id)


def _child(args: list[str], data: bytes, timeout_s: float) -> bytes:
    """Run `python -m readablestem.web.pdf_probe ARGS` with the PDF on stdin; its stdout, or
    RenderFailed. The caller holds a render slot (`render_slot`). MuPDF's own messages
    (stderr) are discarded, never logged: they can quote the PDF."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "readablestem.web.pdf_probe", *args],
            input=data,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=child_env(),  # no keys or secrets for the process that reads the PDF
        )
    except (subprocess.TimeoutExpired, OSError):
        raise RenderFailed() from None
    if completed.returncode != 0:
        raise RenderFailed()
    return completed.stdout


def _numbers(box: BBox) -> str:
    return ",".join(f"{v:.2f}" for v in box)


def render_region(
    pdf: bytes | Callable[[], bytes],
    page: int,
    *,
    zoom: float = PAGE_ZOOM,
    max_side: int = PAGE_MAX_SIDE_PX,
    clip: BBox | None = None,
    mark: BBox | None = None,
    chars: Iterable[str] = (),
    box: BBox | None = None,
    snap: bool = False,
    trim: bool = False,
    cache_as: tuple[str, str] | None = None,
) -> bytes:
    """PNG of page `page` (0-based), or of `clip` on it; glyphs of `chars` inside `mark`
    are outlined, and so is `box` as a whole; with `snap` the clip's edges move to whole
    lines and words (`pdf_probe.snap_band`); with `trim` the words its left and right
    edges cut are painted over, the clip unchanged (`pdf_probe.cut_words`). Raises
    RenderFailed.

    `cache_as=(job_id, upload_id)` is what the web routes pass: the picture is looked up
    in, and kept in, `CACHE` under the exact arguments of the render. `upload_id` names the
    upload row, which is never rewritten (a different upload is a different row), so a hit
    is always the picture of this upload. `pdf` may then be a function that loads the PDF,
    called only on a miss: a repeat loads nothing."""
    args = ["render", str(page), f"{zoom:.4f}", str(max_side)]
    if clip is not None:
        args += ["--clip", _numbers(clip)]
    hexes = ",".join(f"{ord(c):x}" for c in sorted(set(chars)) if len(c) == 1)
    if mark is not None and hexes:
        args += ["--mark", _numbers(mark), "--chars", hexes]
    if box is not None:
        args += ["--box", _numbers(box)]
    if snap:
        args += ["--snap", "1"]
    if trim:
        args += ["--trim", "1"]
    what = tuple(args)
    if cache_as is not None and (hit := CACHE.get(*cache_as, what)) is not None:
        return hit
    png = _child(args, pdf if isinstance(pdf, bytes) else pdf(), RENDER_TIMEOUT_S)
    if not png.startswith(PNG_MAGIC):
        raise RenderFailed()
    if cache_as is not None:
        CACHE.put(*cache_as, what, png)
    return png


def render_page(
    pdf: bytes | Callable[[], bytes], page: int, cache_as: tuple[str, str] | None = None
) -> bytes:
    """The whole page at `PAGE_ZOOM`."""
    return render_region(pdf, page, cache_as=cache_as)


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


def region_text(pdf: bytes, page: int, clip: BBox) -> str:
    """The words inside `clip` on page `page` (0-based): the text layer of a region the
    reviewer marked. Raises RenderFailed."""
    out = _child(["text", str(page), _numbers(clip)], pdf, SIZES_TIMEOUT_S)
    try:
        answer = json.loads(out.decode("utf-8").strip().splitlines()[-1])
        text = answer["text"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise RenderFailed() from None
    if not isinstance(text, str):
        raise RenderFailed()
    return text
