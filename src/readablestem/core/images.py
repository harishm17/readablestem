"""The pixel budget every image decode answers to.

A small file can hold a huge picture (a one-colour canvas of 20,000 x 20,000 pixels is a
few hundred bytes), and decoding it takes seconds and hundreds of megabytes whatever it
shows. So no code in the pipeline decodes an image before it knows how many pixels the
image has: width and height are read from the image's header or its PDF object, and an
image over `MAX_IMAGE_PIXELS` is not decoded at all.

- `MAX_IMAGE_PIXELS`: the most pixels any one decode may be asked for. Over it, a caller
  treats the image as not readable (no picture, "not plain", a figure left as it is).
- `MAX_UPLOAD_IMAGE_PIXELS`: the upload probe refuses a PDF holding an image over this
  (docs/security.md, "Pixel budget"). It is above the decode budget so that a real scan
  (a 600 dpi A3 page is 70 million pixels) is still taken; such a page is read from its
  rendered picture, never from the embedded image.
- `DECODE_BUDGET_S`: the seconds one job may spend decoding images in the layout step,
  summed; over it the job stops with the code `image_too_large`.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

MAX_IMAGE_PIXELS = 40_000_000
MAX_UPLOAD_IMAGE_PIXELS = 100_000_000
DECODE_BUDGET_S = 30.0


class ImageTooLarge(ValueError):
    """The image has more pixels than `MAX_IMAGE_PIXELS`; nothing was decoded."""


def within_budget(width: int, height: int) -> bool:
    """Whether an image of this size may be decoded (a missing or zero side may not)."""
    return width > 0 and height > 0 and width * height <= MAX_IMAGE_PIXELS


def open_within_budget(data: bytes) -> Image:
    """`PIL.Image.open(data)` that refuses an image over the budget before any pixel is
    decoded (opening reads the header only). The caller closes it and may then `load` or
    `convert` it. Raises `ImageTooLarge`, or PIL's own errors for data that is not an
    image."""
    from PIL import Image as pil

    image = pil.open(io.BytesIO(data))
    if not within_budget(*image.size):
        image.close()
        raise ImageTooLarge("image over the pixel budget")
    return image


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height in pixels of an image, from its header (no pixel is decoded)."""
    with open_within_budget(data) as image:
        return image.size
