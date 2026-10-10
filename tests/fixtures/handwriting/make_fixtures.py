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
a clip-mode title, photo slides, a scan with a visible stamp, ink under a typed header).

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
    path object) or "polyline" (one stroked path per stroke)."""

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

    # 1. iPad notes, every page handwritten with a hidden recognition layer (lec14's shape).
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
    return made


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    for path in build(target):
        print(f"{path.name}: {path.stat().st_size // 1024} KB")
