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
- PDFs with nothing to convert (`pipeline.page_type.document_refusal`): every page
  handwritten ("handwritten", or "handwritten_no_text" without a hidden recognition
  layer), a scanned image ("scanned", or "scanned_ocr" with a hidden OCR layer), or
  blank ("no_content"). Text nobody sees never counts as text. The text of every page
  is read first and the check stops at the first typed page, so only such documents
  pay for the drawings and the handwriting analysis, which has a time budget
  (`page_type.INK_BUDGET_S`): a page not analysed in time never refuses the upload.

The review screen uses the same child, with the same memory cap, for two more things
(`web.page_images` is the caller):

- `python -m a11y_stem.web.pdf_probe render PAGE ZOOM MAX_SIDE [--clip X0,Y0,X1,Y1]
  [--mark X0,Y0,X1,Y1 --chars HEX,...] [--box X0,Y0,X1,Y1] [--snap 1] [--trim 1]`
  writes one PNG of a page (or of a region of it) to stdout, its long side at most
  `MAX_SIDE` (never more than `RENDER_MAX_SIDE_PX`). `--mark` outlines the glyphs of the
  listed characters (Unicode code points in hex) inside that region, so a flagged symbol
  is outlined in the PDF's own image; `--box` outlines a whole region (a heading among
  the lines around it); `--snap 1` moves the region's top and bottom to whole lines and
  paints over the words its right edge would cut (`snap_band`, from the page's text here
  in the child), so no slice of a line or a word shows; `--trim 1` paints over the words
  its left and right edges cut, leaving the region as asked (an equation's compare
  crop). On any failure it writes nothing and exits with status 2.
- `python -m a11y_stem.web.pdf_probe extract PAGES SHRINK` writes a new PDF of the listed
  0-based pages (comma-separated, in that order) to stdout, for converting a large file in
  parts (`web.routes.parts`); with SHRINK `1` the pictures sharper than `SHRINK_ABOVE_DPI`
  are saved at `SHRINK_TO_DPI` first (`Document.rewrite_images`), which is what makes a part
  of a heavy file small enough to convert. Unused objects of the pages left out are dropped,
  so the part is smaller than the file in proportion. On any failure it writes nothing and
  exits with status 2.
- `python -m a11y_stem.web.pdf_probe sizes` prints {"ok": true, "sizes": [[W, H], ...]}:
  every page's size in points (for results stored before they recorded it).
- `python -m a11y_stem.web.pdf_probe text PAGE X0,Y0,X1,Y1` prints {"ok": true, "text": "..."}:
  the words whose middle is inside the region, line by line (for a region the reviewer
  marked as an equation: its text layer is evidence for the checks, T3-5).

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
    """`code` is a category; `pages` the page count when it is the reason."""

    def __init__(self, code: str, pages: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.pages = pages


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
            # A file cut short (a download that did not finish) opens only after MuPDF
            # repairs it, and its page tree is often lost: damaged, not empty.
            raise ProbeRejected("damaged" if doc.is_repaired else "no_pages")
        if pages > max_pages:
            raise ProbeRejected("too_many_pages", pages=pages)
        _check_pages(doc)
    finally:
        doc.close()
    return pages


def _check_pages(doc: pymupdf.Document) -> None:
    """Refuse oversized pages and documents with nothing to convert (module docstring)."""
    from a11y_stem.pipeline.page_type import document_refusal

    try:
        for page in doc:
            if max(page.rect.width, page.rect.height) > MAX_PAGE_SIDE_PT:
                raise ProbeRejected("page_too_large")
        reason = document_refusal(doc)
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


SNAP_PAD_PT = 1.5  # room left round the text when a band is snapped to whole lines
SNAP_MAX_GROW_PT = 40.0  # a band edge never moves further than this to take a line in
SNAP_ABOVE_PT = 5.0  # room above a heading with no line above it in its band
INK_ABOVE, INK_BELOW = 0.8, 0.25  # of the font size, from the baseline
HIDE_COLOR = (1.0, 1.0, 1.0)  # a word cut by the band's right edge is painted over


def _ink_lines(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Each text line's box from its baselines: cap height above, descenders below. The
    boxes MuPDF reports use the fonts' full ascent and descent, so lines one above the
    other overlap there, and no edge between them would cut neither."""
    import pymupdf

    out = []
    for block in page.get_text("dict", flags=pymupdf.TEXT_MEDIABOX_CLIP).get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            x0, _, x1, _ = line["bbox"]
            y0 = min(s["origin"][1] - INK_ABOVE * s["size"] for s in spans)
            y1 = max(s["origin"][1] + INK_BELOW * s["size"] for s in spans)
            out.append(pymupdf.Rect(x0, y0, x1, y1))
    return out


def snap_band(
    page: pymupdf.Page, area: pymupdf.Rect, keep: pymupdf.Rect | None = None
) -> tuple[pymupdf.Rect, list[pymupdf.Rect]]:
    """`area` with its top and bottom moved so it cuts no line of text, and the words its
    right edge would cut (to be painted over, so each line ends on a whole word, as
    ragged as left-aligned text). A line across the top edge comes in whole (the line
    just above a heading is context worth seeing), and with no line above `keep` (the
    heading) the band starts just above it; a line across the bottom edge is left out,
    unless it is the first line below the heading. A word across the left edge moves
    that edge out to take it in. Only lines that share some width with the area count,
    so the next column is not pulled in. No edge moves more than `SNAP_MAX_GROW_PT`."""
    import pymupdf

    pad = SNAP_PAD_PT
    top, bottom, left, right = area.y0, area.y1, area.x0, area.x1
    beside = sorted(
        (r for r in _ink_lines(page) if r.x1 > left and r.x0 < right), key=lambda r: r.y0
    )
    head_top = keep.y0 if keep is not None else top
    head_bottom = keep.y1 if keep is not None else top
    above = [r for r in beside if r.y1 <= head_top + 0.5]
    under = [r for r in beside if r.y0 >= head_bottom - 0.5]
    across_top = [r for r in above if r.y0 < top < r.y1]
    if across_top:
        top = max(min(r.y0 for r in across_top) - pad, area.y0 - SNAP_MAX_GROW_PT)
    elif keep is not None and not [r for r in above if r.y0 >= top]:
        # No line above the heading in the band: only space, or a rule from a box
        # above. Start just above the heading instead.
        top = max(top, keep.y0 - SNAP_ABOVE_PT)
    across_bottom = [r for r in under if r.y0 < bottom < r.y1]
    if across_bottom:
        # The first line under the heading comes in whole; any later one is left out.
        cut = across_bottom[0]
        first = cut is under[0]
        bottom = min(cut.y1 + pad, area.y1 + SNAP_MAX_GROW_PT) if first else cut.y0 - pad
    words = [
        pymupdf.Rect(w[:4])
        for w in page.get_text("words", flags=pymupdf.TEXT_MEDIABOX_CLIP)
        if w[3] > top + 0.5 and w[1] < bottom - 0.5
    ]
    across_left = [w.x0 for w in words if w.x0 < left < w.x1]
    if across_left:
        left = max(min(across_left) - pad, area.x0 - SNAP_MAX_GROW_PT)
    hide = [_cover(w) for w in words if w.x0 < right < w.x1]
    return pymupdf.Rect(left, top, right, bottom) & page.rect, hide


def _cover(word: pymupdf.Rect) -> pymupdf.Rect:
    """What to paint over to hide a word. Descenders can reach below a word's box (a "p"
    in Times), so a little more than the box."""
    import pymupdf

    return pymupdf.Rect(word.x0 - 0.6, word.y0 - 1.0, word.x1 + 0.6, word.y1 + 0.3 * word.height)


def cut_words(page: pymupdf.Page, area: pymupdf.Rect) -> list[pymupdf.Rect]:
    """What to paint over so no word shows cut at the left or right edge of `area` (an
    equation's compare crop: its edges stay where they are, so the proposal drawn under
    it keeps its scale)."""
    import pymupdf

    out = []
    for w in page.get_text("words", flags=pymupdf.TEXT_MEDIABOX_CLIP):
        word = pymupdf.Rect(w[:4])
        if word.y1 <= area.y0 or word.y0 >= area.y1:
            continue
        if word.x0 < area.x0 < word.x1 or word.x0 < area.x1 < word.x1:
            out.append(_cover(word))
    return out


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
    snap: bool = False,
    trim: bool = False,
) -> bytes:
    """PNG of one page (0-based), or of `clip` on it, at `zoom` pixels per point, its long
    side capped at `max_side` (and `RENDER_MAX_SIDE_PX`). `box` is outlined as a whole
    (a heading among the lines around it); with `snap` the clip is first moved to whole
    lines and words round it (`snap_band`); with `trim` the words its side edges cut are
    painted over (`cut_words`). Raises ProbeRejected."""
    import pymupdf

    doc = _open(data)
    try:
        if not 0 <= page < doc.page_count:
            raise ProbeRejected("no_such_page")
        current = doc[page]
        if max(current.rect.width, current.rect.height) > MAX_PAGE_SIDE_PT:
            raise ProbeRejected("page_too_large")
        area = current.rect if clip is None else pymupdf.Rect(clip) & current.rect
        if snap and clip is not None:
            keep = pymupdf.Rect(box) if box is not None else None
            area, hide = snap_band(current, area, keep)
            for rect in hide:  # in this child's copy only: it is never saved
                current.draw_rect(rect, color=None, fill=HIDE_COLOR, width=0)
        elif trim and clip is not None:
            for rect in cut_words(current, area):
                current.draw_rect(rect, color=None, fill=HIDE_COLOR, width=0)
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


# "Shrink large pictures first": pictures sharper than this are saved at the second number,
# as JPEG at this quality. Text and vector drawings (equations set as text, charts drawn as
# lines) are not pictures and are not touched. 150 dpi is what the conversion reads figures
# at, so it loses nothing the model or the review screen would have used.
SHRINK_ABOVE_DPI = 200
SHRINK_TO_DPI = 150
SHRINK_QUALITY = 80


def extract(data: bytes, pages: list[int], *, shrink: bool = False) -> bytes:
    """A PDF of just `pages` (0-based, no repeats, in this order). Raises ProbeRejected."""
    doc = _open(data)
    try:
        if not pages or len(set(pages)) != len(pages):
            raise ProbeRejected("no_such_page")
        if any(p < 0 or p >= doc.page_count for p in pages):
            raise ProbeRejected("no_such_page")
        doc.select(pages)
        if shrink:
            doc.rewrite_images(
                dpi_threshold=SHRINK_ABOVE_DPI, dpi_target=SHRINK_TO_DPI, quality=SHRINK_QUALITY
            )
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


REGION_TEXT_MAX_CHARS = 2000


def region_text(data: bytes, *, page: int, clip: tuple[float, float, float, float]) -> str:
    """The words of page `page` (0-based) whose middle lies inside `clip`, in reading order,
    one space between words and between lines; at most `REGION_TEXT_MAX_CHARS` characters.
    Raises ProbeRejected."""
    import pymupdf

    doc = _open(data)
    try:
        if not 0 <= page < doc.page_count:
            raise ProbeRejected("no_such_page")
        current = doc[page]
        if max(current.rect.width, current.rect.height) > MAX_PAGE_SIDE_PT:
            raise ProbeRejected("page_too_large")
        area = pymupdf.Rect(clip) & current.rect
        found = []
        for x0, y0, x1, y1, word, block, line, number in current.get_text(
            "words", flags=pymupdf.TEXT_MEDIABOX_CLIP
        ):
            if area.x0 <= (x0 + x1) / 2 <= area.x1 and area.y0 <= (y0 + y1) / 2 <= area.y1:
                found.append((block, line, number, word))
        found.sort(key=lambda w: w[:3])
        return " ".join(w[3] for w in found)[:REGION_TEXT_MAX_CHARS]
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
        snap=options.get("--snap") == "1",
        trim=options.get("--trim") == "1",
    )
    sys.stdout.buffer.write(png)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "extract":
        data = sys.stdin.buffer.read()
        try:
            part = extract(data, [int(p) for p in args[1].split(",")], shrink=args[2] == "1")
        except Exception:  # ProbeRejected, bad arguments, anything MuPDF raises
            return RENDER_FAILED
        sys.stdout.buffer.write(part)
        return 0
    if args and args[0] in {"render", "sizes", "text"}:
        data = sys.stdin.buffer.read()
        try:
            if args[0] == "render":
                return _render_main(args[1:], data)
            if args[0] == "text":
                said = region_text(data, page=int(args[1]), clip=_rect(args[2]))
                sys.stdout.write(json.dumps({"ok": True, "text": said}) + "\n")
                return 0
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
        if exc.pages is not None:
            answer["pages"] = exc.pages
    except Exception:
        answer = {"ok": False, "code": "unreadable"}
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    limit_memory()  # here, not in main(): never cap a process that imports this module
    raise SystemExit(main())
