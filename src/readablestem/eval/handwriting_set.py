"""A handwriting answer key: real human ink with known LaTeX, composed into scanned pages.

The image page pass (ADR 0007) was measured on synthetic scans of printed pages and by eye on
four handwritten ones. This module builds the missing set (docs/eval-handwriting.md): ink from
Google's MathWriting dataset, written by people copying a rendered LaTeX expression, so that every
expression has a label that is what the writer was asked to write. The ink is drawn at pen width,
composed into pages (display expressions on lines of their own, inline expressions inside short
typed sentences), degraded as a scanner degrades (`eval.scan`) and set in an image-only PDF.

The data is licensed CC BY-NC-SA 4.0 (Google LLC), so none of it is committed: the repository holds
this code and a manifest of identifiers and hashes (`scripts/handwriting_eval.py`).
Everything is deterministic from the seed, the ink files and the font file, so a rebuilt page is
the same bytes and a recorded model answer replays.

The gold records are those of the document task, as `gold/arxiv_gold` writes them for papers: one
JSONL file per part, MathML made by the pipeline's own LaTeX path, so that a prediction in LaTeX
and the label are compared like for like. A unit is a small PDF of two pages.
"""

from __future__ import annotations

import hashlib
import io
import random
import re
import statistics
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from readablestem.eval import scan
from readablestem.gold.arxiv_gold import GoldError, to_mathml, token_count

NS = "{http://www.w3.org/2003/InkML}"
PAGE_W, PAGE_H = 2550, 3300  # US Letter at 300 dpi
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 260, 260, 300
BODY_W = PAGE_W - 2 * MARGIN_X
SUPERSAMPLE = 3
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
FONT_BOLD_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
TEXT_PX = 54
BLOCKS_PER_PAGE = 7
PAGES_PER_UNIT = 2
INKS = {  # name: RGB of the pen
    "black": (24, 24, 28),
    "blue": (28, 40, 118),
    "graphite": (72, 72, 78),
}
LICENSE_NAME = "CC BY-NC-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
SOURCE_URL = "https://github.com/google-research/google-research/tree/master/mathwriting"

# Typed words around inline expressions. {} is a slot for one handwritten expression.
SENTENCES_1 = (
    "Hence {} for every admissible value.",
    "Substituting gives {} after simplifying.",
    "Recall that {} from the previous lecture.",
    "The result is {} and nothing else.",
    "We use {} in the next step.",
    "Therefore {} as required.",
    "Check the units of {} before moving on.",
)
SENTENCES_2 = (
    "Since {} we obtain {} directly.",
    "Write {} and then {} to finish.",
    "Both {} and {} hold here.",
    "Compare {} with {} in the same line.",
    "If {} then {} follows.",
)
HEADERS = ("Problem set", "Practice sheet", "Lecture notes", "Worked examples", "Review questions")
LABELS = ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "(a)", "(b)", "(c)", "(d)")

_WORD = re.compile(r"[A-Za-z]{3,}")
_COMMAND = re.compile(r"\\[A-Za-z]+")
_TEXTLIKE = re.compile(r"\\(?:mathrm|operatorname)\s*\{[^{}]*\}")
_ENV = re.compile(r"\\(?:begin|end)\s*\{[A-Za-z*]+\}")
_INLINE_BAN = re.compile(r"\\(?:begin|end|sum|prod|int|iint|oint|lim|bigcup|bigcap)")


class InkError(ValueError):
    pass


@dataclass(frozen=True)
class Ink:
    """One handwritten expression: strokes as (x, y) points, y down; and its label."""

    sample_id: str
    split: str
    label: str
    strokes: tuple[tuple[tuple[float, float], ...], ...]
    sha256: str

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [x for s in self.strokes for x, _ in s]
        ys = [y for s in self.strokes for _, y in s]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def letter_height(self) -> float:
        """The median size of a stroke's box: a stand-in for the writer's letter size, which
        differs by device and by writer."""
        sizes = []
        for stroke in self.strokes:
            ys = [y for _, y in stroke]
            xs = [x for x, _ in stroke]
            height, width = max(ys) - min(ys), max(xs) - min(xs)
            if max(height, width) > 0:
                sizes.append(max(height, width * 0.5))
        return statistics.median(sizes) if sizes else 1.0


def read_ink(path: Path) -> Ink:
    """Parse one MathWriting InkML file (human ink only)."""
    raw = path.read_bytes()
    root = ET.fromstring(raw)
    notes = {a.get("type", ""): (a.text or "") for a in root.findall(NS + "annotation")}
    if notes.get("inkCreationMethod") != "human":
        raise InkError("not human ink")
    strokes = []
    for trace in root.findall(NS + "trace"):
        points = []
        for chunk in (trace.text or "").split(","):
            numbers = chunk.split()
            if len(numbers) >= 2:
                points.append((float(numbers[0]), float(numbers[1])))
        if points:
            strokes.append(tuple(points))
    if len(strokes) < 2:
        raise InkError("fewer than two strokes")
    label = notes.get("normalizedLabel") or ""
    if not label:
        raise InkError("no label")
    return Ink(
        sample_id=notes.get("sampleId", path.stem),
        split=notes.get("splitTagOriginal", ""),
        label=label,
        strokes=tuple(strokes),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def label_problem(label: str) -> str | None:
    """Why a label is not used as an answer key (None when it is): long, text mode, or words
    spelled out in plain letters (a writer copied `sqrt(2)` as four letters)."""
    if len(label) > 150:
        return "long"
    if "\\text" in label or "\\mbox" in label or "\\hbox" in label:
        return "text mode"
    bare = _COMMAND.sub("", _ENV.sub("", _TEXTLIKE.sub("", label)))
    if _WORD.search(bare):
        return "letters spelled out"
    return None


@dataclass(frozen=True)
class Candidate:
    ink: Ink
    mathml_inline: str
    mathml_display: str
    tokens: int
    inline_ok: bool


def candidate(ink: Ink) -> Candidate | None:
    """A usable expression, with its MathML for both roles; None when it is not usable."""
    if label_problem(ink.label) is not None:
        return None
    try:
        display = to_mathml(ink.label, True)
        inline = to_mathml(ink.label, False)
    except GoldError:
        return None
    tokens = token_count(display)
    if not 3 <= tokens <= 40:
        return None
    x0, y0, x1, y1 = ink.bbox
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return None
    inline_ok = (
        _INLINE_BAN.search(ink.label) is None
        and tokens <= 16
        and width / height >= 1.4
        and height / ink.letter_height <= 3.4
    )
    return Candidate(ink, inline, display, tokens, inline_ok)


def ranked(seed: str, split: str, sample_ids: Sequence[str]) -> list[str]:
    """Sample ids in the seeded order that picks the set (SHA-256 of seed, split and id)."""
    return sorted(
        sample_ids, key=lambda i: hashlib.sha256(f"{seed}|{split}|{i}".encode()).hexdigest()
    )


# ================================================================================ drawing


@dataclass
class Placed:
    mask: Image.Image  # 255 where there is ink
    size: tuple[int, int]


def place_ink(ink: Ink, letter_px: float, *, max_w: int, max_h: int) -> Placed:
    """The ink drawn so that its letters are about `letter_px` tall, shrunk to fit the box."""
    x0, y0, x1, y1 = ink.bbox
    scale = min(letter_px / ink.letter_height, max_w / (x1 - x0), max_h / (y1 - y0))
    pen = min(8.0, max(4.0, 0.075 * ink.letter_height * scale))
    pad = int(pen) + 2
    width = int((x1 - x0) * scale) + 2 * pad
    height = int((y1 - y0) * scale) + 2 * pad
    big = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 0)
    draw = ImageDraw.Draw(big)
    radius = pen * SUPERSAMPLE / 2
    for stroke in ink.strokes:
        points = [
            (((x - x0) * scale + pad) * SUPERSAMPLE, ((y - y0) * scale + pad) * SUPERSAMPLE)
            for x, y in stroke
        ]
        if len(points) > 1:
            draw.line(points, fill=255, width=max(1, round(radius * 2)), joint="curve")
        for px, py in (points[0], points[-1]):
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=255)
    return Placed(big.resize((width, height), Image.Resampling.LANCZOS), (width, height))


# =============================================================================== composing


@dataclass
class Slot:
    """One expression on one page of a unit: where it is and what role it plays."""

    sample_id: str
    split: str
    label: str
    display: bool
    page: int  # 1-based, within the unit
    sentence: str | None  # the typed words around an inline expression ({} marks slots)
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 on the 300 dpi page


@dataclass
class UnitPlan:
    unit_id: str
    pen: str
    ruled: bool
    header: str
    slots: list[Slot] = field(default_factory=list)


class Pool:
    """Candidates in the seeded order; each is used at most once."""

    def __init__(self, candidates: Sequence[Candidate]) -> None:
        self.items = list(candidates)
        self.used: set[str] = set()

    def take(self, *, inline: bool) -> Candidate:
        for item in self.items:
            if (item.inline_ok or not inline) and item.ink.sample_id not in self.used:
                self.used.add(item.ink.sample_id)
                return item
        raise InkError("the pool is empty")

    def give_back(self, item: Candidate) -> None:
        self.used.discard(item.ink.sample_id)


def _font(bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD_PATH if bold else FONT_PATH), TEXT_PX)


def _ruled(page: Image.Image) -> None:
    draw = ImageDraw.Draw(page)
    for y in range(MARGIN_TOP + 60, PAGE_H - 120, 108):
        draw.line((0, y, PAGE_W, y), fill=(188, 206, 232), width=3)
    draw.line((MARGIN_X - 70, 0, MARGIN_X - 70, PAGE_H), fill=(226, 160, 160), width=3)


def _paste(page: Image.Image, placed: Placed, x: int, y: int, colour: tuple[int, int, int]) -> None:
    page.paste(Image.new("RGB", placed.size, colour), (x, y), placed.mask)


def compose_unit(
    unit_id: str, pool: Pool, seed: str, *, pages: int = PAGES_PER_UNIT
) -> tuple[list[Image.Image], UnitPlan]:
    """Draw the pages of one unit (clean, 300 dpi) and the plan of what is where."""
    rng = random.Random(f"{seed}|{unit_id}")
    pen = rng.choice(sorted(INKS))
    header = f"{rng.choice(HEADERS)} {rng.randint(1, 9)}"
    plan = UnitPlan(unit_id, pen, rng.random() < 1 / 3, header)
    colour = INKS[pen]
    font, bold = _font(), _font(True)
    label_no = 0
    images: list[Image.Image] = []
    for page_no in range(1, pages + 1):
        page = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
        if plan.ruled:
            _ruled(page)
        draw = ImageDraw.Draw(page)
        y = MARGIN_TOP
        if page_no == 1:
            draw.text((MARGIN_X, y), plan.header, font=bold, fill=(20, 20, 20))
            y += 220
        for _ in range(BLOCKS_PER_PAGE):
            if y > PAGE_H - MARGIN_BOTTOM - 520:
                break
            if rng.random() < 0.55:
                made = _sentence(page, draw, font, pool, rng, colour, y)
                if made is None:
                    continue
                slots, used = made
            else:
                label = LABELS[label_no % len(LABELS)] if rng.random() < 0.6 else None
                label_no += 1
                slot, used = _display(page, draw, font, pool, rng, colour, y, label)
                slots = [slot]
            for slot in slots:
                slot.page = page_no
            plan.slots.extend(slots)
            y += used
        images.append(page)
    return images, plan


def _display(
    page: Image.Image,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    pool: Pool,
    rng: random.Random,
    colour: tuple[int, int, int],
    y: int,
    label: str | None,
) -> tuple[Slot, int]:
    item = pool.take(inline=False)
    left = MARGIN_X + (190 if label else 0)
    placed = place_ink(item.ink, rng.uniform(62, 86), max_w=PAGE_W - MARGIN_X - left, max_h=480)
    width, height = placed.size
    centred = label is None or (width < 0.5 * BODY_W and rng.random() < 0.5)
    x = (PAGE_W - width) // 2 if centred else left
    top = y + 90
    if label:
        draw.text((MARGIN_X, top + height // 2 - TEXT_PX // 2), label, font=font, fill=(20, 20, 20))
    _paste(page, placed, x, top, colour)
    box = (x, top, x + width, top + height)
    return Slot(
        item.ink.sample_id, item.ink.split, item.ink.label, True, 0, None, box
    ), height + 180


def _sentence(
    page: Image.Image,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    pool: Pool,
    rng: random.Random,
    colour: tuple[int, int, int],
    y: int,
) -> tuple[list[Slot], int] | None:
    n = 1 if rng.random() < 0.45 else 2
    template = rng.choice(SENTENCES_1 if n == 1 else SENTENCES_2)
    parts = template.split("{}")
    picks = [pool.take(inline=True) for _ in range(n)]
    letter = rng.uniform(44, 56)
    placed = [place_ink(c.ink, letter, max_w=900, max_h=210) for c in picks]
    text_w = sum(draw.textlength(p, font=font) for p in parts)
    gaps = 60 * n
    room = BODY_W - text_w - gaps
    ink_w = sum(p.size[0] for p in placed)
    if ink_w > room:  # shrink the ink to fit the line, never the words
        factor = room / ink_w
        if factor < 0.6:
            for c in picks:
                pool.give_back(c)
            return None
        placed = [place_ink(c.ink, letter * factor, max_w=900, max_h=210) for c in picks]
    line_h = max(max(p.size[1] for p in placed), TEXT_PX + 20) + 70
    centre = y + line_h // 2
    x = MARGIN_X
    slots = []
    for i, part in enumerate(parts):
        draw.text((x, centre - TEXT_PX // 2 - 6), part, font=font, fill=(20, 20, 20))
        x += int(draw.textlength(part, font=font))
        if i < n:
            x += 30
            top = centre - placed[i].size[1] // 2
            _paste(page, placed[i], x, top, colour)
            box = (x, top, x + placed[i].size[0], top + placed[i].size[1])
            ink = picks[i].ink
            slots.append(Slot(ink.sample_id, ink.split, ink.label, False, 0, template, box))
            x += placed[i].size[0] + 30
    return slots, line_h + 60


# ============================================================================== PDF and gold


def unit_pdf(images: Sequence[Image.Image], seed: int) -> tuple[bytes, list[scan.Degradation]]:
    """The image-only PDF of a scan of the pages (the recipe of `eval.scan`, seeded)."""
    clean = pymupdf.open()
    try:
        for image in images:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            sheet = clean.new_page(width=612, height=792)
            sheet.insert_image(sheet.rect, stream=buffer.getvalue())
        printed = clean.tobytes(deflate=True)
    finally:
        clean.close()
    return scan.scan_pdf(printed, seed)


def pdf_content_sha256(pdf: bytes) -> str:
    """SHA-256 of what is on the pages (each page's picture, in order). The PDF container
    carries a random document id, so its own bytes differ between two builds of one set."""
    digest = hashlib.sha256()
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        for page in doc:
            for image in page.get_images():
                digest.update(doc.extract_image(image[0])["image"])
    finally:
        doc.close()
    return digest.hexdigest()


def gold_records(
    plan: UnitPlan,
    candidates: dict[str, Candidate],
    *,
    part: str,
    pdf_name: str,
    pdf_sha256: str,
) -> list[dict]:
    """The gold records of one unit, in reading order (page by page, top to bottom)."""
    records = []
    for index, slot in enumerate(plan.slots):
        cand = candidates[slot.sample_id]
        records.append(
            {
                "book_slug": "handwriting",
                "book_title": "Handwritten mathematics (MathWriting ink on composed pages)",
                "page_slug": plan.unit_id,
                "page_title": plan.header,
                "id": f"handwriting/{plan.unit_id}/eq-{index:03d}",
                "kind": "equation",
                "display": "block" if slot.display else "inline",
                "latex": slot.label,
                "mathml": cand.mathml_display if slot.display else cand.mathml_inline,
                "index_on_page": index,
                "tokens": cand.tokens,
                "classes": [],
                "in_float": False,
                "pdf": pdf_name,
                "pdf_page": slot.page,
                "pdf_sha256": pdf_sha256,
                "source": "mathwriting",
                "sample_id": slot.sample_id,
                "split": slot.split,
                "part": part,
                "pen": plan.pen,
                "ruled": plan.ruled,
                "sentence": slot.sentence,
                "license_name": LICENSE_NAME,
                "license_url": LICENSE_URL,
                "source_url": SOURCE_URL,
            }
        )
    return records
