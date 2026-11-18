"""A figure of a LaTeX project as a PNG: the file `\\includegraphics` named, from the project's own
folder only.

`find` names the file the way LaTeX does (`\\includegraphics{name}` with no extension is the usual
way to write it). The path comes from LaTeXML's output, which an uploaded document wrote, so it
is treated as untrusted: it is resolved inside the project folder, must be a regular file (never
a link, `os.O_NOFOLLOW`), and must be a PNG, JPEG, GIF or PDF (the first page); `.eps` and `.svg`
are not read. Sizes are capped before
decoding (a decompression bomb is a small file with a huge canvas), and so is how far a PDF
page's drawing instructions inflate (`core.content_budget`). Anything else is None: the figure
then has no picture and becomes a decision.
"""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path

from readablestem.core import content_budget
from readablestem.core.images import MAX_IMAGE_PIXELS, within_budget

MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_SIDE_PX = 2400
PDF_DPI = 150
# The kinds that are read, in the order pdfTeX tries them for a name with no extension.
READ_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".gif")


def _target(project: Path, name: str) -> Path | None:
    """`project/name` resolved, when it stays inside `project` and is of a kind that is read."""
    if not name or "\x00" in name or name.startswith(("/", "~")) or "\\" in name:
        return None
    root = project.resolve()
    target = (root / name).resolve()
    if root not in target.parents or target.suffix.lower() not in READ_SUFFIXES:
        return None
    return target


def find(project: Path, written: str, candidates: str = "") -> str:
    """The file of the project that `\\includegraphics{written}` shows, as LaTeX finds it: the
    first of LaTeXML's own `candidates` (comma separated) that is inside the project and of a kind
    that is read; else the written name with each read suffix in pdfTeX's order, in the written
    case and in upper case. `written` itself when nothing is found, so a decision names it."""
    for name in (n.strip() for n in candidates.split(",")):
        if (target := _target(project, name)) is not None and target.is_file():
            return name
    for suffix in READ_SUFFIXES:
        for tried in (written + suffix, written + suffix.upper()):
            if (target := _target(project, tried)) is not None and target.is_file():
                return tried
    return written


def _read_project_file(project: Path, name: str) -> bytes | None:
    """The bytes of `project/name` when it is a regular file inside `project`, else None."""
    target = _target(project, name)
    if target is None:
        return None
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            return None
        return handle.read(MAX_FILE_BYTES + 1)


def _raster_to_png(raw: bytes) -> bytes | None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS or width < 1 or height < 1:
                return None
            image.load()
            if image.mode not in ("RGB", "RGBA", "L"):
                image = image.convert(
                    "RGBA" if "A" in image.mode or "transparency" in image.info else "RGB"
                )
            scale = min(1.0, MAX_SIDE_PX / max(width, height))
            if scale < 1.0:
                image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))))
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
    except Exception:  # a damaged or unsupported image: no picture, never a failed job
        return None


def _pdf_to_png(raw: bytes) -> bytes | None:
    import pymupdf

    try:
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            if document.page_count < 1 or document.needs_pass:
                return None
            page = document[0]
            rect = page.rect
            if rect.width <= 0 or rect.height <= 0 or rect.width * rect.height > 20_000_000:
                return None
            # Drawing the page decodes its pictures: not one over the pixel budget.
            if any(not within_budget(w, h) for _, _, w, h, *_ in document.get_page_images(0)):
                return None
            # ... and parses its drawing instructions: not more than the content budget.
            content_budget.check(document, [0])
            scale = min(PDF_DPI / 72, MAX_SIDE_PX / max(rect.width, rect.height))
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            return pixmap.tobytes("png")
    except Exception:
        return None


def svg_to_png(svg: bytes) -> bytes | None:
    """An inline SVG (a picture LaTeXML drew) as a PNG, drawn by MuPDF (no network, no scripts)."""
    import pymupdf

    if len(svg) > 5 * 1024 * 1024:
        return None
    try:
        with pymupdf.open(stream=svg, filetype="svg") as document:
            page = document[0]
            rect = page.rect
            if rect.width <= 0 or rect.height <= 0:
                return None
            scale = min(3.0, MAX_SIDE_PX / max(rect.width, rect.height))
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    except Exception:
        return None


def png_of(raw: bytes) -> bytes | None:
    """The PNG of the picture `raw` holds (a raster image, or the first page of a PDF, by what
    the bytes are and not by any name), under the same size limits; None when it is neither."""
    if len(raw) > MAX_FILE_BYTES:
        return None
    if raw.startswith(b"%PDF"):  # what the file holds, not what its name says
        return _pdf_to_png(raw)
    return _raster_to_png(raw)


def figure_png(project: Path, name: str) -> bytes | None:
    """The PNG of the project's image file `name` (a path relative to the project), or None."""
    raw = _read_project_file(project, name)
    return None if raw is None else png_of(raw)
