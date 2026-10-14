"""The size of an equation kept as a picture in the student file (T3-15).

An equation the tool could not convert stays in the file as the crop of the PDF. Shown at
its printed size it is as big as the PDF's text, which is not the size of the file's text:
a 10 pt handout shows its text at 13 CSS px and the file's text is 18 px, so the picture
looks small beside it; a slide deck's 24 pt text would make the picture huge. So the crop is
shown at the size its text would have in the file: scaled by

    scale = (the file's text size) / (the PDF's text size around the equation)

The PDF's text size is the paragraph an inline equation sits in (`Element.parent_id`), the
equation's own size for a display equation, else the document's body size (`body_size`: the
size most of the paragraph and list text is set in). The scale is kept within `MIN_SCALE`
and `MAX_SCALE`, and is 1 when the document has no text to measure by.

The crop is also rendered for that size at `SHARP` image pixels per CSS pixel
(`crop_dpi`), and carries its resolution in its PNG header (`png_dpi`), so a student on a
high-density screen, or zooming in, sees sharp lines. The assembler displays it at
`width = pixels / SHARP` without knowing anything but the file (`display_size`), and a crop
rendered the old way (no resolution in the header) is shown at its printed size times the
scale.

Pure functions; the pipeline (when it cuts the crop) and the assembler (when it writes
the `<img>`) call the same `scaler`, so they agree.
"""

from __future__ import annotations

import struct
from collections import Counter
from collections.abc import Callable, Iterable

from a11y_stem.core.types import Element, Kind

CSS_PX_PER_PT = 96 / 72  # a PDF point is 1/72 in; a CSS pixel is 1/96 in
OUTPUT_TEXT_PX = 18.0  # the file's text: `assemble.STYLE` sets the body at 1.125rem
SHARP = 3  # image pixels per CSS pixel of the displayed picture
MIN_SCALE = 0.5
MAX_SCALE = 2.0
# A display equation's own size is trusted when it is near the body text's: a drawn
# fraction or an integral sign alone can be far from it.
OWN_SIZE_RANGE = (0.6, 1.8)
_UNTAGGED_DPI = 96  # what MuPDF writes in a PNG header it was not told the resolution of
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def body_size(elements: Iterable[Element]) -> float | None:
    """The font size most of the document's paragraph and list text is set in (points,
    to a tenth), counted by characters; None when no such text has a size."""
    counts: Counter[float] = Counter()
    for element in elements:
        if (
            element.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM)
            and element.font_size
            and element.font_size > 0
            and not element.preformatted
        ):
            counts[round(element.font_size, 1)] += len(element.text or "")
    return counts.most_common(1)[0][0] if counts else None


def scaler(elements: Iterable[Element]) -> Callable[[Element], float]:
    """A function from an equation to the scale its picture is shown at."""
    listed = list(elements)
    body = body_size(listed)
    sizes = {e.id: e.font_size for e in listed if e.font_size and e.font_size > 0}

    def scale(element: Element) -> float:
        if body is None:
            return 1.0
        size: float | None = None
        if element.parent_id is not None:
            size = sizes.get(element.parent_id)
        if size is None and element.font_size and element.font_size > 0:
            low, high = OWN_SIZE_RANGE
            if low * body <= element.font_size <= high * body:
                size = element.font_size
        size = size or body
        wanted = OUTPUT_TEXT_PX / (size * CSS_PX_PER_PT)
        return min(MAX_SCALE, max(MIN_SCALE, wanted))

    return scale


def crop_dpi(scale: float) -> int:
    """The resolution to cut the crop at for a picture shown at `scale` of its printed
    size: `SHARP` pixels for each CSS pixel it will take."""
    return round(72 * CSS_PX_PER_PT * scale * SHARP)


# ------------------------------------------------------------------------- PNG header


def png_size(png: bytes) -> tuple[int, int] | None:
    """(width, height) in pixels from the IHDR chunk; None when it is not a PNG."""
    if not png.startswith(_PNG_SIGNATURE) or png[12:16] != b"IHDR" or len(png) < 24:
        return None
    width, height = struct.unpack(">II", png[16:24])
    return (width, height) if width > 0 and height > 0 else None


def png_dpi(png: bytes) -> float | None:
    """The resolution a crop was cut at, from its pHYs chunk; None when there is none or
    it is MuPDF's default (a crop cut the old way)."""
    position = len(_PNG_SIGNATURE)
    while position + 8 <= len(png):
        length, kind = struct.unpack(">I4s", png[position : position + 8])
        body = png[position + 8 : position + 8 + length]
        if kind == b"pHYs" and length == 9 and body[8] == 1:
            per_metre = struct.unpack(">I", body[:4])[0]
            dpi = per_metre * 0.0254
            return None if abs(dpi - _UNTAGGED_DPI) < 1 or dpi <= 0 else dpi
        if kind in (b"IDAT", b"IEND"):
            return None  # pHYs comes before the image data
        position += 12 + length
    return None


def display_size(
    png: bytes, scale: float, printed: tuple[int, int] | None
) -> tuple[int, int] | None:
    """(width, height) in CSS px to show the crop at: its pixels at the resolution it was
    cut at, times `scale`. A crop with no resolution in its header (cut the old way) is
    shown at `printed`, its element's printed size in CSS px, times `scale`. None when
    neither is known or the size is not sensible."""
    size = png_size(png)
    dpi = png_dpi(png)
    if size is not None and dpi is not None:
        factor = scale * 96 / dpi
        width, height = round(size[0] * factor), round(size[1] * factor)
    elif printed is not None:
        width, height = round(printed[0] * scale), round(printed[1] * scale)
    else:
        return None
    return (width, height) if 0 < width <= 4000 and 0 < height <= 4000 else None
