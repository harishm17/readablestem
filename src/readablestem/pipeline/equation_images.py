"""Equations pasted as pictures: MathType and Word's "paste as picture", an equation editor
exported to PNG, a PowerPoint slide's formula saved as an image.

Layout finds raster images and, until this module, called every one a figure: the figure
step wrote alt text for it and nothing checked the formula. A picture of an equation has no
text layer, so there is nothing to cross-check a transcription with, and a description
("a fraction with 18 below T") is not what a student needs: the equation step reads it
like any other equation, and because nothing can confirm the reading it is always a decision
for the instructor (`equation_step.NO_TEXT_LAYER_CODE`).

`looks_like_equation` decides from the picture and where it sits, nothing else (no model):

- size: not larger than a few lines of text (`MAX_HEIGHT_BODY` x the body size, never
  taller than `MAX_HEIGHT_PT`), and wide enough to hold a symbol;
- ink: dark marks on a white ground, almost no colour (`MAX_COLOUR`), a few percent of the
  area inked (`MIN_INK`..`MAX_INK`): not a photograph, a painted diagram or a filled chart;
- shape: the marks are many and glyph-sized (`MIN_COMPONENTS` connected pieces), and no
  single piece frames the picture (a plot's axes or a box around a drawing span most of its
  width and height, a fraction bar or a bracket spans one of the two).

A picture with a caption ("Figure 2.1") is a figure whatever it looks like; layout checks
that before it asks here. Pictures that pass this test but are not equations (a small
diagram of dark lines on white) reach the equation step, which answers "not an equation"
and leaves the picture in the output, flagged: the cost of a wrong guess is one decision.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymupdf

# `Element.reasons` of an equation that is a picture.
REASON = "equation image"

# Size limits, in points and in body font sizes.
MIN_SIDE_PT = 5.0
MAX_HEIGHT_PT = 110.0
MAX_HEIGHT_BODY = 10.0
MAX_WIDTH_PT = 520.0
# The picture is looked at rendered at this resolution, shrunk to at most RENDER_SIDE px.
RENDER_DPI = 144
RENDER_SIDE = 400
DARK = 150  # a pixel darker than this (0-255 grey) is ink
WHITE = 235  # ... lighter than this is the page
# Ink and colour limits.
MAX_COLOUR = 0.04  # share of pixels with a visible hue
MIN_INK = 0.004
MAX_INK = 0.25
MIN_WHITE = 0.6  # share of near-white pixels
# Shape limits.
MIN_COMPONENTS = 1
# A picture on a line of its own has at least this many pieces: an equation has several
# symbols, a drawing of two lines and a letter or two does not (a lone symbol is kept only in
# a line of text).
MIN_DISPLAY_COMPONENTS = 3
FRAME_WIDTH = 0.7  # one piece this wide AND
FRAME_HEIGHT = 0.6  # this tall frames the picture: a plot, a box, a drawing
INLINE_MAX_BODY = 3.5  # a picture within a line of text is at most this tall (body sizes)


@dataclass(frozen=True)
class Profile:
    """What the pixels of a picture say, normalised to its size."""

    colour: float  # share of pixels with a visible hue
    ink: float  # share of dark pixels
    white: float  # share of near-white pixels
    components: int  # connected pieces of ink
    frame: bool  # a single piece spans most of both sides


def looks_like_equation(profile: Profile) -> bool:
    return (
        profile.colour <= MAX_COLOUR
        and MIN_INK <= profile.ink <= MAX_INK
        and profile.white >= MIN_WHITE
        and profile.components >= MIN_COMPONENTS
        and not profile.frame
    )


def size_fits(width_pt: float, height_pt: float, body_pt: float) -> bool:
    """Is a picture this big (points) the size of an equation set in text of `body_pt`?"""
    return (
        width_pt >= MIN_SIDE_PT
        and height_pt >= MIN_SIDE_PT
        and width_pt <= MAX_WIDTH_PT
        and height_pt <= min(MAX_HEIGHT_PT, MAX_HEIGHT_BODY * max(body_pt, 1.0))
    )


def profile_of(page: pymupdf.Page, box: tuple[float, float, float, float]) -> Profile | None:
    """The `Profile` of the picture drawn in `box` (page coordinates), or None when it
    cannot be rendered. The page is rendered, not the image read, so a mask, a rotation
    and a transparent ground are what a reader sees."""
    clip = pymupdf.Rect(box) & page.rect
    if clip.is_empty or clip.width < 1 or clip.height < 1:
        return None
    zoom = min(RENDER_DPI / 72.0, RENDER_SIDE / max(clip.width, clip.height))
    try:
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), clip=clip, colorspace=pymupdf.csRGB, alpha=False
        )
    except (RuntimeError, ValueError):
        return None
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    rgb = rgb.astype(np.int16)
    grey = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    ink = grey < DARK
    area = float(grey.size)
    components, frame = _pieces(ink)
    return Profile(
        colour=float(((chroma > 40) & (grey < 245)).sum()) / area,
        ink=float(ink.sum()) / area,
        white=float((grey >= WHITE).sum()) / area,
        components=components,
        frame=frame,
    )


def _pieces(ink: np.ndarray) -> tuple[int, bool]:
    """(number of connected pieces of ink, whether one spans FRAME_WIDTH x FRAME_HEIGHT of
    the picture). Pieces touching diagonally are one (8-connected); a flood fill on the
    rendered picture, which is at most RENDER_SIDE px on its long side."""
    height, width = ink.shape
    seen = np.zeros_like(ink, dtype=bool)
    pieces = 0
    framed = False
    ys, xs = np.nonzero(ink)
    for y0, x0 in zip(ys.tolist(), xs.tolist(), strict=True):
        if seen[y0, x0]:
            continue
        pieces += 1
        stack = [(y0, x0)]
        seen[y0, x0] = True
        top, bottom, left, right = y0, y0, x0, x0
        while stack:
            y, x = stack.pop()
            top, bottom, left, right = min(top, y), max(bottom, y), min(left, x), max(right, x)
            for dy in (-1, 0, 1):
                ny = y + dy
                if not 0 <= ny < height:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < width and ink[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if (right - left + 1) >= FRAME_WIDTH * width and (
            bottom - top + 1
        ) >= FRAME_HEIGHT * height:
            framed = True
    return pieces, framed
