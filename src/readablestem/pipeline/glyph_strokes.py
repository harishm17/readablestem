"""Drawing-font glyphs: strokes of the page, not text.

LaTeX's picture environment sets a slanted line, an arrowhead or an arc as a glyph of a
drawing font (`\\line(5,3){60}` is a row of glyphs of line10, each one a short piece of the
line), and xy-pic does the same with its own fonts. The text layer reads those glyphs as
characters: a triangle's slanted side came out as a paragraph of 17 quote marks, and no
figure existed to hold the drawing (F-550).

`layout._text_blocks` takes the glyphs of these fonts out of the text blocks it returns (one
place, so rows, the text pass's glyphs and the line reader all see text without them) and
leaves them in one block of type `GLYPHS`. `layout._read_page`, where the page's drawings are
gathered, turns that block into entries of the page's drawing list (`strokes`), so the figure
clustering, the page type and the ink analysis see a drawing.

A glyph's geometry is its outline, as MuPDF draws it (the page's SVG with text as paths). A
thin straight outline (a line's glyph) is the segment it draws; any other outline (an
arrowhead, an arc) is a filled path of its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import cache
from typing import Any

import pymupdf

GLYPHS = 2  # the `type` of the block that holds the taken glyphs (0 is text, 1 an image)

# The fonts TeX draws with. LaTeX's picture environment: line10 and linew10 (lines and
# arrowheads, thin and thick), lcircle10 and lcirclew10 (circles and arcs), the "latex-fonts"
# TFMs of a TeX Live install. xy-pic's own fonts, the "xypic" TFMs: xyatip and xybtip
# (arrowheads), xycmat and xycmbt (Computer Modern tips), xyeuat, xyeubt, xyluat and xylubt
# (Euler and Lucida tips), xybsql (a bent square), xycirc, xydash, xyline, xymisc and xyqc
# (circles, dashes, lines, quarter circles). Matched on the whole name after the subset
# prefix ("IIMURR+LINE10"), so a font that only contains "line" or "circle" is text.
_DRAWING_FONT = re.compile(
    r"(?:[A-Z]{6}\+)?(?:linew?10|lcirclew?10"
    r"|xy(?:atip|btip|bsql|circ|cmat|cmbt|dash|euat|eubt|line|luat|lubt|misc|qc)\d+)",
    re.IGNORECASE,
)


@cache
def is_drawing_font(name: str) -> bool:
    return _DRAWING_FONT.fullmatch(name) is not None


def take(blocks: list[dict]) -> None:
    """Move the glyphs of drawing fonts out of the text `blocks` (in place) into one block of
    type `GLYPHS` at the end: each glyph's origin and colour. A line or block left with no
    text goes; the boxes of those that stay are cut to what is left."""
    taken: list[dict] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        lines = []
        for line in block["lines"]:
            spans = [s for s in line["spans"] if not is_drawing_font(s["font"])]
            if len(spans) != len(line["spans"]):
                for span in line["spans"]:
                    if is_drawing_font(span["font"]):
                        colour = int(span.get("color") or 0)
                        taken += [{"origin": ch["origin"], "color": colour} for ch in span["chars"]]
                line["spans"] = spans
                if spans:
                    line["bbox"] = _union(s["bbox"] for s in spans)
            if spans:
                lines.append(line)
        if len(lines) != len(block["lines"]):
            block["lines"] = lines
            if lines:
                block["bbox"] = _union(line["bbox"] for line in lines)
    if taken:
        blocks[:] = [b for b in blocks if b.get("type") != 0 or b["lines"]]
        blocks.append({"type": GLYPHS, "glyphs": taken})


def _union(boxes: Iterable[Iterable[float]]) -> tuple[float, float, float, float]:
    rows = [tuple(b) for b in boxes]
    return (
        min(b[0] for b in rows),
        min(b[1] for b in rows),
        max(b[2] for b in rows),
        max(b[3] for b in rows),
    )


Point = tuple[float, float]
Item = tuple  # ("l", p, q) or ("c", p, c1, c2, q): what `get_cdrawings` calls an item

_USE = re.compile(r'<use [^>]*?xlink:href="#(\w+)" transform="matrix\(([^)]*)\)"')
_PATH = re.compile(r'<path id="(\w+)" d="([^"]*)"')
_TOKEN = re.compile(r"([MLHVCZ])|(-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6}
_AT = 0.05  # pt: how far a glyph's origin may be from the place the SVG draws it
THIN = 0.12  # a straight outline at most this wide, as a share of its length, is a segment


def strokes(page: pymupdf.Page, blocks: list[dict]) -> list[dict[str, Any]]:
    """The glyphs taken out of the page's text (`take`) as entries of `page.get_cdrawings()`
    (the page unrotated, the drawings' frame). A glyph the page's SVG has no outline for is
    left out."""
    glyphs = [g for b in blocks if b.get("type") == GLYPHS for g in b["glyphs"]]
    if not glyphs:
        return []
    svg = page.get_svg_image(text_as_path=True)  # the page in display space
    outlines = {m[1]: _outline(m[2]) for m in _PATH.finditer(svg)}
    placed: dict[tuple[int, int], list[tuple[str, tuple[float, ...]]]] = {}
    for m in _USE.finditer(svg):
        matrix = tuple(float(v) for v in m[2].split(","))
        if len(matrix) == 6 and outlines.get(m[1]):
            placed.setdefault((round(matrix[4]), round(matrix[5])), []).append((m[1], matrix))
    turn, back = page.rotation_matrix, page.derotation_matrix
    out: list[dict[str, Any]] = []
    for glyph in glyphs:
        x, y = glyph["origin"]
        ox, oy = x * turn.a + y * turn.c + turn.e, x * turn.b + y * turn.d + turn.f
        found = next(
            (
                (name, m)
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                for name, m in placed.get((round(ox) + dx, round(oy) + dy), ())
                if abs(m[4] - ox) <= _AT and abs(m[5] - oy) <= _AT
            ),
            None,
        )
        if found is None:
            continue
        name, matrix = found
        items = [
            (item[0], *(_place(matrix, back, p) for p in item[1:])) for item in outlines[name] or ()
        ]
        out.append(_drawing(items, _rgb(glyph["color"])))
    return out


def _place(matrix: tuple[float, ...], back: pymupdf.Matrix, point: Point) -> Point:
    """A glyph-space point on the page: the glyph's matrix, then back to the unrotated frame."""
    a, b, c, d, e, f = matrix
    x, y = a * point[0] + c * point[1] + e, b * point[0] + d * point[1] + f
    return x * back.a + y * back.c + back.e, x * back.b + y * back.d + back.f


def _outline(d: str) -> list[Item] | None:
    """The items of an SVG path of M, L, H, V, C and Z (all MuPDF writes for a glyph), every
    subpath closed. None for anything else."""
    items: list[Item] = []
    command, numbers = "", []
    x = y = 0.0
    start: Point | None = None

    def close() -> None:
        if start is not None and (x, y) != start:
            items.append(("l", (x, y), start))

    for m in _TOKEN.finditer(d):
        if m[1]:
            if m[1] == "Z":
                command = ""
            elif m[1] == "M":
                close()
            command, numbers = m[1], []
            continue
        if not command:
            return None
        numbers.append(float(m[2]))
        if len(numbers) < _ARITY[command]:
            continue
        here = (x, y)
        if command == "M":
            x, y = numbers
            start = (x, y)
            command = "L"  # more pairs after a move are lines
        elif command == "L":
            x, y = numbers
            items.append(("l", here, (x, y)))
        elif command == "H":
            x = numbers[0]
            items.append(("l", here, (x, y)))
        elif command == "V":
            y = numbers[0]
            items.append(("l", here, (x, y)))
        else:
            n = numbers
            x, y = n[4], n[5]
            items.append(("c", here, (n[0], n[1]), (n[2], n[3]), (x, y)))
        numbers = []
    close()
    return items


def _rgb(colour: int) -> tuple[float, float, float]:
    return ((colour >> 16) & 255) / 255, ((colour >> 8) & 255) / 255, (colour & 255) / 255


def _drawing(items: list[Item], rgb: tuple[float, float, float]) -> dict[str, Any]:
    """One `get_cdrawings` entry for a glyph's outline: the segment it draws when the outline
    is thin and straight, else the outline, filled."""
    points = [p for item in items for p in item[1:]]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    rect = (min(xs), min(ys), max(xs), max(ys))
    entry: dict[str, Any] = {
        "items": items,
        "type": "f",
        "even_odd": True,
        "fill_opacity": 1.0,
        "fill": rgb,
        "closePath": True,
        "rect": rect,
        "layer": "",
    }
    segment = _segment(points)
    if segment is not None:
        (p, q), width = segment
        entry |= {
            "items": [("l", p, q)],
            "type": "s",
            "color": rgb,
            "width": width,
            "stroke_opacity": 1.0,
            "lineCap": (0, 0, 0),
            "lineJoin": 0.0,
            "dashes": "[] 0",
            "closePath": False,
            "rect": (min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])),
        }
        del entry["fill"], entry["fill_opacity"], entry["even_odd"]
    return entry


def _segment(points: list[Point]) -> tuple[tuple[Point, Point], float] | None:
    """The centre line and thickness of an outline that is a thin straight sliver (the
    glyph of a line), else None. The axis runs between the two points farthest apart; the
    outline is thin when no point lies off it by more than `THIN` of its length."""
    if len(points) > 64:
        return None
    p, q = max(((p, q) for p in points for q in points), key=lambda pq: _gap(*pq))
    length = _gap(p, q)
    if length < 1.0:
        return None
    ux, uy = (q[0] - p[0]) / length, (q[1] - p[1]) / length
    along = [((r[0] - p[0]) * ux + (r[1] - p[1]) * uy) for r in points]
    across = [(-(r[0] - p[0]) * uy + (r[1] - p[1]) * ux) for r in points]
    thickness = max(across) - min(across)
    if thickness > THIN * length:
        return None
    mid = (max(across) + min(across)) / 2
    ends = [(p[0] + t * ux - mid * uy, p[1] + t * uy + mid * ux) for t in (min(along), max(along))]
    return (ends[0], ends[1]), max(thickness, 0.1)


def _gap(p: Point, q: Point) -> float:
    return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5
