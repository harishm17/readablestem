"""Open an uploaded PDF and count its pages, in a child process.

`python -m a11y_stem.web.pdf_probe MAX_PAGES` reads the PDF from stdin and prints one
JSON line: {"ok": true, "pages": N} or {"ok": false, "code": CATEGORY}.

Why a separate process: MuPDF is C code parsing bytes from the internet. If a malformed
file crashes or hangs it, only this child dies (the caller kills it after a timeout and
treats the file as unreadable). It also keeps PyMuPDF, which is not thread-safe, off the
web server's request threads. Kept free of web-framework imports so it starts fast.
"""

from __future__ import annotations

import json
import sys

PDF_MAGIC = b"%PDF-"
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
    finally:
        doc.close()
    if pages == 0:
        raise ProbeRejected("no_pages")
    if pages > max_pages:
        raise ProbeRejected("too_many_pages")
    return pages


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
    raise SystemExit(main())
