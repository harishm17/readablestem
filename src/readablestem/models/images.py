"""Shrinking a picture before it is sent to a model.

A Claude image costs about width x height / 750 input tokens, so a large figure crop costs
far more than the model needs to read it. `shrink_png` scales a PNG down so its long side is at most
`max_edge` pixels (never up), keeping the aspect ratio; the caller keeps the original for
everything a person sees (the review screen, the HTML).
"""

from __future__ import annotations

import io

from PIL import Image

from readablestem.models.pricing import png_size


def shrink_png(png: bytes, max_edge: int | None) -> bytes:
    """`png` scaled so its long side is at most `max_edge` pixels; the same bytes when it
    already is, when `max_edge` is None, or when the data cannot be read as an image."""
    if max_edge is None:
        return png
    size = png_size(png)
    if size is None or max(size) <= max_edge:
        return png
    try:
        with Image.open(io.BytesIO(png)) as image:
            image.load()
            scale = max_edge / max(image.size)
            target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            # LANCZOS keeps thin lines and small labels legible when a figure is reduced.
            resized = image.resize(target, Image.Resampling.LANCZOS)
            out = io.BytesIO()
            resized.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return png
