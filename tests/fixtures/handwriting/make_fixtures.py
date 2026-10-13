"""Regenerate the page-type fixtures: synthetic handwriting, scans and hidden text.

Usage:
    uv run python tests/fixtures/handwriting/make_fixtures.py [OUT_DIR]

Every file is built here from seeded random numbers and made-up text: there is no real
handwriting, no real scan and no real OCR output in them (docs/layout.md "Hidden text
and page types"). The ink imitates how note apps export pen strokes (filled ribbons
packed many to a path, as Apple's PencilKit writes them; stroked polylines), the text
layers imitate what iPad notes apps (fill opacity 0, an ".SF" font) and OCR programs
(render mode 3, GlyphLessFont; or visible text painted under the page image) write, and
the adversarial pages rebuild the design review's failing cases (a transparent overlay,
a clip-mode title, photo slides, a scan with a visible stamp, ink under a typed header),
the routing review's and the final verifier's (a short note under a typed title, plots
on pages with decorative frames, letters written as single curves).

The PDFs are committed (each under 100 KB) so the tests need nothing but PyMuPDF; the
same seeds give the same files.
"""

from __future__ import annotations

import itertools
import math
import random
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
FIXED_DATE = "D:20261009000000Z"
A4 = (595.28, 841.89)
IOS = "iOS Version 16.6 (Build 20G75) Quartz PDFContext"
INK_BLUE, INK_BLACK = (0.0, 0.44, 1.0), (0.05, 0.05, 0.08)

TYPED = [
    "Kinematics in one dimension",
    "Displacement is the change in position of an object. It has a direction as well as a "
    "size, so it is a vector. Distance is how far the object travels along its path.",
    "Average velocity is displacement divided by the time it takes. Speed is the size of "
    "the velocity. A car that drives 30 m east in 3 s has an average velocity of 10 m/s.",
    "Acceleration is the rate of change of velocity. When the velocity of an object does "
    "not change, its acceleration is zero, even if it moves very fast.",
]
STAMP = "Downloaded from library.example.edu on 2026-10-01. For personal use only."
HEADER = "PHYS 101 lecture notes | Date: ________ | Page: ____"
ANSWER = "Answer key: (a) 12 m/s (b) 7.3 s (c) 2.4 m"


# ----------------------------------------------------------------------------- ink


def _arc(cx, cy, rx, ry, a0, a1, n=None):
    n = n or max(6, int(abs(a1 - a0) / (math.pi / 10)))
    return [
        (cx + rx * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n))
        for i in range(n + 1)
    ]


def _line(p, q, n=6):
    return [(p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n) for i in range(n + 1)]


def _letter(ch: str):
    """Rough pen skeletons in em units (baseline y = 0, x-height 1, y up), and the advance."""
    d = math.radians
    if ch == "o":
        return [_arc(0.45, 0.5, 0.42, 0.5, d(70), d(450))], 0.95
    if ch == "a":
        return [_arc(0.45, 0.5, 0.4, 0.5, d(40), d(370)) + _line((0.85, 0.95), (0.92, 0.0))], 1.0
    if ch == "e":
        return [_line((0.08, 0.5), (0.85, 0.55)) + _arc(0.45, 0.5, 0.42, 0.5, d(20), d(320))], 0.9
    if ch == "l":
        return [_line((0.25, 1.85), (0.2, 0.15)) + _arc(0.4, 0.15, 0.2, 0.15, d(180), d(300))], 0.5
    if ch == "n":
        return [
            _line((0.1, 1.0), (0.1, 0.0))
            + _arc(0.45, 0.55, 0.35, 0.45, d(170), d(5))
            + _line((0.8, 0.55), (0.85, 0.0))
        ], 1.0
    if ch == "s":
        pts = [
            (0.8 - 0.65 * i / 16 + 0.25 * math.sin(i / 16 * 2 * math.pi), 1.0 - i / 16)
            for i in range(17)
        ]
        return [pts], 0.85
    if ch == "t":
        return [
            _line((0.4, 1.7), (0.38, 0.15)) + _arc(0.58, 0.15, 0.2, 0.15, d(180), d(320)),
            _line((0.1, 1.05), (0.75, 1.1), 4),
        ], 0.8
    if ch == "d":
        return [_arc(0.45, 0.5, 0.4, 0.5, d(40), d(370)) + _line((0.85, 1.85), (0.9, 0.0))], 1.0
    if ch == "x":
        return [
            _arc(0.0, 0.5, 0.45, 0.5, d(80), d(-80)),
            _arc(0.95, 0.5, 0.45, 0.5, d(100), d(260)),
        ], 0.95
    if ch == "=":
        return [_line((0.1, 0.7), (0.9, 0.72), 4), _line((0.1, 0.3), (0.9, 0.32), 4)], 1.1
    return [_line((0.15, 1.0), (0.2, 0.0)) + _arc(0.5, 0.5, 0.35, 0.5, d(180), d(-90))], 0.9


LETTERS = "aaeeelnnosttdx"


class Writer:
    """Pseudo-handwritten words as vector ink: "ribbon" (filled outlines, many strokes per
    path object), "polyline" (one stroked path per stroke) or "segments" (one stroked path
    per short segment, as Xournal++ writes them)."""

    def __init__(self, page: pymupdf.Page, seed: int, style: str, colour, width=1.1) -> None:
        self.page, self.rng, self.style, self.colour, self.width = (
            page,
            random.Random(seed),
            style,
            colour,
            width,
        )
        self.shape = page.new_shape()
        self.pending = 0
        self.word_boxes: list[pymupdf.Rect] = []

    def _jitter(self, pts):
        r = self.rng
        a1, a2 = r.uniform(0.02, 0.06), r.uniform(0.02, 0.05)
        f1, f2 = r.uniform(1.5, 4.0), r.uniform(3.0, 7.0)
        p1, p2 = r.uniform(0, 6.3), r.uniform(0, 6.3)
        n = max(1, len(pts) - 1)
        return [
            (x + a1 * math.sin(f1 * i / n * 6.28 + p1), y + a2 * math.sin(f2 * i / n * 6.28 + p2))
            for i, (x, y) in enumerate(pts)
        ]

    @staticmethod
    def _resample(pts, step):
        out = [pts[0]]
        carry = 0.0
        for a, b in itertools.pairwise(pts):
            seg = math.dist(a, b)
            if seg == 0:
                continue
            pos = step - carry
            while pos <= seg:
                t = pos / seg
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                pos += step
            carry = seg - (pos - step)
        if math.dist(out[-1], pts[-1]) > 0.2 * step:
            out.append(pts[-1])
        return out

    def _ribbon(self, centre):
        n = len(centre)
        phase = self.rng.uniform(0, 6.28)
        left, right = [], []
        for i in range(n):
            a, b = centre[max(0, i - 1)], centre[min(n - 1, i + 1)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / length, dx / length
            t = i / max(1, n - 1)
            taper = min(1.0, 0.35 + 3 * t, 0.35 + 3 * (1 - t))
            w = self.width * taper * (1 + 0.35 * math.sin(t * 5 + phase)) / 2
            x, y = centre[i]
            left.append((x + nx * w, y + ny * w))
            right.append((x - nx * w, y - ny * w))
        return left + right[::-1]

    def stroke(self, centre) -> None:
        if len(centre) < 2:
            return
        if self.style == "ribbon":
            outline = self._resample(self._ribbon(self._resample(centre, 2.0)), 2.0)
            if len(outline) < 4:
                return
            n = len(outline)
            for i in range(n):  # a closed Catmull-Rom spline through the outline
                p0, p1, p2, p3 = (outline[(i + k) % n] for k in (-1, 0, 1, 2))
                c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
                c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
                self.shape.draw_bezier(p1, c1, c2, p2)
            self.pending += 1
            if self.pending >= self.rng.randint(8, 60):
                self.flush()
        elif self.style == "segments":
            for a, b in itertools.pairwise(self._resample(centre, 0.8)):
                self.shape.draw_line(a, b)
                self.shape.finish(color=self.colour, width=self.width, lineCap=1, closePath=False)
        else:
            self.shape.draw_polyline(self._resample(centre, 0.9))
            self.shape.finish(color=self.colour, width=self.width, lineCap=1, lineJoin=1)

    def flush(self) -> None:
        if self.style == "ribbon" and self.pending:
            self.shape.finish(fill=self.colour, color=None, closePath=True)
            self.pending = 0

    def commit(self) -> None:
        self.flush()
        self.shape.commit()
        self.shape = self.page.new_shape()

    def word(self, text: str, x: float, base: float, em: float, slant: float) -> float:
        r = self.rng
        start, top, bottom = x, base, base
        for ch in text:
            strokes, advance = _letter(ch)
            scale = em * r.uniform(0.88, 1.12)
            lift = r.uniform(-0.6, 0.6)
            for s in strokes:
                pts = [
                    (x + (px + slant * py) * scale, base + lift - py * scale)
                    for px, py in self._jitter(s)
                ]
                top, bottom = min(top, *(p[1] for p in pts)), max(bottom, *(p[1] for p in pts))
                self.stroke(pts)
            x += advance * scale * r.uniform(0.95, 1.15)
        self.word_boxes.append(pymupdf.Rect(start, top, x, bottom))
        return x

    def lines(self, box: pymupdf.Rect, pitch: float, em: float, count: int) -> None:
        r = self.rng
        slant = r.uniform(0.05, 0.3)
        base = box.y0 + pitch
        for _ in range(count):
            if base > box.y1:
                break
            x = box.x0 + r.uniform(0, 3 * em)
            right = box.x1 - r.uniform(0, 0.3 * box.width)
            while x < right - 3 * em:
                word = "".join(r.choice(LETTERS) for _ in range(r.randint(1, 6)))
                x = self.word(word, x, base, em, slant) + em * r.uniform(0.9, 1.8)
            base += pitch
        self.commit()


def ruled(page: pymupdf.Page) -> None:
    """A ruled paper template: one stroked path of page-wide lines, and a margin line."""
    w, h = page.rect.width, page.rect.height
    shape = page.new_shape()
    for y in range(85, int(h) - 60, 22):
        shape.draw_line((0, y), (w, y))
    shape.finish(color=(0.36, 0.8, 1.0), width=0.5)
    shape.draw_line((70, 0), (70, h))
    shape.finish(color=(1.0, 0.48, 0.62), width=0.5)
    shape.commit()


# ----------------------------------------------------------------------------- text layers


def garbage(boxes, rng: random.Random) -> list[tuple[pymupdf.Rect, str]]:
    letters = "abcdefghiklmnoprstuvwyLEIZ"
    return [
        (b, "".join(rng.choice(letters) for _ in range(max(1, int(b.width / 6))))) for b in boxes
    ]


def text_layer(page: pymupdf.Page, words, mode: str) -> None:
    """Words as a recognition layer: "alpha0" (fill opacity 0, as iPad notes apps write
    it), "mode3" (render mode 3, as OCR programs write it) or "visible" (normal text, to
    be painted over by a page image)."""
    shape = page.new_shape()
    for rect, word in words:
        size = max(4.0, rect.height * 0.82)
        point = (rect.x0, rect.y1 - rect.height * 0.22)
        if mode == "mode3":
            shape.insert_text(point, word, fontsize=size, fontname="cour", render_mode=3)
        elif mode == "alpha0":
            shape.insert_text(point, word, fontsize=size, fontname="tiro", fill_opacity=0)
        else:
            shape.insert_text(point, word, fontsize=size, fontname="tiro", color=(0, 0, 0))
    shape.commit()


def rename_fonts(doc: pymupdf.Document, page: pymupdf.Page, basefont: str, resource: str) -> None:
    """Give the layer's base-14 font (`resource`: "tiro" or "cour", never the typed
    text's "helv") the name a recogniser writes (".SFUI-Regular", "GlyphLessFont")."""
    for xref, _ext, _type, _base, name, *_ in page.get_fonts(full=True):
        if name == resource:
            doc.xref_set_key(xref, "BaseFont", f"/{basefont}")


def typed(page: pymupdf.Page, top: float = 72, paragraphs=TYPED) -> list[tuple[pymupdf.Rect, str]]:
    """A heading and paragraphs of typed text; returns each line's box and words."""
    page.insert_text((72, top), paragraphs[0], fontsize=16)
    page.insert_textbox(
        pymupdf.Rect(72, top + 14, 520, top + 330), "\n\n".join(paragraphs[1:]), fontsize=11
    )
    return [(pymupdf.Rect(w[:4]), w[4]) for w in page.get_text("words")]


def scan_png(width: float, height: float, dpi: int = 90) -> tuple[bytes, list]:
    """A typed page rendered to a grey JPEG (a "scan"), and its words with their boxes."""
    src = pymupdf.open()
    page = src.new_page(width=width, height=height)
    words = typed(page)
    jpeg = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).tobytes("jpeg", jpg_quality=55)
    src.close()
    return jpeg, words


# ----------------------------------------------------------------------------- pages


def ios_page(doc: pymupdf.Document, seed: int, lines: int = 4, right: float = 400) -> None:
    """iPad notes: ribbon ink on ruled paper under an opacity-0 ".SFUI" layer."""
    page = doc.new_page(width=A4[0], height=A4[1])
    ruled(page)
    writer = Writer(page, seed, "ribbon", INK_BLUE)
    writer.lines(pymupdf.Rect(80, 70, right, 70 + 22 * lines), pitch=22, em=7, count=lines)
    text_layer(page, garbage(writer.word_boxes, random.Random(seed)), "alpha0")
    rename_fonts(doc, page, ".SFUI-Regular", "tiro")


def ink_page(doc: pymupdf.Document, seed: int, lines: int = 5) -> None:
    """Handwriting with no text layer: stroked polylines on ruled paper."""
    page = doc.new_page(width=A4[0], height=A4[1])
    ruled(page)
    Writer(page, seed, "polyline", INK_BLACK, width=0.9).lines(
        pymupdf.Rect(80, 70, 560, 70 + 22 * lines), pitch=22, em=7, count=lines
    )


def scan_page(doc: pymupdf.Document, *, ocr: str | None, stamp: bool = False) -> None:
    """A picture of a typed page: ocr None (no text), "mode3" (an invisible OCR layer in
    GlyphLessFont) or "under" (visible OCR text painted under the image, ABBYY style)."""
    jpeg, words = scan_png(*A4)
    page = doc.new_page(width=A4[0], height=A4[1])
    if ocr == "under":
        text_layer(page, words, "visible")
    page.insert_image(page.rect, stream=jpeg)
    if ocr == "mode3":
        text_layer(page, words, "mode3")
        rename_fonts(doc, page, "GlyphLessFont", "cour")
    if stamp:
        page.insert_text((36, 820), STAMP, fontsize=7)


def typed_page(doc: pymupdf.Document) -> pymupdf.Page:
    page = doc.new_page(width=A4[0], height=A4[1])
    typed(page)
    return page


def save(doc: pymupdf.Document, out: Path, name: str, producer: str = "a11y-stem fixtures") -> Path:
    doc.set_metadata(
        {
            "producer": producer,
            "creator": producer,
            "creationDate": FIXED_DATE,
            "modDate": FIXED_DATE,
        }
    )
    path = out / name
    doc.save(path, garbage=4, deflate=True, no_new_id=True)
    doc.close()
    return path


def _raw_doc(width: float = 612, height: float = 792):
    """A page whose content stream is written by hand (for operators PyMuPDF's drawing
    API does not write: render mode 7, an ExtGState alpha on an image)."""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    font = doc.get_new_xref()
    doc.update_object(
        font, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>"
    )
    return doc, page, font


def _text_ops(lines: list[str], x: float, y: float, size: float = 11, lead: float = 15) -> str:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    body = " ".join(f"({esc(line)}) Tj T*" for line in lines)
    return f"BT /F1 {size} Tf {lead} TL {x} {y} Td {body} ET"


def _set_content(doc, page, font: int, content: str, resources: str = "") -> None:
    doc.xref_set_key(page.xref, "Resources", f"<</Font<</F1 {font} 0 R>>{resources}>>")
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode("latin-1"))
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")


def _paragraph_lines() -> list[str]:
    words = " ".join(TYPED[1:]).split()
    lines, line = [], ""
    for word in words:
        if len(line) + len(word) > 80:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return [*lines, line]


# ----------------------------------------------------------------------------- files


def build(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    made = []

    # 1. iPad notes, every page handwritten with a hidden recognition layer (the shape of a real iPad lecture kept outside the repo).
    doc = pymupdf.open()
    ios_page(doc, 101)
    made.append(save(doc, out, "ios_notes.pdf", IOS))

    # 2. Handwriting with no text layer at all.
    doc = pymupdf.open()
    ink_page(doc, 201)
    made.append(save(doc, out, "ink_no_layer.pdf"))

    # 3. Scans: an invisible OCR layer; OCR text under the image; a stamp on top.
    doc = pymupdf.open()
    scan_page(doc, ocr="mode3")
    made.append(save(doc, out, "scan_ocr.pdf", "OCRmyPDF 16 / Tesseract"))
    doc = pymupdf.open()
    scan_page(doc, ocr="under")
    made.append(save(doc, out, "scan_text_under_image.pdf"))
    doc = pymupdf.open()
    scan_page(doc, ocr="mode3", stamp=True)
    made.append(save(doc, out, "scan_stamp_ocr.pdf"))

    # 4. A typed page with two lines of handwriting written under the text.
    doc = pymupdf.open()
    page = typed_page(doc)
    Writer(page, 401, "ribbon", INK_BLUE).lines(
        pymupdf.Rect(80, 470, 540, 540), pitch=26, em=8, count=2
    )
    made.append(save(doc, out, "typed_with_ink.pdf"))

    # 5. A typed page with words nobody sees: render mode 3, alpha 0, under a black box;
    #    and a visible title used as a clip (render mode 7) over coloured bands.
    doc, page, font = _raw_doc()
    gs = doc.get_new_xref()
    doc.update_object(gs, "<</Type/ExtGState/ca 0/CA 0>>")
    bands = " ".join(
        f"{0.1 + 0.08 * i:.2f} 0.2 {0.9 - 0.08 * i:.2f} rg 60 {700 + 5 * i} 480 5 re f"
        for i in range(10)
    )
    content = "\n".join(
        [
            f"q BT /F1 34 Tf 7 Tr 72 708 Td (Momentum and energy) Tj ET {bands} Q",
            "0 0 0 rg " + _text_ops(_paragraph_lines(), 72, 660),
            "q BT /F1 11 Tf 3 Tr 72 520 Td (Hidden phrase in render mode three for the index) Tj "
            "ET Q",
            "q /G0 gs 0 0 0 rg BT /F1 11 Tf 72 500 Td (Hidden phrase at zero opacity for the "
            "answer key) Tj ET Q",
            "0 0 0 rg " + _text_ops([ANSWER], 72, 470),
            "0 0 0 rg 66 462 330 18 re f",  # a "redaction" painted over the answer key
        ]
    )
    _set_content(doc, page, font, content, f"/ExtGState<</G0 {gs} 0 R>>")
    made.append(save(doc, out, "typed_hidden.pdf"))

    # 6. Typed text under see-through things: a transparent PNG with a few red marks, a
    #    photo at 30 % opacity, and a stencil mask. The text is visible on all three.
    doc = pymupdf.open()
    page = typed_page(doc)
    width, height = 306, 421
    overlay = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), True)
    alpha = bytearray(width * height)  # fully transparent ...
    rng = random.Random(6)
    for _ in range(5):  # ... but for a few red circles drawn by hand
        cx, cy, r = rng.randint(60, 240), rng.randint(40, 200), rng.randint(10, 25)
        for t in range(0, 360, 3):
            x = int(cx + r * math.cos(math.radians(t)))
            y = int(cy + 0.6 * r * math.sin(math.radians(t)))
            overlay.set_rect(pymupdf.IRect(x, y, x + 2, y + 2), (220, 30, 30, 255))
            for dy in range(2):
                alpha[(y + dy) * width + x : (y + dy) * width + x + 2] = b"\xff\xff"
    overlay.set_alpha(bytes(alpha))
    page.insert_image(page.rect, pixmap=overlay, overlay=True)
    page2_doc, page2, font = _raw_doc(*A4)
    img = page2_doc.get_new_xref()
    page2_doc.update_object(
        img,
        "<</Type/XObject/Subtype/Image/Width 20/Height 26/ColorSpace/DeviceGray"
        "/BitsPerComponent 8>>",
    )
    page2_doc.update_stream(img, bytes([120]) * (20 * 26))
    wash = page2_doc.get_new_xref()
    page2_doc.update_object(wash, "<</Type/ExtGState/ca 0.3/CA 0.3>>")
    mask = page2_doc.get_new_xref()
    page2_doc.update_object(
        mask, "<</Type/XObject/Subtype/Image/Width 16/Height 16/ImageMask true/BitsPerComponent 1>>"
    )
    page2_doc.update_stream(mask, bytes([0x0F, 0xFF] * 16))  # stripes: mostly unpainted
    content = "\n".join(
        [
            "0 0 0 rg " + _text_ops(_paragraph_lines(), 72, 760),
            f"q /G1 gs {A4[0]} 0 0 {A4[1] / 2:.2f} 0 {A4[1] / 2:.2f} cm /Im1 Do Q",
            "0 0 0 rg " + _text_ops(_paragraph_lines(), 72, 360),
            f"q 0.2 0.2 0.2 rg {A4[0]} 0 0 {A4[1] / 2:.2f} 0 0 cm /Im2 Do Q",
        ]
    )
    _set_content(
        page2_doc, page2, font, content,
        f"/ExtGState<</G1 {wash} 0 R>>/XObject<</Im1 {img} 0 R/Im2 {mask} 0 R>>",
    )  # fmt: skip
    doc.insert_pdf(page2_doc)
    page2_doc.close()
    made.append(save(doc, out, "overlays.pdf"))

    # 7. Photo slides: a short title over a photo that covers two thirds of the slide.
    doc = pymupdf.open()
    rng = random.Random(7)
    for title in ("Lab setup", "The air track", "Results"):
        slide = doc.new_page(width=720, height=405)
        slide.insert_text((40, 50), title, fontsize=28)
        photo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 150, 80), False)
        photo.clear_with(90)
        for _ in range(60):
            x, y = rng.randint(0, 140), rng.randint(0, 70)
            photo.set_rect(
                pymupdf.IRect(x, y, x + 10, y + 10), (rng.randint(0, 255), rng.randint(0, 255), 120)
            )
        slide.insert_image(pymupdf.Rect(60, 75, 660, 395), pixmap=photo)
    made.append(save(doc, out, "photo_slides.pdf"))

    # 8. Handwriting under a typed template header, with a hidden recognition layer.
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    ruled(page)
    page.insert_text((80, 50), HEADER, fontsize=9)
    writer = Writer(page, 801, "ribbon", INK_BLACK)
    writer.lines(pymupdf.Rect(80, 70, 400, 180), pitch=22, em=7, count=4)
    text_layer(page, garbage(writer.word_boxes, random.Random(801)), "alpha0")
    rename_fonts(doc, page, ".SFUI-Regular", "tiro")
    made.append(save(doc, out, "ink_header_layer.pdf", IOS))

    # 9. A typed page, then a page of machine-drawn marks filling the page and nothing to
    #    read: layout keeps nothing of it (a page-sized drawing is a border), so only the
    #    safety net keeps it, as an image with a decision.
    doc = pymupdf.open()
    typed_page(doc)
    page = doc.new_page(width=A4[0], height=A4[1])
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(20, 20, A4[0] - 20, A4[1] - 20))
    for row in range(36):
        for col in range(24):
            shape.draw_circle((40 + col * 22, 45 + row * 21.5), 4)
    shape.finish(color=(0.2, 0.2, 0.2), width=0.6)
    shape.commit()
    made.append(save(doc, out, "drawn_page.pdf"))

    # 10. A course document: typed, iPad notes, handwriting without a layer, a scan.
    doc = pymupdf.open()
    typed_page(doc)
    ios_page(doc, 1001, lines=3, right=320)
    ink_page(doc, 1002, lines=3)
    scan_page(doc, ocr="mode3")
    made.append(save(doc, out, "mixed_course.pdf"))
    made += build_review_cases(out)
    made += build_verify_cases(out)
    made += build_edge_cases(out)
    return made


# ----------------------------------------------------------------------------- review cases
# The routing review's failing cases (2026-10-09), rebuilt from made-up text and drawings.

PROBLEM = (
    "Problem 3. A ball is thrown upward with a speed of 12 m/s from a height of 2 m. Find "
    "the maximum height and the time it takes to reach the ground."
)
SECRET = "Grading note: Student 2291 (Jane Roe) has an accommodation for extra time."
HINT = "Hint: state the units of every quantity you compute."


def _secret_page(doc: pymupdf.Document) -> tuple[pymupdf.Page, pymupdf.Rect]:
    """A typed problem, the line to hide, and a typed hint; returns the line's box."""
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 140), PROBLEM, fontsize=11)
    page.insert_text((72, 300), SECRET, fontsize=11)
    page.insert_text((72, 340), HINT, fontsize=11)
    return page, pymupdf.Rect(66, 286, 520, 306)


def _bezier_through(shape, pts) -> None:
    """A smooth curve through `pts` (Catmull-Rom as cubic Beziers), as drawing tools write."""
    for i in range(len(pts) - 1):
        p0, p1, p2 = pts[max(i - 1, 0)], pts[i], pts[i + 1]
        p3 = pts[min(i + 2, len(pts) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        shape.draw_bezier(p1, c1, c2, p2)


def _coil(shape, x0: float, x1: float, y: float, loops: int, r: float) -> None:
    """An inductor's coil from x0 to x1, machine-drawn."""
    pts = []
    for k in range(loops * 12 + 1):
        t = k / (loops * 12)
        th = 2 * math.pi * loops * t
        pts.append((x0 + (x1 - x0) * t - r * 0.9 * math.sin(th), y + r * (1 - math.cos(th))))
    _bezier_through(shape, pts)


def _plot(page: pymupdf.Page, box: pymupdf.Rect) -> None:
    """A framed plot with a grid and three damped waves, filling `box`."""
    shape = page.new_shape()
    shape.draw_rect(box)
    shape.finish(color=(0, 0, 0), width=0.8)
    for k in range(1, 10):
        x = box.x0 + box.width * k / 10
        y = box.y0 + box.height * k / 10
        shape.draw_line((x, box.y0), (x, box.y1))
        shape.draw_line((box.x0, y), (box.x1, y))
    shape.finish(color=(0.8, 0.8, 0.8), width=0.3)
    for colour, (amp, waves) in zip(
        [(0.8, 0, 0), (0, 0, 0.8), (0, 0.5, 0)], [(0.4, 2), (0.25, 3), (0.15, 5)], strict=True
    ):
        pts = [
            (
                box.x0 + box.width * t / 200,
                (box.y0 + box.y1) / 2
                - box.height * amp * math.exp(-t / 150) * math.sin(waves * 2 * math.pi * t / 200),
            )
            for t in range(201)
        ]
        shape.draw_polyline(pts)
        shape.finish(color=colour, width=1.2)
    shape.commit()


def build_review_cases(out: Path) -> list[Path]:
    made = []

    # 11. Fake redactions drawn with annotation tools over a typed line (Acrobat or Preview
    #     shapes: a black box, a black highlight, a black text box, a white box), and a
    #     yellow highlight, which hides nothing.
    doc = pymupdf.open()
    styles = ("square_black", "highlight_black", "freetext_black", "square_white")
    for style in (*styles, "highlight_yellow"):
        page, box = _secret_page(doc)
        if style.startswith("square"):
            annot = page.add_rect_annot(box)
            colour = (0, 0, 0) if style.endswith("black") else (1, 1, 1)
            annot.set_colors(stroke=colour, fill=colour)
        elif style.startswith("freetext"):
            annot = page.add_freetext_annot(box, " ", fill_color=(0, 0, 0), text_color=(0, 0, 0))
        else:
            annot = page.add_highlight_annot(box)
            annot.set_colors(stroke=(0, 0, 0) if style.endswith("black") else (1, 1, 0))
        annot.update()
    made.append(save(doc, out, "annot_redactions.pdf"))

    # 12. Text nobody sees in three more ways: used as a clip with nothing painted through
    #     it, painted over by a shading, and painted over by thick white strokes.
    covers = [
        "q 0 0 0 rg BT /F1 11 Tf 7 Tr 72 300 Td ({secret}) Tj ET n Q",
        "0 0 0 rg BT /F1 11 Tf 72 300 Td ({secret}) Tj ET q 60 290 500 24 re W n /Sh1 sh Q",
        "0 0 0 rg BT /F1 11 Tf 72 300 Td ({secret}) Tj ET q 1 1 1 RG 16 w 60 303 m 560 303 l S Q",
    ]
    shading = (
        "/Shading<</Sh1<</ShadingType 2/ColorSpace/DeviceRGB/Coords[0 0 600 0]"
        "/Function<</FunctionType 2/Domain[0 1]/C0[1 1 1]/C1[0.9 0.9 0.9]/N 1>>>>>>"
    )
    doc = pymupdf.open()
    for cover in covers:
        part, page, font = _raw_doc()
        secret = SECRET.replace("(", "\\(").replace(")", "\\)")
        content = "0 0 0 rg " + _text_ops([PROBLEM[:80], PROBLEM[80:]], 72, 720)
        _set_content(part, page, font, content + "\n" + cover.format(secret=secret), shading)
        doc.insert_pdf(part)
        part.close()
    made.append(save(doc, out, "painted_over.pdf"))

    # 13. A scan whose CropBox trims the scanner's margins, with a short typed header: the
    #     visible page is all picture.
    jpeg, _ = scan_png(612, 792, dpi=72)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=jpeg)
    page.insert_text((70, 100), "PHYS 101 Week 3 reading", fontsize=10)
    page.set_cropbox(pymupdf.Rect(40, 50, 572, 742))
    made.append(save(doc, out, "cropped_scan.pdf"))

    # 14. A digital timing diagram with no text: square waves of horizontal and vertical
    #     lines only (not paper rules), and dashed time markers.
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    shape = page.new_shape()
    signals = [[0, 1] * 8, [0, 0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 0, 1, 1]]
    signals.append([0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 1, 0, 0, 0, 1])
    for row, bits in enumerate(signals):
        high, low = 200 + row * 60, 225 + row * 60
        x, pts = 120.0, []
        for bit in bits:
            y = high if bit else low
            if not pts or pts[-1][1] != y:
                pts.append((x, y))
            x += 25
            pts.append((x, y))
        shape.draw_polyline(pts)
        shape.finish(color=(0, 0, 0), width=1.2, closePath=False)
    for k in range(9):
        shape.draw_line((120 + 50 * k, 185), (120 + 50 * k, 400))
    shape.finish(color=(0.6, 0.6, 0.6), width=0.4, dashes="[2 2] 0")
    shape.commit()
    made.append(save(doc, out, "timing_diagram.pdf"))

    # 15. Machine-drawn diagrams under a short typed title: a circuit with five coils, and a
    #     slide of five different waves (one row of a few smooth curves).
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), "Coupled LC network", fontsize=16)
    shape = page.new_shape()
    x = 80.0
    for i, loops in enumerate([4, 5, 3, 6, 4]):
        shape.draw_line((x, 300), (x + 15, 300))
        shape.finish(color=(0, 0, 0), width=1)
        _coil(shape, x + 15, x + 15 + 10 * loops, 300, loops, 3.5 + 0.5 * i)
        shape.finish(color=(0, 0, 0), width=1, closePath=False)
        x += 15 + 10 * loops
        shape.draw_line((x, 300), (x + 15, 300))
        shape.draw_line((x + 15, 290), (x + 15, 310))
        shape.draw_line((x + 19, 290), (x + 19, 310))
        shape.finish(color=(0, 0, 0), width=1)
        x += 22
    shape.draw_polyline([(x, 300), (x, 360), (80, 360), (80, 300)])  # the return wire
    shape.finish(color=(0, 0, 0), width=1, closePath=False)
    shape.commit()
    page = doc.new_page(width=960, height=540)
    page.insert_text((60, 70), "Wave types", fontsize=28)
    waves = [
        lambda t: math.sin(6 * math.pi * t),
        lambda t: math.exp(-3 * t) * math.sin(10 * math.pi * t),
        lambda t: math.sin(8 * math.pi * t) * math.sin(math.pi * t),
        lambda t: math.sin(2 * math.pi * (2 + 6 * t) * t),
        lambda t: 0.5 * math.sin(4 * math.pi * t) + 0.5 * math.sin(9 * math.pi * t),
    ]
    for i, wave in enumerate(waves):
        shape = page.new_shape()
        left, amp = 70 + i * 170, 18 + 3 * i
        _bezier_through(shape, [(left + 85 * k / 80, 280 - amp * wave(k / 80)) for k in range(81)])
        shape.finish(color=(0, 0.2, 0.7), width=1.5, closePath=False)
        shape.commit()
    made.append(save(doc, out, "diagram_slides.pdf"))

    # 16. Machine vector content (the same word shapes stamped again and again, as text
    #     turned into outlines is) under an invisible search layer (render mode 3), as an
    #     OCR program writes over a vector page with no text layer.
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    stamp = pymupdf.open()
    sheet = stamp.new_page(width=A4[0], height=A4[1])
    writer = Writer(sheet, 1601, "ribbon", INK_BLACK)
    for i, word in enumerate(("table", "ends", "lost", "sea")):
        writer.word(word, 80 + 120 * i, 60, 8, 0.1)
    writer.commit()
    with pymupdf.open("pdf", stamp.tobytes()) as source:
        for row in range(12):
            for col in range(2):
                left = 70 + 120 * ((row + col) % 4)
                top = 90 + 52 * row
                page.show_pdf_page(
                    pymupdf.Rect(80 + 230 * col, top, 200 + 230 * col, top + 50),
                    source,
                    0,
                    clip=pymupdf.Rect(left, 30, left + 120, 80),
                )
    stamp.close()
    Writer(page, 1602, "ribbon", INK_BLACK).lines(  # one line in other words: no repeats
        pymupdf.Rect(80, 720, 520, 750), pitch=22, em=8, count=1
    )
    words = [
        (pymupdf.Rect(80, 100 + 52 * r, 520, 120 + 52 * r), "table ends lost sea")
        for r in range(12)
    ]
    text_layer(page, words, "mode3")
    made.append(save(doc, out, "vector_with_layer.pdf", "OCRmyPDF 16 / Tesseract"))

    # 17. Writing on ruled paper that the ink checks reject (one line written twice, stroke
    #     for stroke: exact repeats) under a short typed title, and the same under a long
    #     title.
    doc = pymupdf.open()
    for seed, title in ((1701, "Lecture 14 notes"),
                        (1702, "MATH 2413 Lecture 14: regular languages, pumping")):  # fmt: skip
        page = doc.new_page(width=A4[0], height=A4[1])
        shape = page.new_shape()  # paper ruled from top to bottom
        for y in range(64, int(A4[1]) - 20, 22):
            shape.draw_line((0, y), (A4[0], y))
        shape.finish(color=(0.36, 0.8, 1.0), width=0.5)
        shape.commit()
        page.insert_text((80, 50), title, fontsize=14, fontname="helv")
        for i, line_seed in enumerate((seed, seed, seed + 10, seed + 20)):
            top = 70 + 22 * i
            Writer(page, line_seed, "polyline", INK_BLACK, width=0.9).lines(
                pymupdf.Rect(80, top, 560, top + 22), pitch=22, em=7, count=1
            )
    made.append(save(doc, out, "ink_titled.pdf"))

    # 18. Drawings that fill the page: a typed problem with a plot on a page with a drawn
    #     border, a full-page plot with a caption, and a slide with a full-bleed chart.
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(24, 24, 588, 768))
    shape.finish(color=(0, 0, 0), width=1.5)
    shape.commit()
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 160), PROBLEM, fontsize=11)
    shape = page.new_shape()
    shape.draw_line((200, 350), (400, 350))
    shape.draw_line((200, 350), (200, 200))
    shape.finish(color=(0, 0, 0), width=1)
    pts = [(200 + t * 2, 350 - (2 + 12 * t / 40 - 4.9 * (t / 40) ** 2) * 14) for t in range(101)]
    shape.draw_polyline([(x, y) for x, y in pts if y <= 350])
    shape.finish(color=(0.8, 0, 0), width=1.5)
    shape.commit()
    page.insert_text((290, 368), "time t (s)", fontsize=9)
    page.insert_text((150, 270), "h (m)", fontsize=9)
    page.insert_text((200, 390), "Figure 1: height of the ball against time.", fontsize=10)
    page = doc.new_page(width=612, height=792)
    _plot(page, pymupdf.Rect(30, 40, 582, 752))
    page.insert_text((40, 60), "Damped oscillation x(t) for three damping ratios", fontsize=12)
    page.insert_text((40, 780), "Figure 2: displacement against time (s).", fontsize=9)
    page = doc.new_page(width=960, height=540)
    page.insert_text((60, 40), "Results: damped oscillators", fontsize=24)
    _plot(page, pymupdf.Rect(55, 50, 900, 515))
    made.append(save(doc, out, "big_drawings.pdf"))

    # 19. Pages with nothing to read: a blank slide of a template (a background fill and a
    #     coloured band), a page with only a drawn frame, then a typed slide.
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=405)
    page.draw_rect(page.rect, color=None, fill=(0.95, 0.95, 1.0))
    page.draw_rect(pymupdf.Rect(0, 0, 720, 40), color=None, fill=(0.1, 0.2, 0.5))
    page = doc.new_page(width=720, height=405)
    page.draw_rect(pymupdf.Rect(20, 20, 700, 385), color=(0, 0, 0), width=2)
    page = doc.new_page(width=720, height=405)
    page.insert_text((40, 60), "Average velocity", fontsize=28)
    page.insert_textbox(pymupdf.Rect(40, 90, 680, 300), TYPED[2], fontsize=16)
    made.append(save(doc, out, "template_pages.pdf"))

    # 20. A title slide: white typed text on a photo that fills the slide, nothing hidden.
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=405)
    photo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 144, 81), False)
    photo.clear_with(40)
    rng = random.Random(20)
    for _ in range(40):
        x, y = rng.randint(0, 134), rng.randint(0, 71)
        photo.set_rect(pymupdf.IRect(x, y, x + 10, y + 10), (rng.randint(0, 120), 60, 140))
    page.insert_image(page.rect, pixmap=photo)
    page.insert_text((60, 200), "Lecture 3: Kinematics", fontsize=36, color=(1, 1, 1))
    made.append(save(doc, out, "title_slide.pdf"))
    return made


# ----------------------------------------------------------------------------- verify cases
# The final verifier's failing cases (2026-10-12), rebuilt from made-up text and drawings.

GRAPH = PROBLEM + " The graph below shows its height against time."
WORKSHEET = "Homework 3: show all your working and state the units of every answer."


def _note(page: pymupdf.Page, seed: int, words: str, style: str = "polyline", base=150.0):
    """A short handwritten note of a few pen strokes on one line; returns the Writer."""
    writer = Writer(page, seed, style, INK_BLACK, width=1.0)
    x = 90.0
    for word in words.split():
        x = writer.word(word, x, base, 9, 0.15) + 12
    writer.commit()
    return writer


def _ball_plot(page: pymupdf.Page) -> None:
    """A height-time plot: axes, the curve, six markers, axis labels and a caption."""
    shape = page.new_shape()
    shape.draw_line((200, 350), (400, 350))
    shape.draw_line((200, 350), (200, 200))
    shape.finish(color=(0, 0, 0), width=1)
    pts = [(200 + t * 2, 350 - (2 + 12 * t / 40 - 4.9 * (t / 40) ** 2) * 14) for t in range(101)]
    pts = [(x, y) for x, y in pts if y <= 350]
    shape.draw_polyline(pts)
    shape.finish(color=(0.8, 0, 0), width=1.5)
    for x, y in pts[::20]:
        shape.draw_circle((x, y), 2)
        shape.finish(color=(0, 0, 0.6), fill=(0, 0, 0.6))
    shape.commit()
    page.insert_text((290, 368), "time t (s)", fontsize=9)
    page.insert_text((150, 270), "h (m)", fontsize=9)
    page.insert_text((200, 390), "Figure 1: height of the ball against time.", fontsize=10)


def _frame(page: pymupdf.Page, style: str) -> None:
    """A decorative page frame: rounded corners, a double frame in one path, a frame with
    corner ticks (eight lines in one path), a frame with corner curls, or a light filled
    panel with a 30 pt margin."""
    shape = page.new_shape()
    if style == "rounded":
        shape.draw_rect(pymupdf.Rect(24, 24, 588, 768), radius=0.04)
    elif style == "double":
        shape.draw_rect(pymupdf.Rect(24, 24, 588, 768))
        shape.draw_rect(pymupdf.Rect(28, 28, 584, 764))
    elif style == "ticks":
        corners = [(24, 24), (588, 24), (588, 768), (24, 768)]
        for a, b in itertools.pairwise([*corners, corners[0]]):
            shape.draw_line(a, b)
        for y in (30, 762):
            shape.draw_line((20, y), (28, y))
            shape.draw_line((582, y), (592, y))
    elif style == "curls":
        shape.draw_rect(pymupdf.Rect(24, 24, 588, 768))
        for centre in ((30, 30), (582, 30), (30, 762), (582, 762)):
            shape.draw_circle(centre, 4)
    else:  # "panel"
        shape.draw_rect(pymupdf.Rect(30, 30, 582, 762))
        shape.finish(color=(0.2, 0.2, 0.2), fill=(0.96, 0.96, 0.96), width=1)
        shape.commit()
        return
    shape.finish(color=(0, 0, 0), width=1.2)
    shape.commit()


def _single_curve_page(doc: pymupdf.Document, seed: int) -> None:
    """iPad-style notes in which every letter is one stroked curve (a single Bezier, too
    few points for the stroke test), under an opacity-0 ".SFUI" recognition layer."""
    rng = random.Random(seed)
    page = doc.new_page(width=612, height=792)
    ruled(page)
    boxes = []
    for row in range(8):
        y = 128 + row * 60
        x = 80 + rng.uniform(0, 20)
        while x < 500:
            shape, x0 = page.new_shape(), x
            for _ in range(rng.randint(2, 7)):
                w, h = rng.uniform(6, 10), rng.uniform(9, 16)
                shape.draw_bezier(
                    (x, y),
                    (x + rng.uniform(-2, w), y - rng.uniform(0, h)),
                    (x + rng.uniform(0, w + 3), y - rng.uniform(0, h)),
                    (x + w, y + rng.uniform(-3, 3)),
                )
                shape.finish(color=(0.05, 0.1, 0.35), width=rng.uniform(1.0, 1.6))
                x += w + rng.uniform(0.5, 2.5)
            shape.commit()
            boxes.append(pymupdf.Rect(x0, y - 16, x, y + 3))
            x += rng.uniform(10, 22)
    text_layer(page, garbage(boxes, rng), "alpha0")
    rename_fonts(doc, page, ".SFUI-Regular", "tiro")


def build_verify_cases(out: Path) -> list[Path]:
    made = []

    # 21. A short handwritten note under a short typed title: five strokes ("note ab"),
    #     three strokes ("note") and segments written one path each (Xournal++).
    doc = pymupdf.open()
    for seed, title, words, style in (
        (2101, "Week 6 notes", "note ab", "polyline"),
        (2102, "Week 7 notes", "note", "polyline"),
        (2103, "Week 5 notes, Oct 3", "note ab", "segments"),
    ):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 70), title, fontsize=14)
        _note(page, seed, words, style)
    made.append(save(doc, out, "note_titled.pdf"))

    # 22. The same short note with an opacity-0 recognition layer over it.
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    ruled(page)
    page.insert_text((72, 60), "Week 5, Oct 3", fontsize=12)
    writer = _note(page, 2201, "note ab")
    text_layer(page, garbage(writer.word_boxes, random.Random(2201)), "alpha0")
    rename_fonts(doc, page, ".SFUI-Regular", "tiro")
    made.append(save(doc, out, "note_titled_layer.pdf", IOS))

    # 23. A typed problem with a plot on pages with decorative frames: rounded corners, a
    #     double frame drawn as one path, corner ticks, corner curls, a filled panel.
    doc = pymupdf.open()
    for style in ("rounded", "double", "ticks", "curls", "panel"):
        page = doc.new_page(width=612, height=792)
        _frame(page, style)
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 160), GRAPH, fontsize=11)
        _ball_plot(page)
    made.append(save(doc, out, "framed_plots.pdf"))

    # 24. iPad-style notes whose letters are single curves, under a hidden layer; and pages
    #     of handwriting each under a short typed title, with no hidden layer.
    doc = pymupdf.open()
    _single_curve_page(doc, 2401)
    _single_curve_page(doc, 2402)
    made.append(save(doc, out, "ios_single_curves.pdf", IOS))
    doc = pymupdf.open()
    for seed, title in ((2411, "Week 5, Oct 3"), (2412, "Week 5, Oct 5")):
        ink_page(doc, seed, lines=3)
        doc[-1].insert_text((80, 50), title, fontsize=12)
    made.append(save(doc, out, "ink_titled_pages.pdf"))

    # 25. A typed worksheet page with a short handwritten note under its text.
    doc = pymupdf.open()
    page = typed_page(doc)
    page.insert_text((72, 430), WORKSHEET, fontsize=11)
    _note(page, 2501, "see eq note", base=480)
    made.append(save(doc, out, "typed_with_note.pdf"))
    return made


# ----------------------------------------------------------------------------- edge cases
# The second final verifier's cases (2026-10-12): axes and rulers read as page furniture or
# as plain rules, rebuilt from made-up text and drawings.

QUESTIONS = [
    "1. A cart of mass 2 kg moves at 3 m/s on a level track. Find its kinetic energy.",
    "2. The cart then climbs a ramp. How high does it go before it stops?",
    "3. Repeat question 2 for a cart of twice the mass and explain the result.",
]


def _axes_plot(page: pymupdf.Page, rect: pymupdf.Rect, *, one_path: bool = False) -> None:
    """A plot filling `rect`: an x-axis along its foot and a y-axis up its left side (two
    lines, or one L-shaped path), one sine curve, tick labels "0" to "4" under the x-axis
    and the axis title "v" at the top of the y-axis."""
    r = pymupdf.Rect(rect)
    origin = (r.x0 + 20, r.y1 - 15)
    shape = page.new_shape()
    if one_path:
        shape.draw_polyline([(r.x0 + 20, r.y0), origin, (r.x1, r.y1 - 15)])
        shape.finish(color=(0, 0, 0), width=0.8, closePath=False)
    else:
        shape.draw_line(origin, (r.x1, r.y1 - 15))
        shape.finish(color=(0, 0, 0), width=0.8)
        shape.draw_line(origin, (r.x0 + 20, r.y0))
        shape.finish(color=(0, 0, 0), width=0.8)
    pts = []
    for i in range(80):
        t = i / 79
        pts.append((r.x0 + 22 + t * (r.width - 24),
                    r.y0 + (r.height - 20) * (0.5 - 0.4 * math.sin(2 * math.pi * t))))  # fmt: skip
    shape.draw_polyline(pts)
    shape.finish(color=(0.1, 0.3, 0.8), width=1.0, closePath=False)
    shape.commit()
    for i in range(5):
        page.insert_text((r.x0 + 20 + i * (r.width - 25) / 4, r.y1 - 4), str(i), fontsize=7)
    page.insert_text((r.x0 + 2, r.y0 + 8), "v", fontsize=8)


def _open_axes_slide(page: pymupdf.Page, *, title: bool, labels: bool, gap: float) -> None:
    """A slide's plot whose two axes are one stroked path of two lines, the second starting
    away from the first ("80 75 m 640 75 l 80 335 m 80 75 l S": MuPDF's box of the path
    leaves that start out), a straight line from the axes up, and tick labels."""
    if title:
        page.insert_text((40, 40), "Speed of the cart against time", fontsize=20)
    shape = page.new_shape()
    shape.draw_line((80, 330), (640, 330))
    shape.draw_line((80, 70), (80, 330))
    shape.finish(color=(0, 0, 0), width=1, closePath=False)
    pts = [(80 + gap + (560 - gap) * t / 100, 310 - 220 * t / 100) for t in range(101)]
    shape.draw_polyline(pts)
    shape.finish(color=(0.1, 0.3, 0.8), width=2.0, closePath=False)
    shape.commit()
    if labels:
        for i in range(6):
            page.insert_text((80 + 112 * i - 3, 344), str(2 * i), fontsize=10)


def _timeline(page: pymupdf.Page, *, one_path: bool) -> None:
    """A timeline across the top of a page: a rule within its top 15 % with seven tick
    marks across it (one path, or each line its own path) and a year under each tick,
    under a typed title and above typed questions."""
    page.insert_text((72, 60), "Timeline of physics", fontsize=14)
    shape = page.new_shape()
    shape.draw_line((30, 90), (582, 90))
    if not one_path:
        shape.finish(color=(0, 0, 0), width=1.2)
    for i in range(7):
        x = 30 + i * 92
        shape.draw_line((x, 84), (x, 96))
        if not one_path:
            shape.finish(color=(0, 0, 0), width=1.2)
    if one_path:
        shape.finish(color=(0, 0, 0), width=1.2)
    shape.commit()
    for i in range(7):
        page.insert_text((30 + i * 92 - 12, 110), str(1900 + 20 * i), fontsize=9)
    for i, line in enumerate(QUESTIONS):
        page.insert_text((72, 200 + 22 * i), line, fontsize=11)


def build_edge_cases(out: Path) -> list[Path]:
    made = []

    # 26. Full-page plots of one curve: with a frame round the page (the y-axis, near the
    #     page's edge and nearly as tall, was taken for a side of the frame), without it,
    #     with axes long enough to fill the page (two lines, one L-shaped path).
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(18.36, 18.36, 593.64, 773.64))
    shape.finish(color=(0.2, 0.2, 0.5), width=2)
    shape.commit()
    _axes_plot(page, pymupdf.Rect(40, 40, 572, 752))
    page = doc.new_page(width=612, height=792)
    _axes_plot(page, pymupdf.Rect(40, 40, 572, 752))
    page = doc.new_page(width=612, height=792)
    _axes_plot(page, pymupdf.Rect(20, 30, 600, 770))
    page = doc.new_page(width=612, height=792)
    _axes_plot(page, pymupdf.Rect(20, 30, 600, 770), one_path=True)
    made.append(save(doc, out, "full_page_plots.pdf"))

    # 27. Slides whose axes are one path of two separate lines: with a title and tick
    #     labels; with neither, the line starting 10 pt right of the y-axis.
    doc = pymupdf.open()
    _open_axes_slide(doc.new_page(width=720, height=405), title=True, labels=True, gap=0)
    _open_axes_slide(doc.new_page(width=720, height=405), title=False, labels=False, gap=10)
    made.append(save(doc, out, "open_axes.pdf"))

    # 28. A timeline across the top of a page (one path; separate lines), and a control:
    #     a running header over a rule across the top of a typed page.
    doc = pymupdf.open()
    _timeline(doc.new_page(width=612, height=792), one_path=True)
    _timeline(doc.new_page(width=612, height=792), one_path=False)
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 50), "PHYS 101 Mechanics, week 3", fontsize=9)
    shape = page.new_shape()
    shape.draw_line((30, 58), (582, 58))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()
    for i, line in enumerate(QUESTIONS):
        page.insert_text((72, 120 + 22 * i), line, fontsize=11)
    made.append(save(doc, out, "timelines.pdf"))

    # 29. One or two hand marks over lines of typed text, too simple for the stroke test: a
    #     tick over question 2, a cross over a word of a paragraph's middle line, a slash
    #     through a word; and a control, marks typeset with the text (an underline, a box
    #     round a word, a cancel stroke across a term within its line).
    doc = pymupdf.open()
    red, blue = (0.85, 0, 0), (0.0, 0.44, 1.0)
    for strokes in (
        [[(250, 116), (256, 124), (270, 106)]],
        [[(250, 102), (264, 118)], [(264, 102), (250, 118)]],
        [[(160, 128), (170, 120.5), (182, 110)]],
    ):
        page = doc.new_page(width=612, height=792)
        if len(strokes) == 2:
            page.insert_textbox(pymupdf.Rect(72, 90, 540, 300), " ".join(TYPED[1:3]), fontsize=11)
        else:
            for i, line in enumerate(QUESTIONS):
                page.insert_text((72, 100 + 22 * i), line, fontsize=11)
        shape = page.new_shape()
        for points in strokes:
            shape.draw_polyline(points)
            shape.finish(color=red if len(strokes) < 3 else blue, width=1.6, closePath=False)
        shape.commit()
    page = doc.new_page(width=612, height=792)
    for i, line in enumerate(QUESTIONS):
        page.insert_text((72, 100 + 22 * i), line, fontsize=11)
    shape = page.new_shape()
    shape.draw_line((72, 104), (160, 104))  # an underline under question 1
    shape.finish(color=(0, 0, 0), width=0.6)
    shape.draw_rect(pymupdf.Rect(130, 112, 162, 125))  # a box round a word of question 2
    shape.finish(color=(0, 0, 0), width=0.6)
    shape.draw_line((150, 145), (162, 136))  # a cancel stroke across a term of question 3
    shape.finish(color=(0, 0, 0), width=0.6)
    shape.commit()
    made.append(save(doc, out, "marks_over_text.pdf"))
    return made


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    for path in build(target):
        print(f"{path.name}: {path.stat().st_size // 1024} KB")
