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

The review screen uses the same child, with the same memory cap, for two more things
(`web.page_images` is the caller):

- `python -m a11y_stem.web.pdf_probe render PAGE ZOOM MAX_SIDE [--clip X0,Y0,X1,Y1]
  [--mark X0,Y0,X1,Y1 --chars HEX,...] [--box X0,Y0,X1,Y1]` writes one PNG of a page (or
  of a region of it) to stdout, its long side at most `MAX_SIDE` (never more than
  `RENDER_MAX_SIDE_PX`). `--mark` outlines the glyphs of the listed characters (Unicode
  code points in hex) inside that region, so a flagged symbol is outlined in the PDF's
  own image; `--box` outlines a whole region (a heading among the lines around it). On
  any failure it writes nothing and exits with status 2.
- `python -m a11y_stem.web.pdf_probe sizes` prints {"ok": true, "sizes": [[W, H], ...]}:
  every page's size in points (for results stored before they recorded it).

Nothing is written to disk and nothing is kept: the PDF arrives on stdin and the child
exits after one answer.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf

PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
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


# --------------------------------------------------------------------------- review images

# Longest side of any image this child renders, whatever it is asked for: a 2400 px RGB
# pixmap is 17 MB, well inside `MEMORY_LIMIT_BYTES` next to a 20 MB PDF.
RENDER_MAX_SIDE_PX = 2400
RENDER_MAX_ZOOM = 12.0  # 864 dpi: only reached for a small region (an inline equation)
MARK_COLOR = (0.643, 0.086, 0.102)  # the app's --danger, #a4161a: 7.0:1 against white
MARK_WIDTH_PX = 2.5  # outline width in the rendered image, whatever the zoom
MARK_GAP_PT = 0.8  # space between a glyph and its outline
MARK_SLACK_PT = 2.0  # glyphs this far outside the marked region still count (scripts)
RENDER_FAILED = 2  # exit status when nothing was rendered


def _fold(ch: str) -> str:
    """A character as the review screen names it: math bold "𝛚" and plain "ω" alike."""
    return unicodedata.normalize("NFKC", ch).casefold()


def _open(data: bytes) -> pymupdf.Document:
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    if not looks_like_pdf(data):
        raise ProbeRejected("not_pdf")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise ProbeRejected("unreadable") from None
    if doc.needs_pass:
        doc.close()
        raise ProbeRejected("encrypted")
    return doc


def page_sizes(data: bytes) -> list[tuple[float, float]]:
    """Every page's (width, height) in points, as `page.rect` gives them (after rotation,
    the frame layout's boxes are in)."""
    doc = _open(data)
    try:
        return [(round(p.rect.width, 2), round(p.rect.height, 2)) for p in doc]
    finally:
        doc.close()


def _outline_glyphs(
    page: pymupdf.Page, region: pymupdf.Rect, chars: set[str], width_pt: float
) -> None:
    """Draw a box around every glyph of `chars` inside `region` (in this child's copy of
    the document only: it is never saved)."""
    import pymupdf

    flags = pymupdf.TEXT_ACCURATE_BBOXES | pymupdf.TEXT_MEDIABOX_CLIP
    slack = MARK_SLACK_PT
    area = pymupdf.Rect(region.x0 - slack, region.y0 - slack, region.x1 + slack, region.y1 + slack)
    found = []
    for block in page.get_text("rawdict", clip=area, flags=flags).get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    box = pymupdf.Rect(char["bbox"])
                    if _fold(char.get("c", "")) in chars and not box.is_empty:
                        found.append(box)
    gap = MARK_GAP_PT
    for box in found:
        outline = pymupdf.Rect(box.x0 - gap, box.y0 - gap, box.x1 + gap, box.y1 + gap)
        page.draw_rect(outline, color=MARK_COLOR, width=width_pt)


def render(
    data: bytes,
    *,
    page: int,
    zoom: float,
    max_side: int,
    clip: tuple[float, float, float, float] | None = None,
    mark: tuple[float, float, float, float] | None = None,
    chars: str = "",
    box: tuple[float, float, float, float] | None = None,
) -> bytes:
    """PNG of one page (0-based), or of `clip` on it, at `zoom` pixels per point, its long
    side capped at `max_side` (and `RENDER_MAX_SIDE_PX`). `box` is outlined as a whole
    (a heading among the lines around it). Raises ProbeRejected."""
    import pymupdf

    doc = _open(data)
    try:
        if not 0 <= page < doc.page_count:
            raise ProbeRejected("no_such_page")
        current = doc[page]
        if max(current.rect.width, current.rect.height) > MAX_PAGE_SIDE_PT:
            raise ProbeRejected("page_too_large")
        area = current.rect if clip is None else pymupdf.Rect(clip) & current.rect
        if area.is_empty or area.width < 0.5 or area.height < 0.5:
            raise ProbeRejected("empty_region")
        side = min(max_side, RENDER_MAX_SIDE_PX)
        # MuPDF rounds the clip outwards to whole pixels: one pixel of headroom.
        scale = min(zoom, RENDER_MAX_ZOOM, (side - 1) / max(area.width, area.height))
        if mark is not None and chars:
            wanted = {_fold(c) for c in chars}
            _outline_glyphs(current, pymupdf.Rect(mark), wanted, MARK_WIDTH_PX / scale)
        if box is not None:
            gap = 2 * MARK_GAP_PT
            x0, y0, x1, y1 = box
            outline = pymupdf.Rect(x0 - gap, y0 - gap, x1 + gap, y1 + gap)
            current.draw_rect(outline, color=MARK_COLOR, width=MARK_WIDTH_PX / scale)
        pixmap = current.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=area, alpha=False)
        return pixmap.tobytes("png")
    finally:
        doc.close()


def _rect(value: str) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in value.split(","))
    return x0, y0, x1, y1


def _render_main(args: list[str], data: bytes) -> int:
    page, zoom, max_side = int(args[0]), float(args[1]), int(args[2])
    options = dict(zip(args[3::2], args[4::2], strict=False))
    chars = "".join(chr(int(h, 16)) for h in options.get("--chars", "").split(",") if h)
    png = render(
        data,
        page=page,
        zoom=zoom,
        max_side=max_side,
        clip=_rect(options["--clip"]) if "--clip" in options else None,
        mark=_rect(options["--mark"]) if "--mark" in options else None,
        chars=chars,
        box=_rect(options["--box"]) if "--box" in options else None,
    )
    sys.stdout.buffer.write(png)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in {"render", "sizes"}:
        data = sys.stdin.buffer.read()
        try:
            if args[0] == "render":
                return _render_main(args[1:], data)
            sizes = page_sizes(data)
        except Exception:  # ProbeRejected, bad arguments, anything MuPDF raises
            return RENDER_FAILED
        sys.stdout.write(json.dumps({"ok": True, "sizes": sizes}) + "\n")
        return 0
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
