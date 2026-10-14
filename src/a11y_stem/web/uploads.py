"""Upload intake: size limit while streaming, PDF magic bytes, readability and page count.

Uploads are untrusted bytes from the internet, so:

- The size limit is enforced *while the body streams in* (`read_upload_form`), not after
  the whole body has been buffered. A Content-Length over the limit is refused before
  reading anything.
- The file is kept in memory (never spooled to disk) and is only stored once every check
  has passed.
- The PDF is opened in a short-lived child process (`inspect_pdf_isolated`, see
  `web.pdf_probe`): a malformed file that crashes or hangs MuPDF takes down a child, not
  the web server.

- At most `MAX_CONCURRENT_UPLOADS` uploads are taken in at once, and one per user
  (`UploadGate`). An upload is held in memory while it is read, copied once, and read
  again by the probe child, so each costs about three times its size: with the defaults
  (20 MB, 3 at once) that stays near 200 MB, well inside the instance's 1 GiB, however
  many requests Cloud Run sends (concurrency 20). Over the limit the user gets a short
  "try again in a moment" page before any of the body is read.

Every rejection is an `UploadRejected` with a category `code`, safe to log.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import unicodedata
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request

from a11y_stem.core.childenv import child_env
from a11y_stem.web import office_convert
from a11y_stem.web.pdf_probe import ProbeRejected, looks_like_pdf, probe

# Room for the multipart boundaries and the small text fields around the file itself.
FORM_OVERHEAD_BYTES = 64 * 1024
INSPECT_TIMEOUT_S = 30.0

# Status code per rejection code.
REJECTION_STATUS = {
    "missing_file": 400,
    "bad_form": 400,
    "empty": 400,
    "not_pdf": 415,
    "too_large": 413,
    "encrypted": 422,
    "unreadable": 422,
    "damaged": 422,
    "no_pages": 422,
    "too_many_pages": 422,
    "page_too_large": 422,
    "scanned": 422,
    "scanned_ocr": 422,
    "handwritten": 422,
    "handwritten_no_text": 422,
    "no_content": 422,
    # Word and PowerPoint files (web.office_convert).
    "office_unavailable": 422,
    "office_unsupported": 415,
    "office_old_or_locked": 422,
    "office_unsafe": 422,
    "office_failed": 422,
    "office_busy": 503,
}


class UploadRejected(Exception):
    """The upload cannot be accepted. `code` is a category (see REJECTION_STATUS);
    `pages` the page count when it is the reason ("too_many_pages")."""

    def __init__(self, code: str, pages: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.pages = pages

    @property
    def status_code(self) -> int:
        return REJECTION_STATUS.get(self.code, 400)


@dataclass
class UploadForm:
    fields: dict[str, str]
    filename: str
    data: bytes | None  # None when no file was chosen


def max_upload_bytes(max_upload_mb: int) -> int:
    return max_upload_mb * 1024 * 1024


@dataclass
class UploadGate:
    """Bounds uploads in progress: `limit` in total and one per user (module docstring).

    `enter(user_id)` returns None when admitted (call `leave(user_id)` afterwards, in a
    `finally`), else the reason: "busy" (too many in total) or "own_upload" (this user
    already has one in progress). Per process; each instance has its own memory.
    """

    limit: int
    _users: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def enter(self, user_id: str) -> str | None:
        with self._lock:
            if user_id in self._users:
                return "own_upload"
            if len(self._users) >= self.limit:
                return "busy"
            self._users.add(user_id)
            return None

    def leave(self, user_id: str) -> None:
        with self._lock:
            self._users.discard(user_id)

    @property
    def in_progress(self) -> int:
        with self._lock:
            return len(self._users)


class _InMemoryParser(MultiPartParser):
    # Keep the file in memory (the default spools to a temp file after 1 MB) so document
    # bytes never touch the server's disk. Memory is bounded by the streaming limit.
    spool_max_size = 2**31


async def read_upload_form(request: Request, *, max_bytes: int) -> UploadForm:
    """Parse a multipart upload with one file, refusing more than `max_bytes` of file data.

    Raises UploadRejected("too_large") as soon as the body exceeds the limit (plus a
    little room for the form fields), and UploadRejected("bad_form") for malformed bodies.
    """
    limit = max_bytes + FORM_OVERHEAD_BYTES
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise UploadRejected("too_large")
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        raise UploadRejected("bad_form")

    async def limited() -> AsyncGenerator[bytes]:
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise UploadRejected("too_large")
            yield chunk

    parser = _InMemoryParser(
        request.headers, limited(), max_files=1, max_fields=20, max_part_size=64 * 1024
    )
    try:
        form = await parser.parse()
    except MultiPartException:
        raise UploadRejected("bad_form") from None
    try:
        fields: dict[str, str] = {}
        upload: UploadFile | None = None
        for key, value in form.multi_items():
            if isinstance(value, str):
                fields.setdefault(key, value)
            elif key == "pdf" and upload is None:
                upload = value
        if upload is None or not upload.filename:
            return UploadForm(fields=fields, filename="", data=None)
        data = await upload.read()
        if len(data) > max_bytes:
            raise UploadRejected("too_large")
        return UploadForm(fields=fields, filename=safe_filename(upload.filename), data=data)
    finally:
        await form.close()


def safe_filename(raw: str | None, *, limit: int = 200) -> str:
    """Display name for an uploaded file: base name only, no control characters, bounded.

    The filename is document-derived: it is shown to its owner and deleted with the job,
    never logged.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if len(name) > limit:
        stem, dot, ext = name.rpartition(".")
        short_ext = dot and len(ext) <= 10
        name = (stem[: limit - len(ext) - 1] + dot + ext) if short_ext else name[:limit]
    return name or "document.pdf"


def inspect_pdf(data: bytes, *, max_pages: int) -> int:
    """Return the page count, or raise UploadRejected. In-process: prefer the isolated one."""
    try:
        return probe(data, max_pages=max_pages)
    except ProbeRejected as exc:
        raise UploadRejected(exc.code, exc.pages) from None


# --------------------------------------------------------------------------- isolation


def inspect_pdf_isolated(
    data: bytes, *, max_pages: int, timeout_s: float = INSPECT_TIMEOUT_S
) -> int:
    """`inspect_pdf` in a child process (`web.pdf_probe`); a crash or hang is "unreadable".

    Cheap checks (empty, magic bytes) run here first so obvious junk never starts a child.
    Costs about 0.1 s per upload. Call it from a worker thread, not the event loop.
    """
    if not data:
        raise UploadRejected("empty")
    if not looks_like_pdf(data):
        raise UploadRejected("not_pdf")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "a11y_stem.web.pdf_probe", str(max_pages)],
            input=data,
            capture_output=True,  # MuPDF's own messages are discarded, never logged
            timeout=timeout_s,
            check=False,
            env=child_env(),  # no keys or secrets for the process that reads the PDF
        )
        answer = json.loads(completed.stdout.decode("utf-8").strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, ValueError, IndexError, OSError):
        raise UploadRejected("unreadable") from None
    if not isinstance(answer, dict):
        raise UploadRejected("unreadable")
    if answer.get("ok") is True and isinstance(answer.get("pages"), int):
        return answer["pages"]
    code = answer.get("code")
    pages = answer.get("pages")
    raise UploadRejected(
        code if code in REJECTION_STATUS else "unreadable",
        pages if isinstance(pages, int) and not isinstance(pages, bool) else None,
    )


# --------------------------------------------------------------------------- Word and PowerPoint

OFFICE_WAIT_S = 5.0  # how long a second conversion waits for the first before "busy"
OFFICE_OVERHEAD_S = 20.0  # the wrapper's own time on top of LibreOffice's
# One LibreOffice at a time per instance: it is the heaviest thing the app starts (hundreds
# of megabytes), and the instance is sized for one.
_OFFICE_SLOT = threading.BoundedSemaphore(1)


def is_office_upload(data: bytes) -> bool:
    """Not a PDF, but a ZIP or an old Office container: it goes to the converter."""
    return not looks_like_pdf(data) and office_convert.looks_like_office(data)


def convert_office_isolated(
    data: bytes, *, soffice: str | None, timeout_s: float = office_convert.DEFAULT_TIMEOUT_S
) -> bytes:
    """A Word or PowerPoint file as a tagged PDF, made in a child process
    (`web.office_convert`), or `UploadRejected` with a category. Call it from a worker
    thread. The caller probes the PDF afterwards like any upload."""
    if soffice is None:
        raise UploadRejected("office_unavailable")
    if not _OFFICE_SLOT.acquire(timeout=OFFICE_WAIT_S):
        raise UploadRejected("office_busy")
    try:
        return _run_office_child(data, soffice, timeout_s)
    finally:
        _OFFICE_SLOT.release()


def _run_office_child(data: bytes, soffice: str, timeout_s: float) -> bytes:
    try:
        child = subprocess.Popen(
            [sys.executable, "-m", "a11y_stem.web.office_convert", soffice, str(timeout_s)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # whatever LibreOffice says is discarded, never logged
            env=child_env(),
            start_new_session=True,
        )
    except OSError:
        raise UploadRejected("office_failed") from None
    try:
        out, _ = child.communicate(data, timeout=timeout_s + OFFICE_OVERHEAD_S)
    except (subprocess.TimeoutExpired, OSError):
        office_convert.kill_group(child)
        raise UploadRejected("office_failed") from None
    finally:
        office_convert.kill_group(child)
    header, _, body = out.partition(b"\n")
    try:
        answer = json.loads(header.decode("utf-8"))
    except ValueError:
        raise UploadRejected("office_failed") from None
    if isinstance(answer, dict) and answer.get("ok") is True and body.startswith(b"%PDF-"):
        return body
    code = answer.get("code") if isinstance(answer, dict) else None
    raise UploadRejected(code if code in REJECTION_STATUS else "office_failed")
