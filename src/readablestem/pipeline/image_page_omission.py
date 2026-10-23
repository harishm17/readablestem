"""Ink on a page that no block of the page reader accounts for (ADR 0007, "Omission check").

The image page pass reads a page in one call and trusts the blocks that come back. A reader
that stops early (one line of a page of display equations came back, the other four did not)
leaves nothing behind that says so: the page counts as converted and its one paragraph is the
only item. A page picture has no text layer to compare the answer with, but it has ink, and
the blocks have boxes. This compares the two: ink outside every block's box is content the
reading left out.

The reader's boxes are not exact. On handwriting they sit tens of pixels off the ink and
sometimes most of a line away, so a box is grown by `pad_x` and `pad_y` (thousandths of the
page) before the comparison. That makes the check blind to one dropped line that sits next to a
block and sensitive to a dropped stretch of lines or paragraphs: the price of not trusting the
boxes. What is flagged is a region of ink, above a size, that is not near any block. Rules,
frames drawn round text, a scanner's dark bar, grain, and the strips where running headers,
footers and page numbers sit (the prompt tells the reader to leave those out) are not content.

Works on a coarse grid of the ink mask (`image_page.Ink.mask`) with numpy only: no model, no new
dependency, a few milliseconds a page.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Params:
    pad_x: int = 30  # thousandths of the page width a block's box is grown by, each side
    pad_y: int = 60  # thousandths of the page height, each side
    cell_div: int = 200  # a grid cell is the page width over this many pixels
    cell_min_px: int = 3  # ink pixels that make a cell ink
    edge: float = 0.015  # the outer strip of the page (page width) is not content
    faint: float = 0.2  # ... and at least this share of what the page's strong cells hold
    join_x: float = 0.03  # unaccounted ink this far apart (page width) is one piece
    join_y: float = 0.025  # (page height)
    merge_y: float = 0.12  # pieces this close (page height) are reported as one region
    min_cells: int = 20  # ink cells a piece needs
    min_share: float = 0.02  # share of the page's ink a piece needs
    min_rows: int = 5  # grid rows tall (a line of writing; a rule is thinner)
    min_fill: float = 0.08  # ink cells over a piece's box (a frame, or specks, are sparse)
    band: float = 0.06  # top and bottom strips (page height) where a line is a header or footer
    band_rows: int = 12  # ... if the piece is no taller than this
    max_regions: int = 3


PARAMS = Params()


@dataclass(frozen=True)
class Region:
    """A stretch of unaccounted ink, in thousandths of the page (x0, y0, x1, y1)."""

    x0: int
    y0: int
    x1: int
    y1: int
    cells: int
    share: float  # of the page's ink


@dataclass
class _Piece:
    x0: int
    y0: int
    x1: int
    y1: int  # grid cells, y1 and x1 exclusive
    cells: int
    ink: int


def _label(grid: np.ndarray) -> list[list[tuple[int, int]]]:
    """Connected components (8-neighbour) of the true cells."""
    height, width = grid.shape
    seen = np.zeros_like(grid, dtype=bool)
    parts: list[list[tuple[int, int]]] = []
    for y, x in zip(*np.nonzero(grid), strict=True):
        if seen[y, x]:
            continue
        seen[y, x] = True
        stack = [(int(y), int(x))]
        cells: list[tuple[int, int]] = []
        while stack:
            cy, cx = stack.pop()
            cells.append((cy, cx))
            for ny in range(max(0, cy - 1), min(height, cy + 2)):
                for nx in range(max(0, cx - 1), min(width, cx + 2)):
                    if grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        parts.append(cells)
    return parts


def _dilate(grid: np.ndarray, ry: int, rx: int) -> np.ndarray:
    padded = np.pad(grid, ((ry, ry), (rx, rx)))
    out = np.zeros_like(grid)
    height, width = grid.shape
    for dy in range(2 * ry + 1):
        for dx in range(2 * rx + 1):
            out |= padded[dy : dy + height, dx : dx + width]
    return out


def _furniture(grid: np.ndarray) -> np.ndarray:
    """Ink that is not content: a frame or box drawn round text (a long outline, mostly empty
    inside) and a scanner's dark bar or border (long, thin and solid)."""
    height, width = grid.shape
    out = np.zeros_like(grid)
    for cells in _label(grid):
        ys = [y for y, _ in cells]
        xs = [x for _, x in cells]
        box_h, box_w = max(ys) - min(ys) + 1, max(xs) - min(xs) + 1
        fill = len(cells) / (box_h * box_w)
        frame = box_h >= 0.25 * height and box_w >= 0.25 * width and fill <= 0.10
        long_ = box_h >= 0.4 * height or box_w >= 0.4 * width
        bar = long_ and min(box_h, box_w) <= 0.05 * min(height, width) and fill >= 0.5
        if frame or bar:
            for y, x in cells:
                out[y, x] = True
    return out


def _merged(pieces: list[_Piece], params: Params, rows: int) -> list[_Piece]:
    """Pieces closer than `merge_y` as one (a stretch of lines is one thing to look at)."""
    gap = params.merge_y * rows
    out: list[_Piece] = []
    for piece in sorted(pieces, key=lambda p: p.y0):
        if out and piece.y0 - out[-1].y1 <= gap:
            last = out[-1]
            out[-1] = _Piece(
                min(last.x0, piece.x0),
                last.y0,
                max(last.x1, piece.x1),
                max(last.y1, piece.y1),
                last.cells + piece.cells,
                last.ink + piece.ink,
            )
        else:
            out.append(piece)
    return out


def find(
    mask: np.ndarray, boxes: Sequence[tuple[int, int, int, int]], params: Params = PARAMS
) -> list[Region]:
    """Regions of ink in `mask` (True = ink) that none of `boxes` (x0, y0, x1, y1 in thousandths
    of the page: the blocks') comes near. At most `params.max_regions`, the biggest, in reading
    order. No boxes, or a page too small to judge, give none."""
    page_h, page_w = mask.shape
    cell = max(3, round(page_w / params.cell_div))
    rows, cols = page_h // cell, page_w // cell
    if rows < 8 or cols < 8 or not boxes:
        return []
    counts = mask[: rows * cell, : cols * cell].reshape(rows, cell, cols, cell).sum(axis=(1, 3))
    ink = counts >= params.cell_min_px
    if ink.any():  # grain and paper texture make faint cells; writing makes strong ones
        ink &= counts >= params.faint * float(np.percentile(counts[ink], 90))
    edge = max(2, round(params.edge * cols))  # a scan's border and the dark rim of a photo
    ink[:edge, :] = ink[-edge:, :] = ink[:, :edge] = ink[:, -edge:] = False
    ink &= ~_furniture(ink)
    total = int(counts[ink].sum())
    if total == 0:
        return []
    accounted = np.zeros_like(ink)
    for x0, y0, x1, y1 in boxes:
        left = max(0, int((x0 - params.pad_x) / 1000 * cols))
        right = min(cols, int(np.ceil((x1 + params.pad_x) / 1000 * cols)))
        top = max(0, int((y0 - params.pad_y) / 1000 * rows))
        bottom = min(rows, int(np.ceil((y1 + params.pad_y) / 1000 * rows)))
        accounted[top:bottom, left:right] = True
    left_over = ink & ~accounted
    if not left_over.any():
        return []
    joined = _dilate(
        left_over, max(1, round(params.join_y * rows)), max(1, round(params.join_x * cols))
    )
    pieces: list[_Piece] = []
    for part in _label(joined):
        mine = [(y, x) for y, x in part if left_over[y, x]]
        if len(mine) < params.min_cells:
            continue
        ys = [y for y, _ in mine]
        xs = [x for _, x in mine]
        ink_px = sum(int(counts[y, x]) for y, x in mine)
        piece = _Piece(min(xs), min(ys), max(xs) + 1, max(ys) + 1, len(mine), ink_px)
        if piece.ink < params.min_share * total or piece.y1 - piece.y0 < params.min_rows:
            continue  # too little, or a rule or a speck: writing is taller
        if piece.cells < params.min_fill * (piece.y1 - piece.y0) * (piece.x1 - piece.x0):
            continue  # a frame's outline, scattered specks: not writing
        in_band = piece.y1 <= params.band * rows or piece.y0 >= (1 - params.band) * rows
        if in_band and piece.y1 - piece.y0 <= params.band_rows:
            continue  # a running header, a footer or a page number
        pieces.append(piece)
    found = [
        Region(
            x0=max(0, round((r.x0 - 1) / cols * 1000)),
            y0=max(0, round((r.y0 - 1) / rows * 1000)),
            x1=min(1000, round((r.x1 + 1) / cols * 1000)),
            y1=min(1000, round((r.y1 + 1) / rows * 1000)),
            cells=r.cells,
            share=round(r.ink / total, 4),
        )
        for r in _merged(pieces, params, rows)
    ]
    found.sort(key=lambda r: -r.cells)
    return sorted(found[: params.max_regions], key=lambda r: (r.y0, r.x0))
