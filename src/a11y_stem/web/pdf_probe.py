"""Open an uploaded PDF and count its pages, in a child process.

`python -m a11y_stem.web.pdf_probe MAX_PAGES` reads the PDF from stdin and prints one
JSON line: {"ok": true, "pages": N} or {"ok": false, "code": CATEGORY}.

Why a separate process: MuPDF is C code parsing bytes from the internet. If a malformed
file crashes or hangs it, only this child dies (the caller kills it after a timeout and
treats the file as unreadable). It also keeps PyMuPDF, which is not thread-safe, off the
web server's request threads. Kept free of web-framework imports so it starts fast. The
child caps its own address space (`MEMORY_LIMIT_BYTES`), so a file that makes MuPDF
allocate without bound fails here as "unreadable" instead of exhausting the instance.

Besides counting pages it refuses, before any quota is spent:

- pages larger than `MAX_PAGE_SIDE_PT` on a side ("page_too_large"). MuPDF accepts page
  boxes far beyond the PDF limit, and the conversion's crash probe renders whole pages:
  an 18000 pt page needs about 1 GB there, more than the instance has.
- PDFs with nothing to convert: every page a scanned image ("scanned") or blank
  ("no_content"); see `pipeline.page_kinds`.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf

PDF_MAGIC = b"%PDF-"
# Largest page side accepted, in points (5000 pt is about 1.76 m; an A0 poster is 3370 pt).
# The conversion renders whole pages at 72 dpi in its crash probe: 5000 x 5000 px of RGB is
# 75 MB, while the 14400 pt PDF maximum would be 620 MB and MuPDF accepts even larger.
MAX_PAGE_SIDE_PT = 5000.0
# Address-space cap for the probe child. Python plus PyMuPDF needs well under this; a
# normal probe peaks around 60 MB resident.
MEMORY_LIMIT_BYTES = 768 * 1024 * 1024
# Acrobat accepts the header anywhere in the first 1024 bytes (some generators prepend a
# few bytes), so we do too. Anything else is not a PDF, whatever its name or media type.
MAGIC_WINDOW = 1024


class ProbeRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def looks_like_pdf(data: bytes) -> bool:
    return PDF_MAGIC in data[:MAGIC_WINDOW]


def probe(data: bytes, *, max_pages: int) -> int:
    """Return the page count or raise ProbeRejected (in-process; see module docstring)."""
    if not data:
        raise ProbeRejected("empty")
    if not looks_like_pdf(data):
        raise ProbeRejected("not_pdf")
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # PyMuPDF raises several types for damaged files
        raise ProbeRejected("unreadable") from None
    try:
        # needs_pass: a user password is required to read it. Files with only an owner
        # password (permissions) open fine and are accepted.
        if doc.needs_pass:
            raise ProbeRejected("encrypted")
        pages = doc.page_count
        if pages == 0:
            raise ProbeRejected("no_pages")
        if pages > max_pages:
            raise ProbeRejected("too_many_pages")
        _check_pages(doc)
    finally:
        doc.close()
    return pages


def _check_pages(doc: pymupdf.Document) -> None:
    """Refuse oversized pages and documents with nothing to convert (module docstring)."""
    from a11y_stem.pipeline.page_kinds import nothing_to_convert, page_kinds

    try:
        for page in doc:
            if max(page.rect.width, page.rect.height) > MAX_PAGE_SIDE_PT:
                raise ProbeRejected("page_too_large")
        reason = nothing_to_convert(page_kinds(doc))
    except ProbeRejected:
        raise
    except Exception:  # a page MuPDF cannot read would fail the conversion as well
        raise ProbeRejected("unreadable") from None
    if reason is not None:
        raise ProbeRejected(reason)


def limit_memory(max_bytes: int = MEMORY_LIMIT_BYTES) -> None:
    """Cap this process's address space (Linux, macOS). Best effort elsewhere."""
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            max_bytes = min(max_bytes, hard)
        if soft == resource.RLIM_INFINITY or soft > max_bytes:
            resource.setrlimit(resource.RLIMIT_AS, (max_bytes, hard))
    except (ImportError, ValueError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    max_pages = int(args[0]) if args else 1
    data = sys.stdin.buffer.read()
    try:
        answer: dict[str, object] = {"ok": True, "pages": probe(data, max_pages=max_pages)}
    except ProbeRejected as exc:
        answer = {"ok": False, "code": exc.code}
    except Exception:
        answer = {"ok": False, "code": "unreadable"}
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    limit_memory()  # here, not in main(): never cap a process that imports this module
    raise SystemExit(main())
