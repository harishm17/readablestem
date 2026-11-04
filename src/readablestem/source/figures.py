"""A figure of a LaTeX project as a PNG: the file `\\includegraphics` named, from the project's own
folder only.

The path comes from LaTeXML's HTML, which an uploaded document wrote, so it is treated as
untrusted: it is resolved inside the project folder, must be a regular file (never a link,
`os.O_NOFOLLOW`), and must be a PNG, JPEG, GIF or PDF (the first page). Sizes are capped before
decoding (a decompression bomb is a small file with a huge canvas). Anything else is None: the
figure then has no picture and becomes a decision.
"""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path

MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SIDE_PX = 2400
PDF_DPI = 150
_OK_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf"}


def _read_project_file(project: Path, name: str) -> bytes | None:
    """The bytes of `project/name` when it is a regular file inside `project`, else None."""
    if not name or "\x00" in name or name.startswith(("/", "~")) or "\\" in name:
        return None
    root = project.resolve()
    target = (root / name).resolve()
    if root not in target.parents or target.suffix.lower() not in _OK_SUFFIXES:
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
            if width * height > MAX_PIXELS or width < 1 or height < 1:
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


def figure_png(project: Path, name: str) -> bytes | None:
    """The PNG of the project's image file `name` (a path relative to the project), or None."""
    raw = _read_project_file(project, name)
    if raw is None or len(raw) > MAX_FILE_BYTES:
        return None
    if name.lower().endswith(".pdf"):
        return _pdf_to_png(raw)
    return _raster_to_png(raw)
