"""PDF page -> raw elements: the quick, line-level view behind `readablestem parse`.

This module is deliberately simple: PyMuPDF's text dictionary, one element per text run,
image or maths-looking line, in PyMuPDF block order. It is a debugging aid and the home
of `equation_score`, the cheap, explainable "does this text look like maths" score.

The real analysis (reading order, columns, headings, display/inline equations, vector
fraction bars, figures with captions, running headers) lives in `pipeline.layout`, which
reuses `equation_score`, `MATH_FONT` and the character sets below. Model calls live in
`pipeline.equation_step` and `pipeline.figure_step`.

What this module returns:
- text blocks with their font size,
- images (bounding box and pixel size; image bytes are not kept),
- equation candidates: text lines that look like maths by `equation_score` (math fonts,
  operators, Greek letters, math-italic letters, short symbol-like tokens, superscripts).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

ElementKind = Literal["text", "image", "equation_candidate"]

# Font families used only for maths: TeX (CM*, AMS MSAM/MSBM, txfonts/pxfonts, rsfs,
# euler), OpenType maths fonts (Cambria Math, Latin Modern Math, STIX Two Math, Noto Sans
# Math: all contain "Math"), Word/MathType (MT Extra, Euclid) and Symbol.
MATH_FONT = re.compile(
    r"(CMMI|CMSY|CMEX|CMBSY|MSAM|MSBM|Math|Symbol|STIX|MTExtra|MT Extra|Euclid|"
    r"rsfs|eufm|eurm|txmi|pxmi|txsy|pxsy|txex|pxex|MTMI|MTSY|esint)",
    re.I,
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


def is_math_alphanumeric(char: str) -> bool:
    """Unicode "Mathematical Alphanumeric Symbols" (U+1D400-U+1D7FF), e.g. italic \U0001d463.

    Browsers printing MathML and Word's equation editor emit these for variables, so a
    character in this block is maths whatever font it is in.
    """
    return len(char) == 1 and 0x1D400 <= ord(char) <= 0x1D7FF


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
    math_ratio = sum(
        c in MATH_CHARS or bool(GREEK.match(c)) or is_math_alphanumeric(c) for c in chars
    ) / len(chars)
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


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        round(min(b[0] for b in boxes), 2),
        round(min(b[1] for b in boxes), 2),
        round(max(b[2] for b in boxes), 2),
        round(max(b[3] for b in boxes), 2),
    )


@dataclass
class _ProseRun:
    """Consecutive prose lines of one block (no equation line between them)."""

    lines: list[str] = field(default_factory=list)
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    sizes: list[float] = field(default_factory=list)
    fonts: set[str] = field(default_factory=set)

    def to_element(self, number: int) -> Element:
        return Element(
            kind="text",
            page=number,
            bbox=_union(self.boxes),
            text="\n".join(self.lines),
            font_size=round(max(self.sizes), 2),
            fonts=sorted(self.fonts),
        )


def parse_page(page: pymupdf.Page, number: int) -> list[Element]:
    """Elements of one page, in PyMuPDF block order.

    When a block mixes prose and equation lines, the prose is split into one text element
    per run of consecutive prose lines, each with the bbox of its own lines only. Text
    elements therefore never overlap the equation candidates taken out of the same block,
    and the order prose -> equation -> prose is kept.
    """
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
        run = _ProseRun()
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
                if run.lines:
                    out.append(run.to_element(number))
                    run = _ProseRun()
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
                run.lines.append(text)
                run.boxes.append(tuple(line["bbox"]))
                run.sizes.append(line_size)
                run.fonts.update(fonts)
        if run.lines:
            out.append(run.to_element(number))
    return out


def render_region(
    path: str | Path, page: int, bbox: tuple[float, float, float, float], dpi: int = 200
) -> bytes:
    """PNG bytes of a page region; the input the vision step will receive."""
    with pymupdf.open(path) as doc:
        pixmap = doc[page].get_pixmap(clip=pymupdf.Rect(bbox), dpi=dpi)
        return pixmap.tobytes("png")


def transcribe_equation(png: bytes) -> str:
    """Placeholder from the skeleton; the vision step is `pipeline.equation_step.transcribe`.

    Kept so an old caller fails loudly instead of silently getting nothing. Never send
    private-inputs/ pages to a provider that trains on inputs or keeps them, and keep
    tracing off for those runs.
    """
    raise NotImplementedError("use pipeline.equation_step.transcribe (see module docstring)")
