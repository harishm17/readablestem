"""A vector-ink handwriting dev set: MathWriting expressions drawn as a notes app exports them.

`handwriting_set` composes MathWriting ink into scanned raster pages, but an iPad exports
handwriting as vector paths, and the pipeline's stroke reader (`pipeline.ink_strokes`) only
sees those. This module builds the matching dev set (docs/eval-handwriting.md, "A vector-ink
dev set"): the same kind of expression-level ink, drawn into PDFs with real paths, in the three
ways `ink_strokes` reads:

- `path`: one stroked polyline per stroke, with a round pen;
- `samples`: one small filled disc per pen sample (the GoodNotes style);
- `outline`: one filled outline per stroke (the PencilKit style).

A unit is two pages. Units cycle through the three styles, and through plain and ruled paper
(ruled: pale rules and a margin line, as page furniture to be dropped); some pages carry a
short typed header. There is no text layer under the ink, so `page_type` calls such a page
`handwritten`. Each expression is on a line of its own (display); there are no typed sentences.

Everything is deterministic from the seed, the ink files and PyMuPDF, so a rebuilt set has the
same `rebuild_hash`. The ink is CC BY-NC-SA 4.0 (Google LLC): the built pages, the gold and the
manifest live outside the repository (`~/.cache/readablestem/handwriting-vector/`); only this
code is committed. No model is called.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from readablestem.eval import handwriting_set as hw

RECIPE_VERSION = 1
STYLES = ("path", "samples", "outline")
PAGE_PT = (612.0, 792.0)  # US Letter, points
PT_TO_PX = 300 / 72  # `Slot.box` is in 300 dpi pixels, as the raster set's
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 54.0, 60.0, 72.0
ROW_GAP = 26.0
MAX_ROW_H = 96.0
HEADER_SIZE = 20.0
PAGES_PER_UNIT = 2
ROWS_PER_PAGE = 8
LETTER_PT = (15.0, 21.0)  # the writer's letter height, drawn this tall
PEN_PT = (1.2, 2.2)
RULE_COLOUR = (0.74, 0.81, 0.91)
MARGIN_COLOUR = (0.89, 0.63, 0.63)
RULE_SPACING = 24.0
DEFAULT_UNITS = 6  # one full cycle: every style on plain and on ruled paper
PART = "vector"
Pt = tuple[float, float]


@dataclass(frozen=True)
class Drawn:
    """One expression as drawn: where it is and what it took."""

    sample_id: str
    page: int  # 1-based
    box: tuple[float, float, float, float]  # points
    strokes: int
    display: bool = True


@dataclass(frozen=True)
class UnitSpec:
    unit_id: str
    style: str
    ruled: bool
    header: str | None
    pen: str


# ------------------------------------------------------------------------------ drawing


def _dedupe(points: Sequence[Pt]) -> list[Pt]:
    out: list[Pt] = []
    for p in points:
        if not out or math.dist(out[-1], p) > 1e-6:
            out.append(p)
    return out


def _dense(points: Sequence[Pt], step: float) -> list[Pt]:
    """The polyline cut into samples about `step` apart (the ends are kept)."""
    out = [points[0]]
    for a, b in itertools.pairwise(points):
        n = max(1, math.ceil(math.dist(a, b) / step))
        out.extend(
            (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1)
        )
    return out


def _ribbon(points: Sequence[Pt], width: float) -> list[Pt]:
    """A pen's outline round a centre-line: the left edge out, the right edge back."""
    dense = _dense(points, 1.5)
    left, right = [], []
    for i, (x, y) in enumerate(dense):
        a, b = dense[max(0, i - 1)], dense[min(len(dense) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length * width / 2, dx / length * width / 2
        left.append((x + nx, y + ny))
        right.append((x - nx, y - ny))
    return left + right[::-1]


def _disc(centre: Pt, radius: float, sides: int = 12) -> list[Pt]:
    return [
        (centre[0] + radius * math.cos(2 * math.pi * i / sides),
         centre[1] + radius * math.sin(2 * math.pi * i / sides))
        for i in range(sides)
    ]  # fmt: skip


def draw_stroke(
    shape: pymupdf.Shape, points: Sequence[Pt], style: str, pen: float, colour: tuple[float, ...]
) -> None:
    """One stroke in one of the three ways note apps write ink."""
    pts = _dedupe(points)
    if not pts:
        return
    if style == "path":
        if len(pts) == 1:  # a dot: a path of no length, drawn by the round cap
            pts = [pts[0], (pts[0][0] + 0.05, pts[0][1])]
        shape.draw_polyline([pymupdf.Point(*p) for p in pts])
        shape.finish(color=colour, fill=None, width=pen, closePath=False, lineCap=1, lineJoin=1)
    elif style == "samples":
        for p in _dense(pts, 0.8 * pen) if len(pts) > 1 else pts:
            shape.draw_circle(pymupdf.Point(*p), 0.6 * pen)
            shape.finish(fill=colour, color=None)
    elif style == "outline":
        outline = _ribbon(pts, pen) if len(pts) > 1 else _disc(pts[0], pen / 2)
        shape.draw_polyline([pymupdf.Point(*p) for p in outline])
        shape.finish(fill=colour, color=None, closePath=True)
    else:
        raise ValueError(f"unknown ink style {style!r}")


def _ruled(page: pymupdf.Page) -> None:
    w, h = PAGE_PT
    shape = page.new_shape()
    y = MARGIN_TOP + RULE_SPACING
    while y < h - 36:
        shape.draw_line(pymupdf.Point(0, y), pymupdf.Point(w, y))
        y += RULE_SPACING
    shape.finish(color=RULE_COLOUR, width=0.6)
    shape.draw_line(pymupdf.Point(MARGIN_X - 18, 0), pymupdf.Point(MARGIN_X - 18, h))
    shape.finish(color=MARGIN_COLOUR, width=0.6)
    shape.commit()


# ------------------------------------------------------------------------------ composing


def unit_spec(unit_id: str, number: int, seed: str) -> UnitSpec:
    """Style, paper and header of unit `number` (1-based). The cycle is fixed, so a set of six
    units has every style on plain and on ruled paper; the pen colour and the header words are
    seeded."""
    rng = random.Random(f"{seed}|{unit_id}")
    i = number - 1
    header = f"{rng.choice(hw.HEADERS)} {rng.randint(1, 9)}" if i % 2 == 0 else None
    pen = rng.choice(sorted(hw.INKS))
    return UnitSpec(unit_id, STYLES[i % len(STYLES)], (i // len(STYLES)) % 2 == 1, header, pen)


def _place(ink: hw.Ink, letter_pt: float, max_w: float, max_h: float) -> tuple[float, float]:
    """Scale (ink units to points) and pen width: the ink's letters are about `letter_pt`
    tall, shrunk to fit the box."""
    x0, y0, x1, y1 = ink.bbox
    scale = min(
        letter_pt / ink.letter_height, max_w / max(x1 - x0, 1e-9), max_h / max(y1 - y0, 1e-9)
    )
    pen = min(PEN_PT[1], max(PEN_PT[0], 0.075 * ink.letter_height * scale))
    return scale, pen


def compose_unit(
    spec: UnitSpec, pool: hw.Pool, seed: str
) -> tuple[pymupdf.Document, list[Drawn], list[hw.Candidate]]:
    """Draw one unit: the document and what is where, in reading order."""
    rng = random.Random(f"{seed}|{spec.unit_id}|layout")
    colour = tuple(c / 255 for c in hw.INKS[spec.pen])
    doc = pymupdf.open()
    drawn: list[Drawn] = []
    used: list[hw.Candidate] = []
    for page_no in range(1, PAGES_PER_UNIT + 1):
        page = doc.new_page(width=PAGE_PT[0], height=PAGE_PT[1])
        if spec.ruled:
            _ruled(page)
        y = MARGIN_TOP
        if page_no == 1 and spec.header:
            page.insert_text(
                (MARGIN_X, y + HEADER_SIZE), spec.header, fontname="hebo", fontsize=HEADER_SIZE
            )
            y += HEADER_SIZE + 30
        shape = page.new_shape()
        for _ in range(ROWS_PER_PAGE):
            if y > PAGE_PT[1] - MARGIN_BOTTOM - MAX_ROW_H:
                break
            item = pool.take(inline=False)
            ink = item.ink
            body = PAGE_PT[0] - 2 * MARGIN_X
            scale, pen = _place(ink, rng.uniform(*LETTER_PT), body, MAX_ROW_H)
            x0, y0, x1, y1 = ink.bbox
            w, h = (x1 - x0) * scale, (y1 - y0) * scale
            left = (
                MARGIN_X + (body - w) / 2 if rng.random() < 0.4 else MARGIN_X + rng.uniform(0, 30)
            )
            left = min(left, PAGE_PT[0] - MARGIN_X - w)
            top = y + 6
            for stroke in ink.strokes:
                pts = [(left + (px - x0) * scale, top + (py - y0) * scale) for px, py in stroke]
                draw_stroke(shape, pts, spec.style, pen, colour)
            drawn.append(
                Drawn(ink.sample_id, page_no, (left, top, left + w, top + h), len(ink.strokes))
            )
            used.append(item)
            y += h + ROW_GAP + 12
        shape.commit()
    return doc, drawn, used


def page_content_hash(pdf: bytes) -> str:
    """SHA-256 of what is on the pages (each page's drawing operations and its text). The PDF
    container carries a document id that differs between two builds of one set."""
    digest = hashlib.sha256()
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        for page in doc:
            digest.update(page.read_contents())
            digest.update(page.get_text().encode())
    finally:
        doc.close()
    return digest.hexdigest()


def _slots(spec: UnitSpec, drawn: Sequence[Drawn], used: Sequence[hw.Candidate]) -> hw.UnitPlan:
    plan = hw.UnitPlan(spec.unit_id, spec.pen, spec.ruled, spec.header or "")
    for d, item in zip(drawn, used, strict=True):
        x0, y0, x1, y1 = (round(v * PT_TO_PX) for v in d.box)
        plan.slots.append(
            hw.Slot(
                d.sample_id,
                item.ink.split,
                item.ink.label,
                d.display,
                d.page,
                None,
                (x0, y0, x1, y1),
            )
        )
    return plan


# ------------------------------------------------------------------------------- the set


def used_samples(manifests: Sequence[Path], raw_root: Path) -> tuple[set[str], set[str]]:
    """Sample ids in the registered manifests, and their labels (read again from the ink in
    `raw_root`/{split}), so the dev set shares no expression with a registered part."""
    ids: set[str] = set()
    for path in manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        for part in data["parts"].values():
            for unit in part["units"]:
                ids.update(x["sample_id"] for x in unit["expressions"])
    labels: set[str] = set()
    for sample_id in sorted(ids):
        for split in ("train", "valid", "test"):
            file = raw_root / split / f"{sample_id}.inkml"
            if file.is_file():
                labels.add(hw.read_ink(file).label)
                break
    return ids, labels


def pick_candidates(
    ink_dir: Path, seed: str, size: int, *, exclude_ids: set[str], exclude_labels: set[str]
) -> dict[str, hw.Candidate]:
    """The first `size` usable expressions of `ink_dir`, in the seeded order, one per label."""
    order = hw.ranked(seed, ink_dir.name, [p.stem for p in ink_dir.glob("*.inkml")])
    found: dict[str, hw.Candidate] = {}
    seen = set(exclude_labels)
    for sample_id in order:
        if sample_id in exclude_ids:
            continue
        try:
            ink = hw.read_ink(ink_dir / f"{sample_id}.inkml")
        except (hw.InkError, ValueError):
            continue
        item = hw.candidate(ink)
        if item is not None and ink.label not in seen:
            found[sample_id] = item
            seen.add(ink.label)
        if len(found) >= size:
            break
    return found


def _canonical(data: object) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def build_set(
    candidates: dict[str, hw.Candidate], out_dir: Path, *, seed: str, units: int = DEFAULT_UNITS
) -> dict:
    """Build the units into `out_dir` (`pdfs/UNIT.pdf`, `UNIT.jsonl`, `manifest.json`) and
    return the manifest. `rebuild_hash` is over everything the set is made of: building again
    from the same ink and seed gives the same value."""
    pool = hw.Pool(list(candidates.values()))
    (out_dir / "pdfs").mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    n_expressions = 0
    for number in range(1, units + 1):
        spec = unit_spec(f"hw-vec-{number:02d}", number, seed)
        doc, drawn, used = compose_unit(spec, pool, seed)
        try:
            pdf = doc.tobytes(deflate=True)
        finally:
            doc.close()
        content = page_content_hash(pdf)
        name = f"pdfs/{spec.unit_id}.pdf"
        (out_dir / name).write_bytes(pdf)
        plan = _slots(spec, drawn, used)
        records = hw.gold_records(plan, candidates, part=PART, pdf_name=name, pdf_sha256=content)
        for record, d in zip(records, drawn, strict=True):
            record["ink_style"] = spec.style
            record["book_title"] = "Handwritten mathematics (MathWriting ink drawn as vector paths)"
            record["strokes"] = d.strokes
        text = "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records) + "\n"
        (out_dir / f"{spec.unit_id}.jsonl").write_text(text, encoding="utf-8")
        n_expressions += len(drawn)
        entries.append(
            {
                "unit": spec.unit_id,
                "pages": PAGES_PER_UNIT,
                "style": spec.style,
                "ruled": spec.ruled,
                "typed_header": spec.header is not None,
                "pen": spec.pen,
                "pdf_content_sha256": content,
                "gold_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "expressions": [
                    {
                        "id": r["id"],
                        "sample_id": r["sample_id"],
                        "inkml_sha256": candidates[r["sample_id"]].ink.sha256,
                        "page": d.page,
                        "box_pt": [round(v, 2) for v in d.box],
                        "strokes": d.strokes,
                        "tokens": r["tokens"],
                    }
                    for r, d in zip(records, drawn, strict=True)
                ],
            }
        )
    manifest = {
        "part": PART,
        "builder": "eval/handwriting_vector.py",
        "recipe_version": RECIPE_VERSION,
        "seed": seed,
        "source": {
            "name": "MathWriting (Google LLC)",
            "url": hw.SOURCE_URL,
            "license": hw.LICENSE_NAME,
        },
        "recipe": {
            "page_pt": list(PAGE_PT),
            "pages_per_unit": PAGES_PER_UNIT,
            "rows_per_page": ROWS_PER_PAGE,
            "styles": list(STYLES),
            "letter_pt": list(LETTER_PT),
            "pen_pt": list(PEN_PT),
            "pymupdf": pymupdf.VersionBind,
        },
        "units": entries,
        "n_units": len(entries),
        "n_pages": units * PAGES_PER_UNIT,
        "n_expressions": n_expressions,
    }
    manifest["rebuild_hash"] = hashlib.sha256(_canonical(manifest).encode()).hexdigest()
    (out_dir / "manifest.json").write_text(_canonical(manifest), encoding="utf-8")
    return manifest
