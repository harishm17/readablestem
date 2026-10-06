"""PDF page -> elements (stub).

Step 1 of the pipeline: split a page into text blocks, images and
candidate equation regions. This uses PyMuPDF's text dictionary only. It does not call
any model yet.

What this stub does:
- text blocks with their font size (input for heading detection later),
- images (bounding box and pixel size; image bytes are not kept),
- equation candidates: text lines that look like maths by a cheap, explainable score
  (math fonts, operators, Greek letters, short symbol-like tokens, superscripts).

TODO(vision step, Oct 21-26 per the plan): for each equation candidate and each image,
render the region with `render_region()`, send it to the vision model, get LaTeX back,
convert with `a11y_stem.eval.equations.latex_to_mathml`, then validate (LaTeX compiles,
re-render matches the crop). Equations drawn as vector paths (fraction bars, radicals)
are not detected yet; `page.get_drawings()` near a candidate is the next signal to add.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

ElementKind = Literal["text", "image", "equation_candidate"]

MATH_FONT = re.compile(
    r"(CMMI|CMSY|CMEX|CMBSY|MSAM|MSBM|Math|Symbol|STIX|MTExtra|MT Extra|Euclid)", re.I
)
RELATIONS = set("=<>\u2264\u2265\u2260\u2248\u2261\u221d\u2192")  # <= >= != ~= == prop ->
MATH_CHARS = set("+-*/^_()[]{}|=<>") | set(
    # minus, times, divide, plus-minus, minus-plus, sum, prod, integral, sqrt, partial,
    # nabla, infinity
    "\u2212\u00d7\u00f7\u00b1\u2213\u2211\u220f\u222b\u221a\u2202\u2207\u221e"
    "\u2264\u2265\u2260\u2248\u2261\u221d\u2192\u22c5\u00b7"
)
GREEK = re.compile("[\u0391-\u03a9\u03b1-\u03c9]")
SUPERSCRIPT_FLAG = 1  # PyMuPDF span flag bit 0
EQUATION_THRESHOLD = 0.5


@dataclass
class Element:
    kind: ElementKind
    page: int  # 0-based
    bbox: tuple[float, float, float, float]
    text: str | None = None
    font_size: float | None = None
    fonts: list[str] = field(default_factory=list)
    score: float | None = None  # equation score for text lines
    reasons: list[str] = field(default_factory=list)
    width_px: int | None = None  # images only
    height_px: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def equation_score(text: str, fonts: list[str], has_superscript: bool) -> tuple[float, list[str]]:
    """Cheap, explainable 'does this line look like maths' score in [0, 1+]."""
    score = 0.0
    reasons: list[str] = []
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0, reasons
    if any(MATH_FONT.search(f) for f in fonts):
        score += 0.5
        reasons.append("math font")
    if any(c in RELATIONS for c in chars):
        score += 0.4
        reasons.append("relation symbol")
    math_ratio = sum(c in MATH_CHARS or bool(GREEK.match(c)) for c in chars) / len(chars)
    if math_ratio >= 0.2:
        score += 0.3
        reasons.append(f"math chars {math_ratio:.0%}")
    tokens = text.split()
    if tokens and sum(len(t) <= 2 for t in tokens) / len(tokens) >= 0.6:
        score += 0.2
        reasons.append("short symbol-like tokens")
    if has_superscript:
        score += 0.1
        reasons.append("superscript")
    if sum(len(t) >= 4 and t.isalpha() for t in tokens) >= 6:
        score -= 0.5
        reasons.append("prose penalty")
    return round(score, 3), reasons


def parse_pdf(path: str | Path, pages: list[int] | None = None) -> list[Element]:
    """Return elements for the given 0-based pages (all pages by default)."""
    elements: list[Element] = []
    with pymupdf.open(path) as doc:
        page_numbers = pages if pages is not None else range(doc.page_count)
        for number in page_numbers:
            elements.extend(parse_page(doc[number], number))
    return elements


def parse_page(page: pymupdf.Page, number: int) -> list[Element]:
    out: list[Element] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        bbox = tuple(round(v, 2) for v in block["bbox"])
        if block.get("type") == 1:
            out.append(
                Element(
                    kind="image",
                    page=number,
                    bbox=bbox,
                    width_px=block.get("width"),
                    height_px=block.get("height"),
                )
            )
            continue
        prose_lines: list[str] = []
        sizes: list[float] = []
        block_fonts: set[str] = set()
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            fonts = sorted({s.get("font", "") for s in spans})
            superscript = any(s.get("flags", 0) & SUPERSCRIPT_FLAG for s in spans)
            score, reasons = equation_score(text, fonts, superscript)
            line_size = max((s.get("size", 0.0) for s in spans), default=0.0)
            if score >= EQUATION_THRESHOLD:
                out.append(
                    Element(
                        kind="equation_candidate",
                        page=number,
                        bbox=tuple(round(v, 2) for v in line["bbox"]),
                        text=text,
                        font_size=round(line_size, 2),
                        fonts=fonts,
                        score=score,
                        reasons=reasons,
                    )
                )
            else:
                prose_lines.append(text)
                sizes.append(line_size)
                block_fonts.update(fonts)
        if prose_lines:
            out.append(
                Element(
                    kind="text",
                    page=number,
                    bbox=bbox,
                    text="\n".join(prose_lines),
                    font_size=round(max(sizes), 2),
                    fonts=sorted(block_fonts),
                )
            )
    return out


def render_region(
    path: str | Path, page: int, bbox: tuple[float, float, float, float], dpi: int = 200
) -> bytes:
    """PNG bytes of a page region; the input the vision step will receive."""
    with pymupdf.open(path) as doc:
        pixmap = doc[page].get_pixmap(clip=pymupdf.Rect(bbox), dpi=dpi)
        return pixmap.tobytes("png")


def transcribe_equation(png: bytes) -> str:
    """TODO(vision step): image of an equation -> LaTeX via the vision model.

    Not implemented in the skeleton. Model, prompt, cost cap per call and fallback model
    are open decisions (docs/decisions/0001-scope.md). Never send private-inputs/ pages to
    a provider that trains on inputs or keeps them, and keep tracing off for those runs.
    """
    raise NotImplementedError("vision step not built yet; see module docstring")
