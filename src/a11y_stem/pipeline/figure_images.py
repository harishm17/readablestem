"""Figure images in the student file, re-encoded so the file stays small (UX review MF23).

The crops are PNGs rendered at about 200 dpi (`layout.crop`), which suits the review
screen and the models but makes a heavy file for students: the seven figures of the
public demo (OpenStax Physics 2.3) were 1.7 MB of the 2.3 MB file, about 17 s to load
on slow 4G. Only the image written into the student file changes here; the stored crops,
and the review screen that shows them, keep the PNGs as they are.

For each figure image (`assemble._Renderer.figure`: described, decorative and flagged
figures, scanned pages included):

- at most `MAX_SIDE_PX` on the long side, never upscaled (1600 px is a 42rem column at
  about 2.4x zoom; the image is displayed at its printed size either way);
- photos and illustrations as JPEG (quality `JPEG_QUALITY`), unless the PNG is smaller;
- charts, diagrams and anything else as PNG, unless the JPEG is at most half its size:
  line art and text stay sharp, while a chart that is itself a photograph or a JPEG
  scan (where PNG is several times larger and JPEG loses nothing visible) becomes JPEG;
- an image with transparency, or one MuPDF cannot read, is kept as it is.

MuPDF does the work (no new dependency). Its own PNG is the "optimised" PNG: on the demo
figures it was smaller than Pillow's `optimize=True`, so an image that keeps its size
keeps its PNG bytes. A scaled image's PNG size is estimated from the original's
(bytes x area ratio) to choose the format, so a JPEG-bound scan is never encoded twice.
The result depends only on the crop and the figure type, so rendering a stored result
gives the same file every time (docs/design/content-features.md rule 3).
"""

from __future__ import annotations

import base64
import contextlib
import logging

from a11y_stem.pipeline.safe_html import escape_text

log = logging.getLogger(__name__)

MAX_SIDE_PX = 1600
JPEG_QUALITY = 82
PHOTO_TYPES = frozenset({"photo", "illustration"})
# Line art keeps PNG unless JPEG is at most this share of its size.
JPEG_SHARE_FOR_LINE_ART = 0.5
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _lock() -> contextlib.AbstractContextManager[object]:
    """PyMuPDF is not thread-safe: hold layout's lock, like every other PyMuPDF call (the
    web app re-renders on request threads while its worker may be cropping)."""
    from a11y_stem.pipeline import layout

    lock = getattr(layout, "_LOCK", None)
    return lock if lock is not None else contextlib.nullcontext()


def student_image(png: bytes, figure_type: str | None) -> tuple[str, bytes]:
    """(MIME type, bytes) of the image to embed for a figure crop (see the module
    docstring). Falls back to the PNG as it is when anything goes wrong."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - a core dependency
        return "image/png", png
    with _lock():
        try:
            pixmap = pymupdf.Pixmap(png)
            if pixmap.alpha:
                return "image/png", png
            if pixmap.n not in (1, 3):  # CMYK or a palette: JPEG wants grey or RGB
                pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
            longest = max(pixmap.width, pixmap.height)
            scale = min(1.0, MAX_SIDE_PX / longest) if longest else 1.0
            if scale < 1.0:
                width = max(1, round(pixmap.width * scale))
                height = max(1, round(pixmap.height * scale))
                pixmap = pymupdf.Pixmap(pixmap, width, height, None)
            png_size = round(len(png) * scale * scale)  # exact when not scaled
            jpeg = pixmap.tobytes("jpg", jpg_quality=JPEG_QUALITY)
            if figure_type in PHOTO_TYPES:
                use_jpeg = len(jpeg) < png_size
            else:
                use_jpeg = len(jpeg) <= JPEG_SHARE_FOR_LINE_ART * png_size
            if use_jpeg:
                return "image/jpeg", jpeg
            if scale == 1.0:
                return "image/png", png
            return "image/png", pixmap.tobytes("png")
        except Exception as exc:  # a bad image never fails the document: keep the crop
            log.warning("figure image kept as it is: %s", type(exc).__name__)
            return "image/png", png


def img_html(png: bytes | None, alt: str, attrs: str, figure_type: str | None) -> str | None:
    """`<img>` with the student-file image of a PNG crop, its alt text escaped and
    `attrs` (width and height) appended; None when there is no PNG crop."""
    if png is None or not png.startswith(_PNG_SIGNATURE):
        return None
    mime, data = student_image(png, figure_type)
    src = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    return f'<img src="{src}" alt="{escape_text(alt)}"{attrs}>'
