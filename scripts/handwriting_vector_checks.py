"""Measure the stroke checks of the image page pass on a vector-ink dev set ($0, no model).

    uv run python scripts/handwriting_vector_checks.py DIR [DIR ...] [--seed S] [--jitter PT]

DIR is a folder built by `handwriting_accuracy.py build-vector` (outside the repository: the ink
is CC BY-NC-SA). No model is asked, so the readings are the gold LaTeX of each expression with
errors put in by this script, and the boxes of the blocks are the drawn boxes moved about:

- case: one letter of the gold (c o s u v w x z, small or capital) written in the other
  case. The control is the gold itself;
- script: a superscript or subscript taken out of the gold, one put in after a letter or digit,
  and one moved from raised to lowered (or back). The control is the gold itself;
- omission: one block left out of a page's blocks, the rest with every edge moved by up to
  `--jitter` points, the way a reader's boxes sit off the ink. The control is the page with no
  block left out. The same variants are given to the raster check (`image_page_omission` on a
  render of the page) and to the raster check on a pale render of the page (the pen at 30%
  strength), which is what pastel ink looks like to a threshold.

Recall is the share of injected errors that the check names (for the right symbol or code where
it can say). False alarms are the share of controls it names. Precision is given at one error to
one control, and the counts are printed so any other mix can be worked out.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
from collections import Counter
from pathlib import Path

import pymupdf

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from readablestem.pipeline import image_page, ink_strokes  # noqa: E402
from readablestem.pipeline import image_page_omission as omission  # noqa: E402
from readablestem.pipeline import image_page_vector as iv  # noqa: E402

CASE_ALL = iv.CASE_LETTERS
STYLE = frozenset({"text", "mathrm", "mathbf", "mathit", "mathcal", "mathbb", "operatorname"})


# ------------------------------------------------------------------------ the data


def load(folder: Path):
    """[(page vector, [(box_pt, latex)])] for every page of the set."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    pages = []
    for unit in manifest["units"]:
        gold = {}
        for line in (folder / f"{unit['unit']}.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            gold[record["id"]] = record["latex"]
        pdf = folder / "pdfs" / f"{unit['unit']}.pdf"
        doc = pymupdf.open(pdf)
        try:
            for number in sorted({e["page"] for e in unit["expressions"]}):
                page = doc[number - 1]
                ink = ink_strokes.extract_ink(page)
                rect = page.rect
                vec = iv.VectorPage(
                    number, tuple(ink.strokes), (rect.x0, rect.y0, rect.x1, rect.y1)
                )
                items = [
                    (tuple(e["box_pt"]), gold[e["id"]])
                    for e in unit["expressions"]
                    if e["page"] == number
                ]
                png = page.get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72)).tobytes("png")
                pages.append((vec, items, png))
        finally:
            doc.close()
    return pages


def strokes_of(vec, box):
    height = box[3] - box[1]
    return iv.glyphs(vec.strokes_in(box, pad=4.0 + 0.2 * height))


# ---------------------------------------------------------------------- injections


def _toks(latex: str) -> list[str]:
    return iv.tokens(latex)


def _plain_letters(toks: list[str]) -> list[int]:
    """Indices of single letters outside \\text-like groups, not after a ^ or _ (level 0 only
    when the group is not a script: checked by the reading itself)."""
    out = []
    depth_style: list[int] = []
    depth = 0
    for i, t in enumerate(toks):
        if t == "{":
            depth += 1
            if i > 0 and toks[i - 1].startswith("\\") and toks[i - 1][1:] in STYLE:
                depth_style.append(depth)
        elif t == "}":
            if depth_style and depth_style[-1] == depth:
                depth_style.pop()
            depth -= 1
        elif len(t) == 1 and t.isalpha() and not depth_style:
            out.append(i)
    return out


def case_flips(latex: str):
    """[(variant, index of the symbol that changed)] for each letter that has two forms."""
    toks = _toks(latex)
    base = iv.reading(latex)
    if base is None:
        return []
    out = []
    for i in _plain_letters(toks):
        t = toks[i]
        if t.lower() not in CASE_ALL:
            continue
        flipped = [*toks[:i], t.swapcase(), *toks[i + 1 :]]
        # the index among symbols: letters before it that make atoms (all single tokens here)
        variant = " ".join(flipped)
        read = iv.reading(variant)
        if read is None or len(read.atoms) != len(base.atoms):
            continue
        idx = next(
            (k for k, (a, b) in enumerate(zip(base.atoms, read.atoms, strict=True)) if a != b), None
        )
        if idx is not None:
            out.append((variant, idx))
    return out


def _group_end(toks: list[str], i: int) -> int:
    """Index after the argument starting at toks[i]."""
    if toks[i] != "{":
        return i + 1
    depth = 0
    for j in range(i, len(toks)):
        if toks[j] == "{":
            depth += 1
        elif toks[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(toks)


def script_variants(latex: str):
    """{kind: [variants]} for a script taken out (raised or lowered), put in, or moved."""
    toks = _toks(latex)
    out: dict[str, list[str]] = {"lost raised": [], "lost lowered": [], "added": [], "moved": []}
    for i, t in enumerate(toks):
        if t in ("^", "_") and i + 1 < len(toks):
            end = _group_end(toks, i + 1)
            if any(x.isalnum() for x in toks[i + 1 : end]):  # marks and signs are too small to see
                out["lost raised" if t == "^" else "lost lowered"].append(
                    " ".join(toks[:i] + toks[end:])
                )
                other = "_" if t == "^" else "^"
                out["moved"].append(" ".join([*toks[:i], other, *toks[i + 1 :]]))
        elif (
            i > 0
            and len(t) == 1
            and (t.isalpha() or t.isdigit())
            and len(toks[i - 1]) == 1
            and (toks[i - 1].isalpha() or toks[i - 1].isdigit())
            and (i + 1 >= len(toks) or toks[i + 1] not in ("^", "_"))
        ):
            # a plain symbol after a plain symbol read as its superscript: the glyph is there,
            # the reading puts it up
            out["added"].append(" ".join([*toks[:i], "^", "{", t, "}", *toks[i + 1 :]]))
    return out


# ------------------------------------------------------------------------ measures


def rate(n: int, d: int) -> str:
    return f"{n}/{d} = {n / d:.2f}" if d else "0/0"


def precision(tp: int, tp_n: int, fp: int, fp_n: int) -> str:
    """Precision with one error for one control: recall / (recall + false-alarm rate)."""
    if not tp_n or not fp_n:
        return "n/a"
    r, f = tp / tp_n, fp / fp_n
    return f"{r / (r + f):.2f}" if r + f else "n/a"


def run_case(pages, rng: random.Random):
    flips = Counter()
    hits = Counter()
    controls = Counter()
    false_alarms = Counter()
    by_letter: dict[str, list[int]] = {}
    for vec, items, _ in pages:
        refs = []
        prepared = []
        for box, latex in items:
            gs = strokes_of(vec, box)
            read = iv.reading(latex)
            if not gs or read is None or len(read.atoms) != len(gs):
                prepared.append(None)
                continue
            own = iv.own_heights(read, gs)
            refs.append(own)
            prepared.append((gs, read, own, latex))
        page = iv._page_heights([type("E", (), {"own": r}) for r in refs])
        for item in prepared:
            if item is None:
                continue
            gs, read, own, latex = item
            letters = [
                a for a in read.atoms if a.plain and a.kind == "letter" and a.ch.lower() in CASE_ALL
            ]
            if not letters:
                continue
            kind = "own" if own else ("page" if page else "none")
            if kind == "none":
                controls["unmeasurable"] += 1
                continue
            controls[kind] += 1
            if iv.case_doubts(read, gs, page):
                false_alarms[kind] += 1
            for variant, idx in case_flips(latex):
                vread = iv.reading(variant)
                letter = vread.atoms[idx].ch.lower()
                hit = idx in iv.case_doubts(vread, gs, page)
                flips[kind] += 1
                hits[kind] += hit
                s = by_letter.setdefault(letter, [0, 0])
                s[0] += hit
                s[1] += 1
    return flips, hits, controls, false_alarms, by_letter


def run_script(pages):
    counts = {k: Counter() for k in ("lost raised", "lost lowered", "added", "moved")}
    lost = iv.SCRIPT_LOST
    expect = {
        "lost raised": lost,
        "lost lowered": lost,
        "added": iv.SCRIPT_ADDED,
        "moved": iv.SCRIPT_MOVED,
    }
    control = Counter()
    for vec, items, _ in pages:
        for box, latex in items:
            gs = strokes_of(vec, box)
            read = iv.reading(latex)
            if not gs or read is None or read.stacked or len(gs) < 2:
                continue
            control["n"] += 1
            events = iv.script_events(read, gs)
            control["flagged"] += bool(events)
            for code in events:
                control[code] += 1
            for kind, variants in script_variants(latex).items():
                for variant in variants[:3]:
                    vread = iv.reading(variant)
                    if vread is None or vread.stacked:
                        continue
                    ev = iv.script_events(vread, gs)
                    counts[kind]["n"] += 1
                    counts[kind]["hit"] += expect[kind] in ev
                    counts[kind]["any"] += bool(ev)
    return counts, control, expect


def jittered(boxes, rng, jx, jy):
    return [
        (
            x0 + rng.uniform(-jx, jx),
            y0 + rng.uniform(-jy, jy),
            x1 + rng.uniform(-jx, jx),
            y1 + rng.uniform(-jy, jy),
        )
        for x0, y0, x1, y1 in boxes
    ]


def to_units(vec, box):
    fx0, fy0, fx1, fy1 = vec.frame
    w, h = fx1 - fx0, fy1 - fy0
    return (
        round((box[0] - fx0) / w * 1000),
        round((box[1] - fy0) / h * 1000),
        round((box[2] - fx0) / w * 1000),
        round((box[3] - fy0) / h * 1000),
    )


def overlaps(region, units) -> bool:
    return not (
        region.x1 < units[0] or region.x0 > units[2] or region.y1 < units[1] or region.y0 > units[3]
    )


def pale(png: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        gray = image.convert("L").point(lambda v: 255 - round((255 - v) * 0.3))
        out = io.BytesIO()
        gray.save(out, format="PNG")
        return out.getvalue()


def run_omission(pages, rng: random.Random, jitter: float):
    tally = {name: Counter() for name in ("vector", "raster", "raster_pale")}
    for vec, items, png in pages:
        boxes = [b for b, _ in items]
        inks = {"raster": image_page.Ink(png), "raster_pale": image_page.Ink(pale(png))}
        for drop in [None, *range(len(boxes))]:
            kept = [b for i, b in enumerate(boxes) if i != drop]
            moved = jittered(kept, rng, jitter * 0.75, jitter)
            units = [to_units(vec, b) for b in moved]
            units = [(max(0, a), max(0, b), min(1000, c), min(1000, d)) for a, b, c, d in units]
            found = {
                "vector": iv.leftover(vec, moved),
                "raster": omission.find(inks["raster"].mask, units),
                "raster_pale": omission.find(inks["raster_pale"].mask, units),
            }
            target = None if drop is None else to_units(vec, boxes[drop])
            for name, regions in found.items():
                t = tally[name]
                if target is None:
                    t["controls"] += 1
                    t["false_alarms"] += bool(regions)
                else:
                    t["dropped"] += 1
                    t["found"] += any(overlaps(r, target) for r in regions)
                    t["named_elsewhere"] += any(not overlaps(r, target) for r in regions)
    return tally


# -------------------------------------------------------------------------- report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folders", nargs="+", help="built vector sets, measured together")
    parser.add_argument("--seed", default="checks")
    parser.add_argument("--jitter", type=float, default=14.0, help="box error, points (vertical)")
    args = parser.parse_args()
    rng = random.Random(args.seed)
    pages = []
    for name in args.folders:
        folder = Path(name).expanduser()
        if REPO in (folder.resolve(), *folder.resolve().parents):
            raise SystemExit("the vector set is CC BY-NC-SA ink: keep it outside the repository")
        pages += load(folder)
    n_expr = sum(len(items) for _, items, _ in pages)
    n_strokes = sum(len(v.strokes) for v, _, _ in pages)
    print(f"{len(pages)} pages, {n_expr} expressions, {n_strokes} strokes")

    flips, hits, controls, alarms, by_letter = run_case(pages, rng)
    print("\nCASE (flips = one letter in the other case; controls = the gold reading)")
    for kind in ("own", "page"):
        print(
            f"  heights from {kind}: recall {rate(hits[kind], flips[kind])}; "
            f"false alarms {rate(alarms[kind], controls[kind])}; "
            f"precision at 1:1 {precision(hits[kind], flips[kind], alarms[kind], controls[kind])}"
        )
    print(f"  controls with no height to measure against: {controls['unmeasurable']}")
    for letter, (h, n) in sorted(by_letter.items()):
        print(f"    {letter}: {rate(h, n)}")

    counts, control, expect = run_script(pages)
    print("\nSCRIPT (controls = the gold reading)")
    print(f"  controls {control['n']}; any flag {rate(control['flagged'], control['n'])}", end="")
    print("; by code " + ", ".join(f"{c} {control[c]}" for c in sorted(set(expect.values()))))
    for kind, c in counts.items():
        print(
            f"  {kind}: names the right code {rate(c['hit'], c['n'])}; "
            f"names any {rate(c['any'], c['n'])}; "
            f"precision at 1:1 {precision(c['hit'], c['n'], control[expect[kind]], control['n'])}"
        )

    tally = run_omission(pages, rng, args.jitter)
    print(f"\nOMISSION (one block left out, boxes off by up to {args.jitter:.0f} pt)")
    for name, t in tally.items():
        prec = precision(t["found"], t["dropped"], t["false_alarms"], t["controls"])
        print(
            f"  {name}: finds the dropped block {rate(t['found'], t['dropped'])}; "
            f"false alarms {rate(t['false_alarms'], t['controls'])}; precision at 1:1 {prec}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
