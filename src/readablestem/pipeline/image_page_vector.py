"""What the pen strokes of a handwritten vector page add to the image page pass (ADR 0007).

A notes app exports handwriting as vector paths, and `ink_strokes` reads them back as strokes.
Measured on the vector dev set (docs/eval-handwriting.md), a clean re-render of the ink does not
make the reader read better than a scan. What strokes give is things to check, and a clean
picture for the person comparing. Four uses, on pages `page_type` calls handwritten and
`ink_strokes` analysed (every other page goes through the pass as before):

- Sharp crops: the picture of an expression (sent to the model, shown on the review screen) is
  drawn from the strokes alone, black on white at `CROP_DPI`, cut to the ink: no paper, ruling,
  template colour or pastel pen, and no page-image compression.
- Case check: a letter whose capital and small form differ in height alone (c, o, s, u, v, w, x,
  z; k, p and y are left out: k is as tall as K, and a tail hides the height of p and y) is
  measured against the heights of the line's own other letters. A reading that says the other
  case adds a reason.
- Script check: each symbol of the reading is lined up with the strokes in writing order, and a
  small raised or lowered group of strokes the reading has as a plain symbol (or not at all)
  adds "script lost"; a script in the reading that the strokes write on the baseline adds
  "script added"; one raised in the reading and lowered in the ink (or the reverse), "script
  moved".
- Leftover ink: a group of strokes no block of the reading accounts for is a stretch of
  writing the reader left out, and becomes the same figure with a required decision that
  `image_page_omission` makes on a raster page. It works from the strokes, so pale ink counts.

Every check only adds a reason or a note. Handwritten maths stays a required decision whatever
these find, and a check that cannot judge (a fraction, a root, a sum, glyphs that touch so the
strokes cannot be told apart, nothing to measure against) says nothing. No model, no network;
numpy, Pillow and the standard library on top of `ink_strokes`.
"""

from __future__ import annotations

import io
import logging
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from readablestem.core.types import (
    IMAGE_PAGE_OMISSION_REASON,
    IMAGE_PAGE_REASON,
    BBox,
    Check,
    Element,
    Fix,
    Kind,
)
from readablestem.core.types import (
    IMAGE_PAGE_ORIGINAL_REASON as ORIGINAL_REASON,
)
from readablestem.pipeline import image_page_omission as omission
from readablestem.pipeline import page_type
from readablestem.pipeline.ink_strokes import Stroke

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- limits

CROP_DPI = 300  # a vector crop is drawn at this resolution (a scan crop is cut at 200)
CROP_MAX_SIDE_PX = 2048  # long side, as `layout.crop`
CROP_MARGIN_PT = 6.0  # paper round the ink of a crop
CROP_MIN_PEN_PX = 2.0  # thinnest line drawn: a hairline pen must still show
SUPERSAMPLE = 2
MIN_STROKES = 3  # a page with fewer strokes is not handwriting we can measure
MAX_GLYPH_PAIRS = 2_000_000  # candidate stroke pairs the grouping may look at on one page
MAX_ATOMS = 120  # a reading of more symbols is not checked
MAX_GLYPHS = 160

# Thresholds, set on the vector dev set (docs/eval-handwriting.md, "Checks from strokes").
# Heights are in units of the line's own letters, so ink of any size gets the same test.
PARAMS_VERSION = 1
GLUE_X = 0.4  # strokes whose x-ranges overlap this much of the narrower one are one glyph ...
GLUE_Y = 0.7  # ... if no further apart vertically than this many median stroke sizes
CAP_OVER_X = 1.55  # capital height over x-height when the line gives only one of them
CASE_UP_AT = 0.62  # a reading of small letter whose ink is this far from x-height toward capital
CASE_LOW_AT = 0.38  # a reading of capital whose ink is this far from x-height toward capital
MARK_SIZE = 0.3  # a glyph this small beside the line's letters is a dot or a comma
SCRIPT_SIZE = 0.85  # a script is no taller than this share of the ordinary letters ...
RAISED_AT = 0.45  # ... and its bottom is this far (in letter heights) above the baseline ...
ON_BASE = 0.2  # a glyph whose bottom is this near the baseline is on it
LOWERED_AT = 0.3  # ... or this far below it
BAR_RAISED_AT = 0.7  # a flat glyph (a minus) is a script only this far up
MAX_SLOPE = 0.25  # steepest baseline of a line of writing
MAX_SPREAD = 0.2  # scatter of the glyphs about the baseline (letter heights) past which it fails
GAP_COST = 1.5
SUB_COST = 1.0

# Leftover ink.
COVER_PAD = 1.2  # a glyph this many glyph heights from every block's box is not covered
JOIN_GAP_X = 3.0  # leftover glyphs this many glyph heights apart sideways are one stretch
JOIN_GAP_Y = 1.5
MIN_LEFTOVER_GLYPHS = 3
MERGE_Y = 0.12  # stretches this close (page height) are reported as one
BAND = 0.05  # top and bottom strips of the page where a short line is a header or footer
REGION_MARGIN = 0.012  # of the page, round a stretch's strokes
MAX_REGIONS = omission.PARAMS.max_regions

ASCENDERS = "bdfhl"
X_HEIGHT = "aemnr"  # small letters that are neither tall nor descending nor case-ambiguous
CAPITALS = "ABDEFGHIJLMNQRT"  # capitals no small letter is written like
DESCENDERS = "gjpqy"
# Letters whose small and capital forms differ in height alone. Left out: k (its small form is
# as tall as the capital) and p and y (the tail hides the height: measured from the baseline,
# they were right about as often as chance, docs/eval-handwriting.md).
CASE_LETTERS = "cosuvwxz"

# The names of the checks (messages in `web/messages.py`).
CASE_CHECK = "image_page.case_check"
SCRIPT_CHECK = "image_page.script_check"
CASE_MISMATCH = "case_mismatch"
SCRIPT_LOST = "script_lost"
SCRIPT_ADDED = "script_added"
SCRIPT_MOVED = "script_moved"
OMITTED_STROKES_NOTE = "omitted_region"  # the existing omission decision


# --------------------------------------------------------------------------- the page


@dataclass(frozen=True)
class VectorPage:
    """The pen strokes of one handwritten page, in page points (y down)."""

    page: int
    strokes: tuple[Stroke, ...]
    frame: BBox  # the page's box in points

    def strokes_in(self, box: BBox, pad: float) -> list[Stroke]:
        """The strokes whose centre is in `box` grown by `pad` points."""
        x0, y0, x1, y1 = box
        out = []
        for s in self.strokes:
            bx0, by0, bx1, by1 = s.bbox
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            if x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad:
                out.append(s)
        return out

    def crop(self, box: BBox, *, dpi: int = CROP_DPI, tag_dpi: bool = False) -> bytes | None:
        """The strokes of the block whose box is `box`, drawn clean (`render`). The reader's box
        is loose, so the picture is cut to the ink the box holds: its centre in the box grown
        by a fifth of its height. None when there is no ink there (the ordinary crop stands)."""
        height = box[3] - box[1]
        found = self.strokes_in(box, pad=4.0 + 0.2 * height)
        if not found:
            return None
        try:
            return render(found, dpi=dpi, tag_dpi=tag_dpi)
        except Exception as exc:  # a picture is never worth failing a page for
            log.warning("vector crop failed: %s", type(exc).__name__)
            return None

    def cut(
        self, region: tuple[int, int, int, int], picture: tuple[int, int], *, dpi: int = CROP_DPI
    ) -> bytes | None:
        """The ink in `region`, pixels of a page picture `picture` (width, height) wide: what
        `image_page_check.cut` makes from the picture, drawn from the strokes instead."""
        fx0, fy0, fx1, fy1 = self.frame
        sx, sy = (fx1 - fx0) / max(picture[0], 1), (fy1 - fy0) / max(picture[1], 1)
        box = (
            fx0 + region[0] * sx,
            fy0 + region[1] * sy,
            fx0 + region[2] * sx,
            fy0 + region[3] * sy,
        )
        found = self.strokes_in(box, pad=0.0)
        if not found:
            return None
        try:
            return render(found, dpi=dpi)
        except Exception as exc:
            log.warning("vector crop failed: %s", type(exc).__name__)
            return None


def analyse(
    pdf_bytes: bytes,
    pages: Iterable[int],
    kinds: Mapping[int, str],
    *,
    budget: page_type.InkBudget | None = None,
) -> dict[int, VectorPage]:
    """The strokes of each of `pages` (0-based) whose `page_type` kind (`kinds`) is handwritten
    and that `ink_strokes` analysed within `budget` (the document's ink budget; a fresh one
    when not given). Any other page, and one that fails, is left out: it is read from its
    picture as before."""
    from readablestem.pipeline import layout

    budget = budget or page_type.InkBudget()
    found: dict[int, VectorPage] = {}
    for number in sorted(set(pages)):
        if kinds.get(number) not in page_type.HANDWRITTEN_KINDS:
            continue
        try:
            ink, frame = layout.page_ink(pdf_bytes, number, budget=budget)
        except Exception as exc:  # strokes are never worth failing a conversion for
            log.warning("page ink failed: %s", type(exc).__name__)
            continue
        if ink.analysed and len(ink.strokes) >= MIN_STROKES:
            found[number] = VectorPage(number, tuple(ink.strokes), frame)
    return found


# ------------------------------------------------------------------------ the picture


def render(strokes: Sequence[Stroke], *, dpi: int = CROP_DPI, tag_dpi: bool = False) -> bytes:
    """PNG of `strokes` alone: black on white, cut to the ink with `CROP_MARGIN_PT` of paper
    round it, at `dpi` (less if the long side would pass `CROP_MAX_SIDE_PX`), drawn at twice
    the size and scaled down for smooth edges. `tag_dpi` writes the resolution into the PNG
    (`equation_scale.png_dpi`), as `layout.crop` does."""
    from PIL import Image, ImageDraw

    boxes = [s.bbox for s in strokes]
    x0 = min(b[0] for b in boxes) - CROP_MARGIN_PT
    y0 = min(b[1] for b in boxes) - CROP_MARGIN_PT
    x1 = max(b[2] for b in boxes) + CROP_MARGIN_PT
    y1 = max(b[3] for b in boxes) + CROP_MARGIN_PT
    zoom = min(dpi / 72.0, (CROP_MAX_SIDE_PX - 1) / max(x1 - x0, y1 - y0))
    width, height = max(2, math.ceil((x1 - x0) * zoom)), max(2, math.ceil((y1 - y0) * zoom))
    big = zoom * SUPERSAMPLE
    canvas = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 255)
    draw = ImageDraw.Draw(canvas)
    for stroke in strokes:
        pen = max(CROP_MIN_PEN_PX * SUPERSAMPLE, stroke.width * big)
        pts = [((x - x0) * big, (y - y0) * big) for x, y in stroke.points]
        r = pen / 2
        if len(pts) > 1:
            draw.line(pts, fill=0, width=round(pen), joint="curve")
        for x, y in (pts[0], pts[-1]):  # round caps (and a dot is one)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=0)
    image = canvas.resize((width, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if tag_dpi:
        image.save(out, format="PNG", dpi=(round(zoom * 72), round(zoom * 72)))
    else:
        image.save(out, format="PNG")
    return out.getvalue()


def wants_picture(element: Element) -> bool:
    """Is this the element of an image page whose picture is drawn from the strokes: a display
    equation, a prose block or list item, or a stretch of ink the reading left out (not the
    page's own picture, a table or a figure the reader described)?"""
    if ORIGINAL_REASON in element.reasons:
        return False
    if IMAGE_PAGE_OMISSION_REASON in element.reasons:
        return True
    return IMAGE_PAGE_REASON in element.reasons and element.kind in (
        Kind.EQUATION,
        Kind.PARAGRAPH,
        Kind.LIST_ITEM,
    )


def element_picture(
    pages: Mapping[int, VectorPage],
    element: Element,
    *,
    dpi: int = CROP_DPI,
    tag_dpi: bool = False,
) -> bytes | None:
    """The picture of `element` drawn from its page's strokes, or None (the page has none, the
    element is not one that is drawn, or there is no ink in its box): the ordinary crop is used."""
    page = pages.get(element.page)
    if page is None or not wants_picture(element):
        return None
    return page.crop(element.bbox, dpi=dpi, tag_dpi=tag_dpi)


# ------------------------------------------------------------------------ the glyphs


@dataclass(frozen=True)
class Glyph:
    """Strokes that are one written symbol: x-ranges that overlap (the two bars of "=", the dot
    of "i", the strokes of "x"), close in height."""

    box: BBox
    members: tuple[int, ...]  # indices into the strokes given

    @property
    def w(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def h(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def cx(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def cy(self) -> float:
        return (self.box[1] + self.box[3]) / 2


def _size(box: BBox) -> float:
    return max(box[2] - box[0], box[3] - box[1])


def glyphs(strokes: Sequence[Stroke]) -> list[Glyph] | None:
    """The glyphs of `strokes`, left to right (then top to bottom). None when the strokes
    cannot be grouped within `MAX_GLYPH_PAIRS` comparisons (not writing, or too much of it
    for a page's check)."""
    n = len(strokes)
    if n == 0:
        return []
    boxes = [s.bbox for s in strokes]
    median = statistics.median(_size(b) for b in boxes) or 1.0
    reach_y = GLUE_Y * median
    cell = max(8.0, 2.0 * median)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (bx0, by0, bx1, by1) in enumerate(boxes):
        for gx in range(int((bx0) // cell), int((bx1) // cell) + 1):
            for gy in range(int((by0 - reach_y) // cell), int((by1 + reach_y) // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: set[tuple[int, int]] = set()
    for members in grid.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = members[a], members[b]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                if len(seen) > MAX_GLYPH_PAIRS:
                    return None
                ai, aj = boxes[i], boxes[j]
                overlap = min(ai[2], aj[2]) - max(ai[0], aj[0])
                narrow = min(ai[2] - ai[0], aj[2] - aj[0])
                if overlap < GLUE_X * narrow or overlap < 0:
                    continue
                gap = max(ai[1], aj[1]) - min(ai[3], aj[3])
                if gap <= reach_y:
                    parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for idx in groups.values():
        bx = [boxes[i] for i in idx]
        out.append(
            Glyph(
                (
                    min(b[0] for b in bx),
                    min(b[1] for b in bx),
                    max(b[2] for b in bx),
                    max(b[3] for b in bx),
                ),
                tuple(sorted(idx)),
            )
        )
    return sorted(out, key=lambda g: (g.box[0], g.box[1]))


# ----------------------------------------------------------------------- the reading


@dataclass(frozen=True)
class Atom:
    """One symbol of a reading, in writing order. `level` is 0 on the baseline, 1 raised, -1
    lowered; `plain` says its case can be told from its height (not words, names or styled
    letters)."""

    ch: str
    kind: str  # "letter", "digit", "greek", "op", "open", "close", "mark"
    level: int = 0
    plain: bool = False


@dataclass(frozen=True)
class Reading:
    atoms: tuple[Atom, ...]
    stacked: bool  # a base has a subscript and a superscript: they are written one over the other


_STRUCTURE = frozenset(
    [
        "frac",
        "dfrac",
        "tfrac",
        "cfrac",
        "sqrt",
        "sum",
        "int",
        "iint",
        "iiint",
        "oint",
        "prod",
        "coprod",
        "lim",
        "limsup",
        "liminf",
        "binom",
        "begin",
        "end",
        "overset",
        "underset",
        "stackrel",
        "xrightarrow",
        "xleftarrow",
        "underbrace",
        "overbrace",
        "overline",
        "underline",
        "substack",
        "choose",
        "over",
        "atop",
        "not",
        "cancel",
        "boxed",
        "bigcup",
        "bigcap",
        "bigoplus",
        "bigotimes",
        "bigwedge",
        "bigvee",
        "tag",
        "label",
    ]
)
_ACCENTS = frozenset(
    [
        "hat",
        "bar",
        "vec",
        "dot",
        "ddot",
        "tilde",
        "widehat",
        "widetilde",
        "check",
        "breve",
        "acute",
        "grave",
        "mathring",
    ]
)
_STYLES = frozenset(
    [
        "mathrm",
        "mathbf",
        "mathit",
        "mathcal",
        "mathbb",
        "mathsf",
        "mathfrak",
        "boldsymbol",
        "operatorname",
        "text",
        "textbf",
        "textrm",
        "textit",
        "mbox",
    ]
)
_NAMES = frozenset(
    [
        "sin",
        "cos",
        "tan",
        "cot",
        "sec",
        "csc",
        "sinh",
        "cosh",
        "tanh",
        "arcsin",
        "arccos",
        "arctan",
        "log",
        "ln",
        "exp",
        "det",
        "dim",
        "ker",
        "arg",
        "max",
        "min",
        "sup",
        "inf",
        "gcd",
        "deg",
        "lg",
        "mod",
    ]
)
_QUIET = frozenset(
    {"left", "right", "big", "Big", "bigg", "Bigg", "displaystyle", "textstyle", "limits"}
    | {"nolimits", ",", ";", ":", "!", " ", "quad", "qquad"}
)
_DOTS = frozenset({"ldots", "cdots", "dots", "dotsb", "dotsc"})
_OPEN = frozenset({"(", "[", "{", "langle", "lfloor", "lceil", "lbrace", "lbrack", "lvert"})
_CLOSE = frozenset({")", "]", "}", "rangle", "rfloor", "rceil", "rbrace", "rbrack", "rvert"})
_GREEK = frozenset(
    [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "pi",
        "varpi",
        "rho",
        "varrho",
        "sigma",
        "varsigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
        "Gamma",
        "Delta",
        "Theta",
        "Lambda",
        "Xi",
        "Pi",
        "Sigma",
        "Upsilon",
        "Phi",
        "Psi",
        "Omega",
    ]
)
_MARKS = frozenset({",", ".", "'", "prime", "cdot", "bullet", "ast"})


class _Unsupported(Exception):
    """A reading whose symbols the strokes cannot be lined up with."""


def tokens(latex: str) -> list[str]:
    """LaTeX as commands (`\\alpha`, `\\{`), braces, `^`, `_` and single characters."""
    out: list[str] = []
    i, n = 0, len(latex)
    while i < n:
        c = latex[i]
        if c == "\\":
            j = i + 1
            if j < n and latex[j].isalpha():
                while j < n and latex[j].isalpha():
                    j += 1
            else:
                j = min(n, j + 1)
            out.append(latex[i:j])
            i = j
        elif c.isspace():
            i += 1
        else:
            out.append(c)
            i += 1
    return out


def _argument(seq: list[str], i: int) -> tuple[list[str], int]:
    """The argument starting at `seq[i]`: a brace group (without the braces) or one token."""
    if i >= len(seq) or seq[i] in ("^", "_", "}"):
        raise _Unsupported
    if seq[i] != "{":
        return [seq[i]], i + 1
    depth = 0
    for j in range(i, len(seq)):
        if seq[j] == "{":
            depth += 1
        elif seq[j] == "}":
            depth -= 1
            if depth == 0:
                return seq[i + 1 : j], j + 1
    raise _Unsupported


def _command(name: str, level: int) -> list[Atom]:
    """The atoms a command makes (none for spacing and size commands)."""
    if name in _QUIET:
        return []
    if name in _NAMES:
        return [Atom(c, "letter", level) for c in name]
    if name in _DOTS:
        return [Atom(".", "mark", level) for _ in range(3)]
    if name in _GREEK:
        return [Atom(name, "greek", level)]
    if name in _OPEN:
        return [Atom(name, "open", level)]
    if name in _CLOSE:
        return [Atom(name, "close", level)]
    return [Atom(name, "mark" if name in _MARKS else "op", level)]


def reading(latex: str) -> Reading | None:
    """The symbols of `latex` in writing order with the level each is written at, or None when
    the reading has a structure that stacks or encloses symbols (fraction, root, sum, matrix,
    an accent over several symbols, a line break) or too many symbols to line up."""
    out: list[Atom] = []
    stacked = False

    def walk(seq: list[str], level: int, plain: bool) -> None:
        nonlocal stacked
        i = 0
        last_script = 0
        while i < len(seq):
            tok = seq[i]
            i += 1
            if tok in ("^", "_"):
                sign = 1 if tok == "^" else -1
                if last_script == -sign:
                    stacked = True
                last_script = sign
                arg, i = _argument(seq, i)
                walk(arg, sign, plain)
                continue
            last_script = 0
            if tok == "{":
                arg, i = _argument(seq, i - 1)
                walk(arg, level, plain)
            elif tok in ("}", "&"):
                raise _Unsupported
            elif tok.startswith("\\"):
                name = tok[1:]
                if name in _STRUCTURE or name == "\\":
                    raise _Unsupported
                if name in _ACCENTS or name in _STYLES:
                    arg, i = _argument(seq, i)
                    if name in _ACCENTS and len(arg) != 1:
                        raise _Unsupported
                    walk(arg, level, plain and name in _ACCENTS)
                else:
                    out.extend(_command(name, level))
            elif tok.isdigit():
                out.append(Atom(tok, "digit", level))
            elif tok.isalpha():
                out.append(Atom(tok, "letter", level, plain and level == 0))
            elif tok in _OPEN:
                out.append(Atom(tok, "open", level))
            elif tok in _CLOSE:
                out.append(Atom(tok, "close", level))
            else:
                out.append(Atom(tok, "mark" if tok in _MARKS else "op", level))
            if len(out) > MAX_ATOMS:
                raise _Unsupported

    try:
        walk(tokens(latex), 0, True)
    except _Unsupported:
        return None
    return Reading(tuple(out), stacked)


# ---------------------------------------------------------------------- the line


@dataclass(frozen=True)
class Line:
    """The letter size and baseline of a line of writing, from its strokes: the baseline is
    the line `y = a + slope * x` (writing slopes), `size` the height of its ordinary letters."""

    size: float
    a: float
    slope: float
    spread: float = (
        0.0  # how far the bottoms of the glyphs it was drawn through sit from it (of size)
    )

    def base(self, x: float) -> float:
        return self.a + self.slope * x


def _fit(points: Sequence[tuple[float, float]], sizes: Sequence[float]) -> Line | None:
    """The baseline through the bottoms `points` (x, y) by repeated medians (the median over
    points of the median slope to the others: a stray tail or a raised glyph, up to half the
    points, does not move it) and `sizes`' median."""
    if not points or not sizes:
        return None
    each = []
    for i, (x1, y1) in enumerate(points):
        slopes = [
            (y2 - y1) / (x2 - x1)
            for j, (x2, y2) in enumerate(points)
            if j != i and abs(x2 - x1) > 1e-6
        ]
        if slopes:
            each.append(statistics.median(slopes))
    slope = 0.0
    if len(points) >= 3 and each:
        slope = max(-MAX_SLOPE, min(MAX_SLOPE, statistics.median(each)))
    a = statistics.median(y - slope * x for x, y in points)
    size = statistics.median(sizes)
    spread = (
        statistics.median(abs(y - slope * x - a) for x, y in points) / size if size > 0 else 0.0
    )
    return Line(size, a, slope, spread) if size > 0 else None


def rough_line(gs: Sequence[Glyph]) -> Line | None:
    """A first estimate of the line from the strokes alone: the tall glyphs (those at least
    three quarters as tall as the upper quartile of the glyphs that are not flat) give the
    baseline, the glyphs over half that tall give the size."""
    body = [g for g in gs if g.w <= 1.6 * g.h]
    if len(body) < 2:
        return None
    heights = sorted(g.h for g in body)
    top = heights[int(0.75 * (len(heights) - 1))]
    tall = [g for g in body if g.h >= 0.75 * top]
    return _fit([(g.cx, g.box[3]) for g in tall], [g.h for g in body if g.h >= 0.55 * top])


def labels(gs: Sequence[Glyph], line: Line) -> list[str]:
    """Each glyph as "B" (on the baseline, ordinary size), "U" (small and raised), "D" (small and
    lowered) or "M" (a dot, a comma, a bar: too small to say)."""
    out = []
    for g in gs:
        if max(g.h, g.w) < MARK_SIZE * line.size:
            out.append("M")
            continue
        if g.w > 1.6 * g.h:  # a bar, a minus, an arrow: where it sits says nothing
            out.append("X")
            continue
        small = g.h / line.size <= SCRIPT_SIZE
        up = (line.base(g.cx) - g.box[3]) / line.size  # how far its bottom is above the baseline
        if up >= RAISED_AT:  # a letter does not float: its bottom is up where a script's is
            out.append("U")
        elif not small:
            out.append("B")
        elif -up >= LOWERED_AT:
            out.append("D")
        else:
            out.append("X")  # small, not clearly raised or lowered: no evidence either way
    return out


_WANT = {0: "B", 1: "U", -1: "D"}
_BASE_KINDS = ("letter", "digit", "greek")


def _cost(atom: Atom, label: str) -> float:
    if atom.kind not in _BASE_KINDS:  # a mark, an operator, a bracket: its place says nothing
        return 0.2
    if label == "M":
        return 0.6
    if label == "X":
        return 0.3
    return 0.0 if _WANT[atom.level] == label else SUB_COST


def align(atoms_: Sequence[Atom], marks: Sequence[str]) -> list[tuple[int | None, int | None]]:
    """The symbols of a reading lined up with the glyphs written (writing order both), as pairs
    of indices; one side is None where a symbol has no glyph or a glyph no symbol."""
    n, m = len(atoms_), len(marks)
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i * GAP_COST
    for j in range(1, m + 1):
        cost[0][j] = j * GAP_COST
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(
                cost[i - 1][j - 1] + _cost(atoms_[i - 1], marks[j - 1]),
                cost[i - 1][j] + GAP_COST,
                cost[i][j - 1] + GAP_COST,
            )
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        step = _cost(atoms_[i - 1], marks[j - 1]) if i > 0 and j > 0 else 0.0
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + step:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + GAP_COST:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return pairs[::-1]


def _refit(
    read: Reading, gs: Sequence[Glyph], pairs: Sequence[tuple[int | None, int | None]]
) -> Line | None:
    """The line drawn through the glyphs the reading says are ordinary letters, digits or Greek
    letters on the baseline (a script it leaves out is one stray glyph among them)."""
    base = [
        (read.atoms[i], gs[j])
        for i, j in pairs
        if i is not None
        and j is not None
        and read.atoms[i].level == 0
        and read.atoms[i].kind in _BASE_KINDS
    ]
    if len(base) < 3:
        return None
    return _fit(
        [(g.cx, g.box[3]) for a, g in base if a.ch not in DESCENDERS],
        [g.h for _, g in base],
    )


def settled(
    read: Reading, gs: Sequence[Glyph]
) -> tuple[Line, list[str], list[tuple[int | None, int | None]]] | None:
    """The line, the label of each glyph and the symbols lined up with the glyphs, for a
    reading and the ink it is of. With as many glyphs as symbols they pair one to one;
    otherwise the line is first guessed from the strokes alone and the two are lined up by
    their levels. The line is then drawn from the glyphs the reading says are on the baseline
    and the glyphs labelled against it. None when there is no line to measure against."""
    rough = rough_line(gs)
    if len(read.atoms) == len(gs):
        pairs: list[tuple[int | None, int | None]] = [(k, k) for k in range(len(gs))]
    elif rough is None:
        return None
    else:
        pairs = align(read.atoms, labels(gs, rough))
    line = _refit(read, gs, pairs) or rough
    if line is None:
        return None
    marks = labels(gs, line)
    if len(read.atoms) != len(gs):
        pairs = align(read.atoms, marks)
        line = _refit(read, gs, pairs) or line
        marks = labels(gs, line)
    return line, marks, pairs


def script_events(read: Reading, gs: Sequence[Glyph]) -> set[str]:
    """What the strokes say about the scripts of `read` (`SCRIPT_LOST`, `SCRIPT_ADDED`,
    `SCRIPT_MOVED`); empty when they say nothing or cannot be lined up."""
    if read.stacked or len(gs) > MAX_GLYPHS or not read.atoms or len(gs) < 2:
        return set()
    got = settled(read, gs)
    if got is None:
        return set()
    line, marks, pairs = got
    if line.spread > MAX_SPREAD:  # the baseline is no line: nothing to measure a script against
        return set()
    found: set[str] = set()
    for i, j in pairs:
        if j is None:
            continue
        mark = marks[j]
        if i is None:
            if mark in "UD":
                found.add(SCRIPT_LOST)
            continue
        atom = read.atoms[i]
        if atom.kind not in _BASE_KINDS or mark in "MX":
            continue
        want = _WANT[atom.level]
        if want == mark:
            continue
        if want == "B":
            found.add(SCRIPT_LOST)
        elif mark == "B":
            found.add(SCRIPT_ADDED)
        else:
            found.add(SCRIPT_MOVED)
    return found


# ---------------------------------------------------------------------- the case


@dataclass(frozen=True)
class Heights:
    """Heights of a small letter (`x`) and a capital (`cap`) in one hand."""

    x: float
    cap: float


def _is_small(a: Atom) -> bool:
    return a.plain and a.kind == "letter" and a.level == 0 and a.ch in X_HEIGHT


def _is_tall(a: Atom) -> bool:
    if a.level != 0:
        return False
    if a.kind == "digit":
        return True
    return a.plain and a.kind == "letter" and (a.ch in CAPITALS or a.ch in ASCENDERS)


def own_heights(read: Reading, gs: Sequence[Glyph]) -> Heights | None:
    """The x-height and capital height shown by the letters of `read` that are not in doubt
    (a, e, m, n, r; digits, capitals only a capital is written like, ascenders), each from
    its glyph. With only one of the two the other follows by `CAP_OVER_X`. None when the
    reading has none of them or its symbols and glyphs do not pair one to one."""
    if len(read.atoms) != len(gs):
        return None
    smalls = [g.h for a, g in zip(read.atoms, gs, strict=True) if _is_small(a)]
    talls = [g.h for a, g in zip(read.atoms, gs, strict=True) if _is_tall(a)]
    if smalls and talls:
        return Heights(statistics.median(smalls), statistics.median(talls))
    if smalls:
        return Heights(statistics.median(smalls), CAP_OVER_X * statistics.median(smalls))
    if talls:
        return Heights(statistics.median(talls) / CAP_OVER_X, statistics.median(talls))
    return None


def case_doubts(read: Reading, gs: Sequence[Glyph], page: Heights | None = None) -> list[int]:
    """Indices of symbols of `read` whose case the ink contradicts: a small letter written as
    tall as a capital, or a capital written as short as a small letter, measured against the
    heights of the line's own letters (`own_heights`, else `page`, the hand's heights on the
    page). Only the letters whose small and capital forms differ in height alone are
    measured (`CASE_LETTERS`)."""
    if len(read.atoms) != len(gs) or len(gs) > MAX_GLYPHS:
        return []
    heights = own_heights(read, gs) or page
    if heights is None or heights.cap <= heights.x:
        return []
    found = []
    for i, (a, g) in enumerate(zip(read.atoms, gs, strict=True)):
        if not (a.plain and a.kind == "letter" and a.ch.lower() in CASE_LETTERS):
            continue
        t = (g.h - heights.x) / (heights.cap - heights.x)
        if (a.ch.islower() and t >= CASE_UP_AT) or (a.ch.isupper() and t <= CASE_LOW_AT):
            found.append(i)
    return found


# ---------------------------------------------------------------------- leftover ink


def _gap(a: BBox, b: BBox) -> tuple[float, float]:
    """The empty space between two boxes, sideways and vertically (0 where they overlap)."""
    return (
        max(a[0] - b[2], b[0] - a[2], 0.0),
        max(a[1] - b[3], b[1] - a[3], 0.0),
    )


def leftover(page: VectorPage, boxes: Sequence[BBox]) -> list[omission.Region]:
    """Stretches of writing on `page` that no box of `boxes` (the blocks of the reading, in
    points) is near, as `image_page_omission` regions (thousandths of the page). A glyph is
    covered when the nearest box is within `COVER_PAD` glyph heights of its centre; what is
    left is grouped, and a stretch of fewer than `MIN_LEFTOVER_GLYPHS` glyphs, or a short
    line in the strip where headers and page numbers sit, is not reported. No boxes: none."""
    if not boxes:
        return []
    gs = glyphs(page.strokes)
    if not gs or len(gs) > 4000:
        return []
    sizes = sorted(max(g.h, g.w) for g in gs)
    unit = sizes[len(sizes) // 2]
    unit = statistics.median(s for s in sizes if s >= 0.35 * unit) if unit > 0 else 0.0
    if unit <= 0:
        return []
    pad = COVER_PAD * unit
    open_ = [
        g
        for g in gs
        if min(
            _gap((g.cx, g.cy, g.cx, g.cy), b)[0] ** 2 + _gap((g.cx, g.cy, g.cx, g.cy), b)[1] ** 2
            for b in boxes
        )
        > pad**2
    ]
    if len(open_) < MIN_LEFTOVER_GLYPHS or len(open_) > 1500:
        return []
    parent = list(range(len(open_)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(open_)):
        for j in range(i + 1, len(open_)):
            dx, dy = _gap(open_[i].box, open_[j].box)
            if dx <= JOIN_GAP_X * unit and dy <= JOIN_GAP_Y * unit:
                parent[find(i)] = find(j)
    groups: dict[int, list[Glyph]] = {}
    for i, g in enumerate(open_):
        groups.setdefault(find(i), []).append(g)
    fx0, fy0, fx1, fy1 = page.frame
    width, height = fx1 - fx0, fy1 - fy0
    pieces: list[tuple[BBox, int]] = []
    for members in groups.values():
        if len(members) < MIN_LEFTOVER_GLYPHS:
            continue
        box = (
            min(g.box[0] for g in members),
            min(g.box[1] for g in members),
            max(g.box[2] for g in members),
            max(g.box[3] for g in members),
        )
        in_band = box[3] <= fy0 + BAND * height or box[1] >= fy1 - BAND * height
        if in_band and box[3] - box[1] <= 2.5 * unit:
            continue  # a running header, a footer or a page number
        pieces.append((box, len(members)))
    merged: list[tuple[BBox, int]] = []
    for box, count in sorted(pieces, key=lambda p: p[0][1]):
        if merged and box[1] - merged[-1][0][3] <= MERGE_Y * height:
            last, n = merged[-1]
            merged[-1] = (
                (min(last[0], box[0]), last[1], max(last[2], box[2]), max(last[3], box[3])),
                n + count,
            )
        else:
            merged.append((box, count))
    total = len(gs)
    found = [
        omission.Region(
            x0=max(0, round(((b[0] - fx0) / width - REGION_MARGIN) * 1000)),
            y0=max(0, round(((b[1] - fy0) / height - REGION_MARGIN) * 1000)),
            x1=min(1000, round(((b[2] - fx0) / width + REGION_MARGIN) * 1000)),
            y1=min(1000, round(((b[3] - fy0) / height + REGION_MARGIN) * 1000)),
            cells=count,
            share=round(count / total, 4),
        )
        for b, count in merged
    ]
    found.sort(key=lambda r: -r.cells)
    return sorted(found[:MAX_REGIONS], key=lambda r: (r.y0, r.x0))


def block_boxes(page: VectorPage, units: Iterable[tuple[int, int, int, int]]) -> list[BBox]:
    """Boxes given in thousandths of the page, in points."""
    fx0, fy0, fx1, fy1 = page.frame
    width, height = fx1 - fx0, fy1 - fy0
    return [
        (
            fx0 + x0 / 1000 * width,
            fy0 + y0 / 1000 * height,
            fx0 + x1 / 1000 * width,
            fy0 + y1 / 1000 * height,
        )
        for x0, y0, x1, y1 in units
    ]


# ------------------------------------------------------------------- the checks


_FAILED: dict[tuple[str, str], Check] = {
    (CASE_CHECK, CASE_MISMATCH): Check(
        name="image_page.case_check", passed=False, severity="warning", code="case_mismatch"
    ),
    (SCRIPT_CHECK, SCRIPT_LOST): Check(
        name="image_page.script_check", passed=False, severity="warning", code="script_lost"
    ),
    (SCRIPT_CHECK, SCRIPT_ADDED): Check(
        name="image_page.script_check", passed=False, severity="warning", code="script_added"
    ),
    (SCRIPT_CHECK, SCRIPT_MOVED): Check(
        name="image_page.script_check", passed=False, severity="warning", code="script_moved"
    ),
}


@dataclass
class _Equation:
    element: Element
    fix: Fix
    read: Reading
    glyphs: list[Glyph]
    own: Heights | None = None


def _equations(
    elements: Sequence[Element], fixes: Mapping[str, Fix], vectors: Mapping[int, VectorPage]
) -> dict[int, list[_Equation]]:
    """The display equations read from handwriting on a vector page, with the symbols of the
    reading and the glyphs of the ink in its box, by page. An equation whose reading has
    nothing to line up (a structure the check does not read) or whose box holds no ink is
    left out."""
    found: dict[int, list[_Equation]] = {}
    for element in elements:
        page = vectors.get(element.page)
        fix = fixes.get(element.id)
        if (
            page is None
            or fix is None
            or fix.equation is None
            or element.kind is not Kind.EQUATION
            or element.parent_id is not None
            or IMAGE_PAGE_REASON not in element.reasons
        ):
            continue
        read = reading(fix.equation.latex)
        if read is None or not read.atoms:
            continue
        height = element.bbox[3] - element.bbox[1]
        gs = glyphs(page.strokes_in(element.bbox, pad=4.0 + 0.2 * height))
        if not gs or len(gs) > MAX_GLYPHS:
            continue
        item = _Equation(element, fix, read, gs)
        item.own = own_heights(read, gs)
        found.setdefault(element.page, []).append(item)
    return found


def _page_heights(items: Sequence[_Equation]) -> Heights | None:
    """The hand's small-letter and capital heights on a page, from the equations that show
    both (two at least): the fallback for a line with no letter to measure against."""
    both = [i.own for i in items if i.own is not None]
    if len(both) < 2:
        return None
    return Heights(statistics.median(h.x for h in both), statistics.median(h.cap for h in both))


def add_checks(
    elements: Sequence[Element], fixes: Mapping[str, Fix], vectors: Mapping[int, VectorPage]
) -> int:
    """Add the case and script checks to the fixes of the display equations read from
    handwriting on the pages of `vectors` (in place; the fix keeps its status and its
    "required" review, so a check only says what to look at). Returns how many checks were
    added. Fail-safe: an equation the strokes cannot be lined up with gets none."""
    added = 0
    for items in _equations(elements, fixes, vectors).values():
        page = _page_heights(items)
        for item in items:
            try:
                wanted = (
                    [(CASE_CHECK, CASE_MISMATCH)]
                    if case_doubts(item.read, item.glyphs, page)
                    else []
                )
                wanted += [(SCRIPT_CHECK, c) for c in sorted(script_events(item.read, item.glyphs))]
            except Exception as exc:  # a check never fails a conversion
                log.warning("stroke check failed: %s", type(exc).__name__)
                continue
            have = {(c.name, c.code) for c in item.fix.checks}
            fresh = [_FAILED[k].model_copy() for k in wanted if k not in have]
            if fresh:
                item.fix.checks = [*item.fix.checks, *fresh]
                added += len(fresh)
    return added
